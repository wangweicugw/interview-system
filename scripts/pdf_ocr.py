"""Extract text PDF pages and locally OCR pages without a useful text layer."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


def _ocr_image(image: Path) -> tuple[str, str, float | None]:
    preference = os.environ.get("INTERVIEW_OCR_ENGINE", "auto")
    if preference not in {"auto", "vision", "tesseract"}:
        raise RuntimeError("INTERVIEW_OCR_ENGINE 仅支持 auto、vision、tesseract")
    swift = shutil.which("swift") if sys.platform == "darwin" and preference != "tesseract" else None
    if swift:
        helper = Path(__file__).with_name("ocr_image.swift")
        cache = Path(tempfile.gettempdir()) / "interview-system-swift-cache"
        cache.mkdir(mode=0o700, exist_ok=True)
        env = os.environ.copy()
        env["SWIFT_MODULECACHE_PATH"] = str(cache)
        env["CLANG_MODULE_CACHE_PATH"] = str(cache)
        result = subprocess.run(
            [swift, "-module-cache-path", str(cache), str(helper), str(image)],
            text=True, capture_output=True, env=env, check=False, timeout=180,
        )
        if result.returncode == 0:
            payload = json.loads(result.stdout)
            lines = payload.get("lines", [])
            text = "\n".join(str(line["text"]) for line in lines).strip()
            confidence = min((float(line["confidence"]) for line in lines), default=None)
            return text, "apple_vision", confidence
        if preference == "vision":
            raise RuntimeError("Apple Vision OCR 在当前执行环境不可用")

    tesseract = shutil.which("tesseract")
    if not tesseract:
        raise RuntimeError("扫描 PDF 需要 macOS Vision 或已安装的 Tesseract")
    languages = subprocess.run(
        [tesseract, "--list-langs"], text=True, capture_output=True, check=False
    ).stdout.splitlines()
    selected = [lang for lang in ("chi_sim", "eng") if lang in languages]
    if not selected:
        raise RuntimeError("Tesseract 缺少 chi_sim/eng 语言包")
    if preference == "auto" and "chi_sim" not in selected:
        raise RuntimeError("自动 OCR 缺少中文语言包；英文扫描件可显式设置 INTERVIEW_OCR_ENGINE=tesseract")
    result = subprocess.run(
        [tesseract, str(image), "stdout", "-l", "+".join(selected), "tsv"],
        text=True, capture_output=True, check=False, timeout=120,
    )
    if result.returncode:
        raise RuntimeError("Tesseract OCR 失败")
    rows = [row.split("\t", 11) for row in result.stdout.splitlines()[1:]]
    words = [(row[11].strip(), float(row[10])) for row in rows if len(row) == 12 and row[11].strip() and float(row[10]) >= 0]
    text = " ".join(word for word, _ in words)
    confidence = min((value / 100 for _, value in words), default=None)
    return text, "tesseract:" + "+".join(selected), confidence


def extract_pdf(path: Path) -> tuple[str, list[dict[str, Any]]]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("读取 PDF 需要 pypdf；请安装 requirements.lock 中的依赖") from exc
    renderer = shutil.which("pdftoppm")
    reader = PdfReader(str(path))
    if not reader.pages:
        raise RuntimeError("PDF 没有页面")
    pages: list[str] = []
    evidence: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="interview-pdf-ocr-") as temporary:
        for number, page in enumerate(reader.pages, start=1):
            embedded = (page.extract_text() or "").strip()
            if len(embedded) >= 12:
                text, method, confidence = embedded, "text_layer", None
            else:
                if not renderer:
                    raise RuntimeError("扫描 PDF 需要 pdftoppm 渲染页面")
                prefix = Path(temporary) / f"page-{number:04d}"
                rendered = subprocess.run(
                    [renderer, "-q", "-f", str(number), "-l", str(number), "-singlefile",
                     "-r", "220", "-png", str(path), str(prefix)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=120,
                )
                image = prefix.with_suffix(".png")
                if rendered.returncode or not image.is_file():
                    raise RuntimeError(f"PDF 第 {number} 页渲染失败")
                text, method, confidence = _ocr_image(image)
                if not text:
                    raise RuntimeError(f"PDF 第 {number} 页 OCR 未识别到文字；请人工核对扫描件")
            pages.append(text)
            evidence.append({"page": number, "method": method, "confidence_floor": confidence})
    return "\n\n".join(pages), evidence
