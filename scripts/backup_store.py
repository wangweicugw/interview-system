#!/usr/bin/env python3
"""创建、校验并安全恢复本地面试数据备份。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

from check_data_compatibility import require_store_compatible
from common import now_iso, sha256_file


EXCLUDED_DIRS = {".git", ".venv", ".venv-whisperx", "models", "logs", "__pycache__"}
MANIFEST = "BACKUP-MANIFEST.json"
AGE_BINARY_HEADER = b"age-encryption.org/v1\n"
AGE_ARMORED_HEADER = b"-----BEGIN AGE ENCRYPTED FILE-----"


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _files(root: Path) -> list[Path]:
    result = []
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if any(part in EXCLUDED_DIRS for part in relative.parts):
            continue
        if path.is_symlink():
            raise RuntimeError(f"备份拒绝符号链接：{relative}")
        if path.is_file():
            result.append(path)
    return sorted(result)


def _create_zip(root: Path, output: Path, *, encrypted: bool, mode: str) -> int:
    files = _files(root)
    manifest = {
        "schema_version": 1,
        "created_at": now_iso(),
        "source_name": root.name,
        "encrypted": encrypted,
        "encryption": {"format": "age-v1", "mode": mode} if encrypted else None,
        "warning": (
            "备份由 age 加密；私钥或口令丢失时无法恢复。"
            if encrypted
            else "备份包含私人面试资料，ZIP 仅压缩不加密；请保存在受控本地位置。"
        ),
        "excluded_directories": sorted(EXCLUDED_DIRS),
        "files": [
            {
                "path": path.relative_to(root).as_posix(),
                "size": path.stat().st_size,
                "sha256": sha256_file(path),
            }
            for path in files
        ],
    }
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        for path in files:
            archive.write(path, path.relative_to(root).as_posix())
    output.chmod(0o600)
    result = _verify_zip(output)
    if result["status"] != "valid":
        raise RuntimeError("新建备份内部校验失败：" + "；".join(result["issues"]))
    return len(files)


def _age_binary(value: str) -> str:
    candidate = shutil.which(value) if os.sep not in value else value
    if not candidate or not Path(candidate).is_file():
        raise RuntimeError("未找到 age 可执行文件；不会回退生成明文备份")
    return str(Path(candidate).resolve())


def _run_age(arguments: list[str]) -> None:
    result = subprocess.run(arguments, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"age 执行失败（退出码 {result.returncode}）；未生成明文回退备份")


def _is_age_file(path: Path) -> bool:
    with path.open("rb") as handle:
        prefix = handle.read(max(len(AGE_BINARY_HEADER), len(AGE_ARMORED_HEADER)))
    return prefix.startswith(AGE_BINARY_HEADER) or prefix.startswith(AGE_ARMORED_HEADER)


def create(
    root: Path,
    output: Path,
    *,
    recipients: list[str] | None = None,
    passphrase: bool = False,
    allow_plaintext: bool = False,
    age_binary: str = "age",
    force: bool = False,
) -> dict[str, Any]:
    root = root.expanduser().resolve()
    require_store_compatible(root)
    output = output.expanduser().absolute()
    if output.is_symlink():
        raise RuntimeError("备份输出不能是符号链接")
    if _inside(output, root):
        raise RuntimeError("备份文件必须位于数据目录之外，避免递归打包")
    if output.exists() and not force:
        raise RuntimeError("备份输出已存在；确认覆盖后添加 --force")
    recipients = [value.strip() for value in (recipients or []) if value.strip()]
    selected = int(bool(recipients)) + int(passphrase) + int(allow_plaintext)
    if selected != 1:
        raise RuntimeError(
            "必须明确选择一种备份方式：--recipient、--passphrase 或 --allow-plaintext"
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".interview-backup-", dir=output.parent) as temporary:
        temporary_root = Path(temporary)
        plain_zip = temporary_root / "backup.zip"
        mode = "recipient" if recipients else "passphrase" if passphrase else "plaintext"
        file_count = _create_zip(root, plain_zip, encrypted=not allow_plaintext, mode=mode)
        if allow_plaintext:
            staged_output = plain_zip
        else:
            executable = _age_binary(age_binary)
            staged_output = temporary_root / "backup.age"
            command = [executable]
            if passphrase:
                command.append("--passphrase")
            else:
                for recipient in recipients:
                    command.extend(["--recipient", recipient])
            command.extend(["--output", str(staged_output), str(plain_zip)])
            _run_age(command)
            if not staged_output.is_file() or not _is_age_file(staged_output):
                raise RuntimeError("age 未生成可识别的加密文件；已拒绝交付")
        staged_output.chmod(0o600)
        os.replace(staged_output, output)

    return {
        "status": "created",
        "backup": str(output),
        "files": file_count,
        "bytes": output.stat().st_size,
        "sha256": sha256_file(output),
        "encrypted": not allow_plaintext,
        "encryption_mode": None if allow_plaintext else mode,
        "warning": (
            None
            if not allow_plaintext
            else "这是明文 ZIP；请勿上传到公共网盘、邮件或公开仓库"
        ),
    }


def _manifest(backup: Path) -> tuple[zipfile.ZipFile, dict[str, Any]]:
    archive = zipfile.ZipFile(backup)
    try:
        payload = json.loads(archive.read(MANIFEST).decode("utf-8"))
    except (KeyError, ValueError, UnicodeDecodeError) as error:
        archive.close()
        raise RuntimeError(f"备份清单无效：{error}") from error
    if not isinstance(payload, dict) or payload.get("schema_version") != 1 or not isinstance(payload.get("files"), list):
        archive.close()
        raise RuntimeError("备份清单格式不受支持")
    return archive, payload


def _safe_name(value: str) -> bool:
    path = PurePosixPath(value)
    return bool(value) and not path.is_absolute() and ".." not in path.parts and value != MANIFEST


def _verify_zip(backup: Path) -> dict[str, Any]:
    archive, manifest = _manifest(backup)
    issues = []
    expected = {str(item.get("path") or ""): item for item in manifest["files"] if isinstance(item, dict)}
    listed_names = archive.namelist()
    if len(listed_names) != len(set(listed_names)):
        issues.append("压缩包包含重复路径")
    names = set(listed_names) - {MANIFEST}
    if any(not _safe_name(name) for name in names):
        issues.append("压缩包包含不安全路径")
    if names != set(expected):
        issues.append("压缩包文件列表与清单不一致")
    for name, item in expected.items():
        if not _safe_name(name) or name not in names:
            continue
        info = archive.getinfo(name)
        if info.is_dir() or ((info.external_attr >> 16) & 0o170000) == 0o120000:
            issues.append(f"包含非普通文件：{name}")
            continue
        data = archive.read(name)
        if len(data) != item.get("size"):
            issues.append(f"大小不一致：{name}")
        if hashlib.sha256(data).hexdigest() != item.get("sha256"):
            issues.append(f"哈希不一致：{name}")
    archive.close()
    return {
        "status": "valid" if not issues else "invalid",
        "files": len(expected),
        "issues": issues,
        "encrypted": bool(manifest.get("encrypted")),
        "encryption": manifest.get("encryption"),
    }


def _decrypt_age(
    backup: Path,
    output: Path,
    *,
    identities: list[Path] | None,
    passphrase: bool,
    age_binary: str,
) -> None:
    identity_paths = [path.expanduser().absolute() for path in (identities or [])]
    if bool(identity_paths) == bool(passphrase):
        raise RuntimeError("加密备份校验/恢复必须明确选择 --identity 或 --passphrase")
    for path in identity_paths:
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"age 身份文件不可用：{path}")
    executable = _age_binary(age_binary)
    command = [executable, "--decrypt"]
    for path in identity_paths:
        command.extend(["--identity", str(path)])
    command.extend(["--output", str(output), str(backup)])
    _run_age(command)
    if not output.is_file() or not zipfile.is_zipfile(output):
        raise RuntimeError("age 解密结果不是可识别的 ZIP 备份")
    output.chmod(0o600)


@contextmanager
def _materialized_zip(
    backup: Path,
    *,
    identities: list[Path] | None = None,
    passphrase: bool = False,
    age_binary: str = "age",
) -> Iterator[tuple[Path, bool]]:
    backup = backup.expanduser().absolute()
    if not backup.is_file() or backup.is_symlink():
        raise RuntimeError("备份文件不存在或为符号链接")
    if zipfile.is_zipfile(backup):
        if identities or passphrase:
            raise RuntimeError("该备份是明文 ZIP，不应提供 age 解密参数")
        yield backup, False
        return
    if not _is_age_file(backup):
        raise RuntimeError("无法识别的备份格式")
    with tempfile.TemporaryDirectory(prefix=".interview-restore-") as temporary:
        plain_zip = Path(temporary) / "backup.zip"
        _decrypt_age(
            backup,
            plain_zip,
            identities=identities,
            passphrase=passphrase,
            age_binary=age_binary,
        )
        yield plain_zip, True


def verify(
    backup: Path,
    *,
    identities: list[Path] | None = None,
    passphrase: bool = False,
    age_binary: str = "age",
) -> dict[str, Any]:
    original = backup.expanduser().absolute()
    with _materialized_zip(
        original,
        identities=identities,
        passphrase=passphrase,
        age_binary=age_binary,
    ) as (plain_zip, outer_encrypted):
        result = _verify_zip(plain_zip)
    result.update({
        "backup": str(original),
        "encrypted": outer_encrypted,
        "sha256": sha256_file(original),
    })
    return result


def restore(
    backup: Path,
    target: Path,
    *,
    confirmed: bool,
    identities: list[Path] | None = None,
    passphrase: bool = False,
    age_binary: str = "age",
) -> dict[str, Any]:
    if not confirmed:
        raise RuntimeError("恢复前必须显式传入 --confirm-empty-target")
    target = target.expanduser().absolute()
    if target.is_symlink() or (
        target.exists() and (not target.is_dir() or any(target.iterdir()))
    ):
        raise RuntimeError("恢复目标必须不存在或为空；不会覆盖现有数据")
    original = backup.expanduser().absolute()
    with _materialized_zip(
        original,
        identities=identities,
        passphrase=passphrase,
        age_binary=age_binary,
    ) as (plain_zip, outer_encrypted):
        result = _verify_zip(plain_zip)
        if result["status"] != "valid":
            raise RuntimeError("备份校验失败，拒绝恢复：" + "；".join(result["issues"]))
        target.mkdir(parents=True, exist_ok=True, mode=0o700)
        archive, manifest = _manifest(plain_zip)
        try:
            for item in manifest["files"]:
                name = str(item["path"])
                if not _safe_name(name):
                    raise RuntimeError(f"不安全的备份路径：{name}")
                info = archive.getinfo(name)
                if info.is_dir() or ((info.external_attr >> 16) & 0o170000) == 0o120000:
                    raise RuntimeError(f"备份中包含非普通文件：{name}")
                destination = target / PurePosixPath(name)
                destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                destination.write_bytes(archive.read(name))
                destination.chmod(0o600)
        finally:
            archive.close()
    require_store_compatible(target)
    return {
        "status": "restored",
        "backup": str(original),
        "target": str(target),
        "files": result["files"],
        "encrypted": outer_encrypted,
    }


def add_decryption_arguments(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--identity", action="append", default=[], type=Path)
    group.add_argument(
        "--passphrase",
        action="store_true",
        help="由 age 在当前终端中交互读取口令",
    )
    parser.add_argument("--age-binary", default="age", help=argparse.SUPPRESS)


def main() -> int:
    parser = argparse.ArgumentParser(description="本地面试档案备份、校验与安全恢复")
    sub = parser.add_subparsers(dest="command", required=True)
    create_parser = sub.add_parser("create")
    create_parser.add_argument("--root", required=True, type=Path)
    create_parser.add_argument("--output", required=True, type=Path)
    encryption = create_parser.add_mutually_exclusive_group(required=True)
    encryption.add_argument("--recipient", action="append", default=[])
    encryption.add_argument(
        "--passphrase",
        action="store_true",
        help="由 age 在当前终端中交互读取口令",
    )
    encryption.add_argument(
        "--allow-plaintext",
        action="store_true",
        help="明确选择未加密 ZIP；只用于受控本地位置",
    )
    create_parser.add_argument("--force", action="store_true")
    create_parser.add_argument("--age-binary", default="age", help=argparse.SUPPRESS)
    verify_parser = sub.add_parser("verify")
    verify_parser.add_argument("--backup", required=True, type=Path)
    add_decryption_arguments(verify_parser)
    restore_parser = sub.add_parser("restore")
    restore_parser.add_argument("--backup", required=True, type=Path)
    restore_parser.add_argument("--target", required=True, type=Path)
    restore_parser.add_argument("--confirm-empty-target", action="store_true")
    add_decryption_arguments(restore_parser)
    args = parser.parse_args()
    if args.command == "create":
        result = create(
            args.root,
            args.output,
            recipients=args.recipient,
            passphrase=args.passphrase,
            allow_plaintext=args.allow_plaintext,
            age_binary=args.age_binary,
            force=args.force,
        )
    elif args.command == "verify":
        result = verify(
            args.backup,
            identities=args.identity,
            passphrase=args.passphrase,
            age_binary=args.age_binary,
        )
    else:
        result = restore(
            args.backup,
            args.target,
            confirmed=args.confirm_empty_target,
            identities=args.identity,
            passphrase=args.passphrase,
            age_binary=args.age_binary,
        )
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("status") not in {"invalid"} else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False))
        raise SystemExit(1)
