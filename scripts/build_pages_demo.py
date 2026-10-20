#!/usr/bin/env python3
"""Build a read-only GitHub Pages preview from the fictional demo fixture."""

from __future__ import annotations

import argparse
import json
import re
import tempfile
from pathlib import Path

from demo_smoke import run_demo


ROOT_PAGES = (
    "interview-history.html",
    "interview-question-bank.html",
    "interview-answers.html",
    "interview-pending-review.html",
)
STATIC_HEAD = (
    '<meta http-equiv="Content-Security-Policy" '
    'content="default-src \'self\' data:; script-src \'self\' \'unsafe-inline\'; '
    'style-src \'self\' \'unsafe-inline\'; connect-src \'none\'; '
    'form-action \'none\'; base-uri \'none\'">'
    '<style>.static-demo-banner{margin:0 0 1.25rem;padding:1rem 1.25rem;'
    'border:2px solid #a8c7ec;border-radius:12px;background:#edf5ff;'
    'color:#183b61;line-height:1.5}.static-demo-banner a{color:inherit;'
    'font-weight:700}button:disabled{cursor:not-allowed}</style>'
)
STATIC_BANNER = (
    '<aside class="static-demo-banner" role="note">'
    '<strong>虚构材料 · 只读演示</strong>　'
    '这里展示的是仓库自带的模拟面试。搜索、展开和切换题目可用；'
    '保存、归档和审核需要在本机服务中操作。'
    '想处理自己的材料，请按 '
    '<a href="https://github.com/wangweicugw/interview-system#quickstart交给-agent">Quickstart</a> '
    '交给 Agent。'
    '</aside>'
)
STATIC_SCRIPT = """<script>
(() => {
  const keep = '[data-home-previous], [data-home-next], [data-search-clear], '
    + '[data-ai-improve], [data-draft-clear]';
  document.querySelectorAll('button').forEach((button) => {
    if (button.matches(keep)) return;
    button.disabled = true;
    button.title = '只读演示：请在本机服务中保存';
  });
})();
</script>"""


def make_static(source: Path, destination: Path) -> None:
    page = source.read_text(encoding="utf-8")
    if "</head>" not in page or not re.search(r"<main(?:\s[^>]*)?>", page) or "</body>" not in page:
        raise ValueError(f"演示页面结构不完整：{source.name}")
    page = page.replace("本页含个人面试资料", "本页仅含虚构面试资料")
    page = page.replace("从一道真实题目开始", "从一道虚构题目开始")
    page = page.replace("真实面试题", "虚构面试题")
    page = page.replace("</head>", STATIC_HEAD + "</head>", 1)
    page = re.sub(r"(<main(?:\s[^>]*)?>)", lambda match: match.group(1) + STATIC_BANNER, page, count=1)
    page = page.replace("</body>", STATIC_SCRIPT + "</body>", 1)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(page, encoding="utf-8")


def build(output: Path) -> dict[str, object]:
    if output.is_symlink() or (output.exists() and (not output.is_dir() or any(output.iterdir()))):
        raise ValueError("输出目录必须不存在或为空，不能覆盖现有文件")
    output = output.expanduser().resolve()
    with tempfile.TemporaryDirectory(prefix="interview-pages-demo-") as temporary:
        root = Path(temporary) / "data"
        run_demo(root)
        cases = list((root / "interviews").glob("*/interview-review.html"))
        if len(cases) != 1:
            raise RuntimeError("虚构演示应恰好生成一场面试")
        for name in ROOT_PAGES:
            make_static(root / name, output / name)
        relative_case = cases[0].relative_to(root)
        make_static(cases[0], output / relative_case)
        make_static(root / "interview-history.html", output / "index.html")
    (output / ".nojekyll").write_text("", encoding="utf-8")
    return {"status": "passed", "scope": "fictional_static_only", "output": str(output), "pages": 6}


def main() -> int:
    parser = argparse.ArgumentParser(description="生成只含虚构资料的静态演示")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = build(args.output)
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
