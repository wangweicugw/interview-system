#!/usr/bin/env python3
"""Create, verify, and safely restore a local interview data backup."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from check_data_compatibility import require_store_compatible
from common import now_iso, sha256_file


EXCLUDED_DIRS = {".git", ".venv", ".venv-whisperx", "models", "logs", "__pycache__"}
MANIFEST = "BACKUP-MANIFEST.json"


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


def create(root: Path, output: Path) -> dict[str, Any]:
    require_store_compatible(root)
    output = output.expanduser().resolve()
    if _inside(output, root):
        raise RuntimeError("备份文件必须位于数据目录之外，避免递归打包")
    output.parent.mkdir(parents=True, exist_ok=True)
    files = _files(root)
    manifest = {
        "schema_version": 1,
        "created_at": now_iso(),
        "source_name": root.name,
        "encrypted": False,
        "warning": "备份包含私人面试资料，ZIP 仅压缩不加密；请保存在受控本地位置。",
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
    with tempfile.NamedTemporaryFile(dir=output.parent, suffix=".zip", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            archive.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
            for path in files:
                archive.write(path, path.relative_to(root).as_posix())
        os.chmod(temporary, 0o600)
        os.replace(temporary, output)
    finally:
        if temporary.exists():
            temporary.unlink()
    return {"status": "created", "backup": str(output), "files": len(files), "bytes": output.stat().st_size,
            "encrypted": False}


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


def verify(backup: Path) -> dict[str, Any]:
    backup = backup.expanduser().resolve()
    archive, manifest = _manifest(backup)
    issues = []
    expected = {str(item.get("path") or ""): item for item in manifest["files"] if isinstance(item, dict)}
    names = set(archive.namelist()) - {MANIFEST}
    if any(not _safe_name(name) for name in names):
        issues.append("压缩包包含不安全路径")
    if names != set(expected):
        issues.append("压缩包文件列表与清单不一致")
    for name, item in expected.items():
        if not _safe_name(name) or name not in names:
            continue
        data = archive.read(name)
        if len(data) != item.get("size"):
            issues.append(f"大小不一致：{name}")
        if hashlib.sha256(data).hexdigest() != item.get("sha256"):
            issues.append(f"哈希不一致：{name}")
    archive.close()
    return {"status": "valid" if not issues else "invalid", "backup": str(backup),
            "files": len(expected), "issues": issues, "encrypted": bool(manifest.get("encrypted"))}


def restore(backup: Path, target: Path, *, confirmed: bool) -> dict[str, Any]:
    if not confirmed:
        raise RuntimeError("恢复前必须显式传入 --confirm-empty-target")
    result = verify(backup)
    if result["status"] != "valid":
        raise RuntimeError("备份校验失败，拒绝恢复：" + "；".join(result["issues"]))
    target = target.expanduser().resolve()
    if target.exists() and any(target.iterdir()):
        raise RuntimeError("恢复目标必须不存在或为空；不会覆盖现有数据")
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    archive, manifest = _manifest(backup.expanduser().resolve())
    for item in manifest["files"]:
        name = str(item["path"])
        if not _safe_name(name):
            archive.close()
            raise RuntimeError(f"不安全的备份路径：{name}")
        destination = target / PurePosixPath(name)
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        destination.write_bytes(archive.read(name))
        destination.chmod(0o600)
    archive.close()
    require_store_compatible(target)
    return {"status": "restored", "backup": str(backup), "target": str(target), "files": len(manifest["files"])}


def main() -> int:
    parser = argparse.ArgumentParser(description="本地面试档案备份、校验与安全恢复")
    sub = parser.add_subparsers(dest="command", required=True)
    create_parser = sub.add_parser("create")
    create_parser.add_argument("--root", required=True, type=Path)
    create_parser.add_argument("--output", required=True, type=Path)
    verify_parser = sub.add_parser("verify")
    verify_parser.add_argument("--backup", required=True, type=Path)
    restore_parser = sub.add_parser("restore")
    restore_parser.add_argument("--backup", required=True, type=Path)
    restore_parser.add_argument("--target", required=True, type=Path)
    restore_parser.add_argument("--confirm-empty-target", action="store_true")
    args = parser.parse_args()
    if args.command == "create":
        result = create(args.root.expanduser().resolve(), args.output)
    elif args.command == "verify":
        result = verify(args.backup)
    else:
        result = restore(args.backup, args.target, confirmed=args.confirm_empty_target)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("status") not in {"invalid"} else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False))
        raise SystemExit(1)
