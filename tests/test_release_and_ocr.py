from __future__ import annotations

import os
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / "scripts"))

from build_release_package import build  # noqa: E402
from check_data_compatibility import scan_store  # noqa: E402
from check_public_release import audit  # noqa: E402
from common import SchemaCompatibilityError, write_json  # noqa: E402
from demo_smoke import run_demo  # noqa: E402
from pdf_ocr import _ocr_image  # noqa: E402


class ReleaseAndOcrTest(unittest.TestCase):
    def test_init_store_requires_an_explicit_absolute_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            command = [sys.executable, str(SKILL / "scripts" / "init_store.py")]
            missing = subprocess.run(command, cwd=temporary, text=True, capture_output=True, check=False)
            relative = subprocess.run(
                [*command, "--root", "private-store"],
                cwd=temporary, text=True, capture_output=True, check=False,
            )
            self.assertNotEqual(missing.returncode, 0)
            self.assertIn("--root", missing.stderr)
            self.assertNotEqual(relative.returncode, 0)
            self.assertIn("绝对路径", relative.stderr)
            self.assertFalse((Path(temporary) / "private-store").exists())

    def test_init_store_defaults_to_no_git_and_private_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-store"
            result = subprocess.run(
                [sys.executable, str(SKILL / "scripts" / "init_store.py"), "--root", str(root)],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["git"], "not_requested")
            self.assertFalse((root / ".git").exists())
            self.assertEqual(root.stat().st_mode & 0o777, 0o700)
            self.assertIn("*\n!.gitignore", (root / ".gitignore").read_text(encoding="utf-8"))

    def test_init_store_rejects_skill_overlap_and_remote_git_parent(self) -> None:
        overlap = subprocess.run(
            [sys.executable, str(SKILL / "scripts" / "init_store.py"),
             "--root", str(SKILL / "should-not-create-private-store")],
            text=True, capture_output=True, check=False,
        )
        self.assertNotEqual(overlap.returncode, 0)
        self.assertIn("不得位于 Skill 源码目录内", overlap.stderr)
        self.assertFalse((SKILL / "should-not-create-private-store").exists())

        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary) / "remote-repository"
            repository.mkdir()
            subprocess.run(["git", "-C", str(repository), "init"], check=True, capture_output=True)
            subprocess.run(
                ["git", "-C", str(repository), "remote", "add", "origin", "https://example.invalid/repo.git"],
                check=True, capture_output=True,
            )
            nested = repository / "private-store"
            blocked = subprocess.run(
                [sys.executable, str(SKILL / "scripts" / "init_store.py"), "--root", str(nested)],
                text=True, capture_output=True, check=False,
            )
            self.assertNotEqual(blocked.returncode, 0)
            self.assertIn("已配置远程的 Git 工作树", blocked.stderr)
            self.assertFalse((nested / "config.json").exists())

    def test_core_only_audit_allows_repair_of_corrupt_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "fictional-demo"
            result = run_demo(root)
            case = Path(result["case_html"]).parent
            (case / "qa-tree.json").write_text("{", encoding="utf-8")
            self.assertEqual(scan_store(root)["status"], "blocked")
            self.assertEqual(scan_store(root, include_optional=False)["status"], "compatible")

    def test_missing_schema_version_is_not_guessed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "config.json").write_text('{"language":"zh-CN"}', encoding="utf-8")
            result = scan_store(root)
            self.assertEqual(result["status"], "blocked")
            self.assertIn("缺少有效的整数 schema_version", result["issues"][0]["reason"])

    def test_future_case_schema_blocks_writes_and_rendering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "fictional-demo"
            result = run_demo(root)
            case = Path(result["case_html"]).parent
            manifest_path = case / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["schema_version"] = 2
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
            before = manifest_path.read_bytes()
            html_before = Path(result["case_html"]).read_bytes()

            audit = scan_store(root)
            self.assertEqual(audit["status"], "blocked")
            self.assertTrue(any(item["path"].endswith("manifest.json") for item in audit["issues"]))
            with self.assertRaises(SchemaCompatibilityError):
                write_json(manifest_path, {**manifest, "schema_version": 1})
            updated = subprocess.run(
                [sys.executable, str(SKILL / "scripts" / "update_case.py"),
                 "--case", str(case), "--result", "通过", "--reason", "测试"],
                text=True, capture_output=True, check=False,
            )
            self.assertNotEqual(updated.returncode, 0)
            rendered = subprocess.run(
                [sys.executable, str(SKILL / "scripts" / "render_interview_html.py"),
                 "--root", str(root), "--all"],
                text=True, capture_output=True, check=False,
            )
            self.assertNotEqual(rendered.returncode, 0)
            self.assertEqual(manifest_path.read_bytes(), before)
            self.assertEqual(Path(result["case_html"]).read_bytes(), html_before)

    def test_demo_smoke_generates_report_without_overwriting_existing_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "fictional-demo"
            result = run_demo(root)
            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["scope"], "synthetic_text_only")
            self.assertIn("示例岗位要求", Path(result["case_html"]).read_text(encoding="utf-8"))
            self.assertTrue(Path(result["history_html"]).is_file())
            with self.assertRaisesRegex(ValueError, "必须不存在或为空"):
                run_demo(root)

    def test_auto_ocr_requires_chinese_tesseract_when_vision_unavailable(self) -> None:
        listed = mock.Mock(returncode=0, stdout="List of available languages (1):\neng\n")
        with mock.patch.dict(os.environ, {"INTERVIEW_OCR_ENGINE": "auto"}), \
                mock.patch("pdf_ocr.sys.platform", "linux"), \
                mock.patch("pdf_ocr.shutil.which", return_value="/usr/bin/tesseract"), \
                mock.patch("pdf_ocr.subprocess.run", return_value=listed) as run:
            with self.assertRaisesRegex(RuntimeError, "缺少中文语言包"):
                _ocr_image(Path("unused.png"))
        self.assertEqual(run.call_count, 1)

    def test_release_audit_reports_private_content_without_secret_value(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            (source / "SKILL.md").write_text("---\nname: test\n---\n", encoding="utf-8")
            (source / "README.md").write_text("Example", encoding="utf-8")
            (source / "LICENSE").write_text("Example", encoding="utf-8")
            (source / "scripts").mkdir()
            secret = "ghp_" + "A" * 24
            (source / "scripts" / "helper.py").write_text(f"TOKEN = '{secret}'\n", encoding="utf-8")
            (source / "private").mkdir()
            result = audit(source, privacy_only=True)
            self.assertEqual(result["status"], "blocked")
            self.assertTrue(any("疑似密钥" in item for item in result["blockers"]))
            self.assertTrue(any("本地数据目录" in item for item in result["blockers"]))
            self.assertNotIn(secret, str(result))

    def test_release_audit_distinguishes_staged_and_history(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "skill"
            (source / "scripts").mkdir(parents=True)
            (source / "SKILL.md").write_text("skill\n", encoding="utf-8")
            (source / "README.md").write_text("readme\n", encoding="utf-8")
            (source / "LICENSE").write_text("license\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(source), "init"], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(source), "config", "user.name", "Fictional Tester"], check=True)
            subprocess.run(["git", "-C", str(source), "config", "user.email", "tester@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(source), "add", "."], check=True)
            subprocess.run(["git", "-C", str(source), "commit", "-m", "base"], check=True, capture_output=True)

            staged_secret = "github_pat_" + "S" * 24
            helper = source / "scripts" / "helper.py"
            helper.write_text(f"TOKEN = '{staged_secret}'\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(source), "add", "scripts/helper.py"], check=True)
            staged = audit(source, privacy_only=True, scopes=("staged",))
            self.assertEqual(staged["status"], "blocked")
            self.assertTrue(any(item.startswith("[staged]") for item in staged["blockers"]))
            self.assertNotIn(staged_secret, str(staged))

            subprocess.run(["git", "-C", str(source), "commit", "-m", "leak fixture"], check=True, capture_output=True)
            helper.write_text("TOKEN = None\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(source), "add", "scripts/helper.py"], check=True)
            subprocess.run(["git", "-C", str(source), "commit", "-m", "remove fixture"], check=True, capture_output=True)
            history = audit(source, privacy_only=True, scopes=("history",))
            self.assertEqual(history["status"], "blocked")
            self.assertTrue(any(item.startswith("[history]") for item in history["blockers"]))
            self.assertNotIn(staged_secret, str(history))

    def test_release_audit_requires_license_only_in_release_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            (source / "SKILL.md").write_text("skill", encoding="utf-8")
            (source / "README.md").write_text("readme", encoding="utf-8")
            self.assertEqual(audit(source, privacy_only=True)["status"], "ready")
            self.assertTrue(any("许可证" in item for item in audit(source)["blockers"]))

    def test_release_package_rejects_symlinked_private_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "skill"
            output = Path(temporary) / "package.zip"
            for directory in ("agents", "references", "scripts", "assets"):
                (source / directory).mkdir(parents=True)
            for name in (
                "SKILL.md", "README.md", "LICENSE", "CONTRIBUTING.md", "SECURITY.md",
                "requirements.lock", "requirements-optional.txt",
            ):
                (source / name).write_text("public", encoding="utf-8")
            private = Path(temporary) / "private.txt"
            private.write_text("private interview content", encoding="utf-8")
            (source / "references" / "linked.md").symlink_to(private)
            with self.assertRaisesRegex(ValueError, "符号链接"):
                build(source, output)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
