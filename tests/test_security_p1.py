from __future__ import annotations

import json
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL / "scripts"
sys.path.insert(0, str(SCRIPTS))

from backup_store import create, restore, verify  # noqa: E402
from build_release_package import build  # noqa: E402
from demo_smoke import run_demo  # noqa: E402
from privacy_check import check as privacy_check  # noqa: E402


class SecurityP1Test(unittest.TestCase):
    def _store(self, parent: Path) -> Path:
        root = parent / "private-store"
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "init_store.py"), "--root", str(root)],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return root

    def _fake_age(self, parent: Path, *, fail: bool = False) -> Path:
        executable = parent / ("fake-age-fail" if fail else "fake-age")
        body = """#!/usr/bin/env python3
import pathlib
import sys

if %s:
    raise SystemExit(19)
args = sys.argv[1:]
output = pathlib.Path(args[args.index('--output') + 1])
source = pathlib.Path(args[-1])
header = b'age-encryption.org/v1\\n'
data = source.read_bytes()
if '--decrypt' in args:
    if not data.startswith(header):
        raise SystemExit(20)
    output.write_bytes(data[len(header):][::-1])
else:
    output.write_bytes(header + data[::-1])
""" % ("True" if fail else "False")
        executable.write_text(body, encoding="utf-8")
        executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
        return executable

    def test_backup_requires_explicit_mode_and_never_falls_back(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = self._store(parent)
            output = parent / "backup.age"
            with self.assertRaisesRegex(RuntimeError, "必须明确选择"):
                create(root, output)
            with self.assertRaisesRegex(RuntimeError, "不会回退生成明文"):
                create(root, output, recipients=["age1example"], age_binary="missing-age")
            self.assertFalse(output.exists())
            with self.assertRaisesRegex(RuntimeError, "未生成明文回退备份"):
                create(
                    root,
                    output,
                    recipients=["age1example"],
                    age_binary=str(self._fake_age(parent, fail=True)),
                )
            self.assertFalse(output.exists())

            symlink = parent / "backup-link.age"
            symlink.symlink_to(parent / "symlink-target.age")
            with self.assertRaisesRegex(RuntimeError, "符号链接"):
                create(
                    root,
                    symlink,
                    recipients=["age1example"],
                    age_binary=str(self._fake_age(parent)),
                )

    def test_recipient_encrypted_backup_verify_and_restore(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = self._store(parent)
            fake_age = self._fake_age(parent)
            identity = parent / "identity.txt"
            identity.write_text("AGE-SECRET-KEY-TEST", encoding="utf-8")
            output = parent / "backup.age"

            created = create(
                root,
                output,
                recipients=["age1example"],
                age_binary=str(fake_age),
            )
            self.assertTrue(created["encrypted"])
            self.assertFalse(zipfile.is_zipfile(output))
            checked = verify(output, identities=[identity], age_binary=str(fake_age))
            self.assertEqual(checked["status"], "valid")
            self.assertTrue(checked["encrypted"])
            restored = parent / "restored"
            result = restore(
                output,
                restored,
                confirmed=True,
                identities=[identity],
                age_binary=str(fake_age),
            )
            self.assertTrue(result["encrypted"])
            self.assertEqual(
                json.loads((restored / "config.json").read_text(encoding="utf-8"))["schema_version"],
                1,
            )

    def test_release_rejects_unknown_resource_without_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            source = parent / "skill"
            shutil.copytree(
                SKILL,
                source,
                ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__", "tests"),
            )
            (source / "scripts" / "unexpected.py").write_text("print('unexpected')\n", encoding="utf-8")
            output = parent / "release.zip"
            with self.assertRaisesRegex(ValueError, "未列入清单"):
                build(source, output)
            self.assertFalse(output.exists())

    def test_privacy_check_detects_remote_permissions_and_plaintext_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            repository = parent / "repository"
            repository.mkdir()
            subprocess.run(["git", "-C", str(repository), "init"], check=True, capture_output=True)
            subprocess.run(
                ["git", "-C", str(repository), "remote", "add", "origin", "https://example.invalid/private.git"],
                check=True,
                capture_output=True,
            )
            root = repository / "private-store"
            root.mkdir(mode=0o700)
            sensitive = root / "resume.txt"
            secret_marker = "FICTIONAL-PRIVATE-CONTENT-MUST-NOT-BE-PRINTED"
            sensitive.write_text(secret_marker, encoding="utf-8")
            sensitive.chmod(0o644)
            backup = parent / "legacy-backup.zip"
            with zipfile.ZipFile(backup, "w") as archive:
                archive.writestr("resume.txt", secret_marker)

            result = privacy_check(root, skill_dir=SKILL, backup_paths=[backup])
            codes = {item["code"] for item in result["issues"]}
            self.assertEqual(result["status"], "blocked")
            self.assertTrue(result["read_only"])
            self.assertIn("data-git-remote", codes)
            self.assertIn("broad-permissions", codes)
            self.assertIn("plaintext-backup", codes)
            self.assertNotIn(secret_marker, json.dumps(result, ensure_ascii=False))

    def test_privacy_check_detects_source_overlap_without_modifying_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "skill"
            source.mkdir(mode=0o700)
            (source / "SKILL.md").write_text("fictional skill\n", encoding="utf-8")
            (source / "SKILL.md").chmod(0o600)
            private = source / "private-store"
            private.mkdir(mode=0o700)
            marker = private / "marker.txt"
            marker.write_text("fictional private marker", encoding="utf-8")
            marker.chmod(0o600)
            before = marker.read_bytes()

            result = privacy_check(private, skill_dir=source)
            self.assertEqual(result["status"], "blocked")
            self.assertTrue(any(item["code"] == "source-data-overlap" for item in result["issues"]))
            self.assertEqual(marker.read_bytes(), before)

    def test_new_demo_has_no_high_risk_private_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "fictional-demo"
            run_demo(root)
            result = privacy_check(root, skill_dir=SKILL)
            self.assertEqual(result["counts"]["critical"], 0)
            self.assertEqual(result["counts"]["high"], 0)


if __name__ == "__main__":
    unittest.main()
