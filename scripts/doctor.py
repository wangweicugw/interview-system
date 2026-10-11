#!/usr/bin/env python3
"""Read-only local capability check; never opens private interview data."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys


def module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def tesseract_languages() -> list[str]:
    command = shutil.which("tesseract")
    if not command:
        return []
    try:
        result = subprocess.run(
            [command, "--list-langs"],
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode:
        return []
    return sorted({line.strip() for line in result.stdout.splitlines() if line.strip() in {"chi_sim", "eng"}})


def check() -> dict[str, object]:
    languages = tesseract_languages()
    renderer = bool(shutil.which("pdftoppm"))
    vision_candidate = sys.platform == "darwin" and bool(shutil.which("swift"))
    return {
        "python": {
            "version": ".".join(map(str, sys.version_info[:3])),
            "supported": sys.version_info >= (3, 10),
        },
        "platform": sys.platform,
        "pdf": {
            "text_layer": module_available("pypdf"),
            "renderer": renderer,
            "apple_vision_candidate": vision_candidate,
            "tesseract_languages": languages,
            "scanned_chinese_candidate": renderer and (vision_candidate or "chi_sim" in languages),
        },
        "audio": {
            "ffprobe": bool(shutil.which("ffprobe")),
            "mlx_whisper_engine": module_available("mlx_whisper"),
            "whisperx_engine": module_available("whisperx") or bool(shutil.which("whisperx")),
        },
        "limits": [
            "引擎存在不代表模型已缓存或受限模型条款已接受。",
            "Apple Vision 候选状态不代表当前宿主已通过实际 OCR 测试。",
            "检查不读取录音、逐字稿、令牌或浏览器登录态。",
        ],
    }


if __name__ == "__main__":
    print(json.dumps(check(), ensure_ascii=False))
