"""真实浏览器验收：虚构 JD -> 准备页 -> 练习保存 -> 服务重启 -> 回看。"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

try:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright
except ImportError:  # CI 安装 requirements-e2e；普通 Python 回归不强制浏览器依赖。
    PlaywrightTimeoutError = RuntimeError
    sync_playwright = None

SKILL_ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str]) -> None:
    subprocess.run(command, cwd=SKILL_ROOT, check=True, capture_output=True, text=True)


def start_server(root: Path) -> tuple[subprocess.Popen[str], str]:
    process = subprocess.Popen(
        [sys.executable, "scripts/local_answer_server.py", "--root", str(root)],
        cwd=SKILL_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    line = process.stdout.readline().strip()
    if not line:
        error = process.stderr.read() if process.stderr else ""
        raise RuntimeError(f"本机服务未启动：{error}")
    result = json.loads(line)
    return process, str(result["url"])


def stop_server(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        process.terminate()
        process.wait(timeout=10)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="interview-browser-e2e-") as directory:
        root = Path(directory) / "store"
        run([sys.executable, "scripts/demo_smoke.py", "--root", str(root)])
        prep = {
            "schema_version": 1,
            "status": "pending",
            "target": {"company": "虚构目标公司", "role": "后端工程师", "summary": "虚构准备稿。"},
            "jd": {"text": "虚构 JD：熟悉缓存一致性与失败恢复。", "source_type": "用户提供文本", "source_note": "仅用于 E2E。"},
            "company_sources": [],
            "questions": [{
                "question_id": "target-q-001", "source_type": "延伸练习题", "priority": "P0",
                "question": "如何处理缓存更新失败？", "short_30s": "先说明边界，再说明恢复策略。",
                "standard_2m": "虚构的两分钟准备稿。", "risks": [], "followups": [],
                "sources": [], "evidence_refs": [],
            }],
            "reverse_questions": [],
        }
        prep_file = root.parent / "prep.json"
        prep_file.write_text(json.dumps(prep, ensure_ascii=False), encoding="utf-8")
        run([sys.executable, "scripts/upsert_target_interview_prep.py", "--root", str(root),
             "--slug", "fictional", "--input", str(prep_file), "--render"])
        if sync_playwright is None:
            print("browser E2E preparation passed; install requirements-e2e.txt for browser steps", file=sys.stderr)
            return 2

        process, open_url = start_server(root)
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page()
                page.goto(open_url)
                page.get_by_role("link", name="公司面试准备", exact=True).click()
                page.get_by_text("打开该公司准备").click()
                if not page.get_by_text("虚构 JD：熟悉缓存一致性与失败恢复。").is_visible():
                    raise AssertionError("准备页未展示虚构 JD")

                page.goto(page.url.rsplit("/", 1)[0] + "/interview-history.html")
                widget = page.locator("[data-practice-widget]").first
                widget.locator("details.practice-recorder summary").click()
                widget.locator("[data-practice-answer]").fill("虚构练习回答：先定义一致性边界，再说明失败恢复。")
                widget.locator("[data-practice-rating]").select_option("4")
                widget.locator("[data-timer-start]").click()
                page.wait_for_timeout(1100)
                widget.locator("[data-timer-stop]").click()
                widget.locator("[data-practice-save]").click()
                if not widget.locator("[data-practice-result]").filter(has_text="已入库").is_visible():
                    raise AssertionError("浏览器页面未显示练习已入库")
                browser.close()
        finally:
            stop_server(process)

        process, open_url = start_server(root)
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page()
                page.goto(open_url)
                page.get_by_text("练习").first.click()
                if not page.get_by_text("先把这题答完整").is_visible():
                    raise AssertionError("服务重启后未回到练习入口")
                if not page.get_by_text("虚构练习回答：先定义一致性边界，再说明失败恢复。").is_visible():
                    raise AssertionError("服务重启后页面未回看练习正文")
                attempts = list((root / "practice" / "private").glob("practice-*.txt"))
                if len(attempts) != 1 or "虚构练习回答" not in attempts[0].read_text(encoding="utf-8"):
                    raise AssertionError("服务重启后练习记录未持久化")
                browser.close()
        finally:
            stop_server(process)
    print("browser E2E passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, OSError, PlaywrightTimeoutError, RuntimeError) as error:
        print(f"browser E2E failed: {error}", file=sys.stderr)
        raise SystemExit(1)
