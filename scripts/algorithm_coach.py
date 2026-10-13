"""Offline, traceable algorithm coaching integrated with interview review."""

from __future__ import annotations

import argparse
import json
import shutil
import uuid
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any

from algorithm_progress import (
    add_mistake, due_problem_ids, ensure_problem, link_source, load_progress, record_attempt,
    record_hint, record_reveal, record_stage, save_progress, start_session,
)
from algorithm_history import scan_history
from algorithm_oral import review_oral
from algorithm_reports import build_week_plan, export_report
from check_data_compatibility import require_store_compatible
from coach_runner import IMAGES, docker_available, run_case
from common import append_private_text, now_iso, read_json, sha256_file, write_json, write_private_text
from java_review import classify_java_errors, inspect_java
from profile_store import append_event, content_hash, latest_events, load_revisions, rebuild_profile


BANK = Path(__file__).resolve().parents[1] / "assets" / "algorithm-problems.json"
STARTER = """def solve(data):
    # data 是题目描述中的 JSON 对象；返回可序列化为 JSON 的结果。
    raise NotImplementedError("请实现 solve")
"""
JAVA_GENERIC_STARTER = """import java.util.Map;

public class Solution {
    public static Object solve(Map<String, Object> data) {
        throw new UnsupportedOperationException("请实现 solve");
    }
}
"""
NEXT_ACTIONS = {
    "understanding": ("ask_approach", "请先脱稿复述题意、输入输出和边界。"),
    "independent_thinking": ("verify_current_solution", "请先完整说明你当前的方案，并用样例和边界检查正确性；暂不讨论优化。"),
    "optimization": ("explain_declared_strategy", "请基于你刚才主动提出的方案，说明实现步骤、正确性、复杂度和边界。"),
    "independent_coding": ("request_java_code", "请独立写 Java 代码，不先看答案。"),
    "code_review": ("fix_first_reported_issue", "先修复第一处已确认问题，再重新运行。"),
    "interviewer_followup": ("ask_interviewer_followup", "请回答一个复杂度或边界追问。"),
    "completed": ("review_scheduled", "本轮完成，按复习日期再次练习。"),
}
FIRST_WEEK = {1: "leetcode-1", 2: "leetcode-217", 3: "leetcode-283", 4: "leetcode-26", 5: "leetcode-125"}
EARLY_SPOILER_TERMS = (
    "哈希表", "哈希集合", "hashmap", "hashset", "动态规划", "滑动窗口", "双指针",
    "二分查找", "拓扑排序", "并查集", "前缀和", "广度优先", "深度优先", "bfs", "dfs",
    "栈", "队列", "stack", "queue", "复杂度目标", "o(n)", "o(log",
)
L1_PROMPT = "请先用一个正常样例和两个边界样例走完你当前的方案，并指出它为什么正确或在哪一步可能失败。"


def problems() -> list[dict[str, Any]]:
    return read_json(BANK)


def get_problem(query: str) -> dict[str, Any]:
    normalized = query.strip().lower()
    for problem in problems():
        aliases = {problem["id"].lower(), str(problem.get("leetcode_id", "")).lower(), problem.get("slug", "").lower()}
        if normalized in aliases:
            return problem
    raise RuntimeError(f"未知题目：{query}")


def validate_early_material(problem: dict[str, Any]) -> None:
    """防止题库维护者把解法标签混入独立思考阶段可见的题面。"""
    fields = [str(problem.get("title") or ""), str(problem.get("statement") or "")]
    fields.extend(str(value) for value in problem.get("constraints", []) if isinstance(value, str))
    visible = " ".join(fields).lower()
    leaks = sorted({term for term in EARLY_SPOILER_TERMS if term.lower() in visible})
    if leaks:
        raise RuntimeError("题库早期材料包含解法泄露词：" + "、".join(leaks))


def public_progress_item(item: dict[str, Any]) -> dict[str, Any]:
    """隐藏内部主题标签，避免 resume/progress 在独立思考阶段泄露方向。"""
    return {key: value for key, value in item.items() if key not in {"topic", "topic_key"}}


def recommend(root: Path) -> dict[str, Any]:
    bank = problems()
    progress = load_progress(root)
    pending = {
        event.get("key", "").removeprefix("algorithm/")
        for event in latest_events(load_revisions(root)).values()
        if event.get("type") == "algorithm_weakness" and event.get("status") == "pending"
    }
    chosen = sorted(bank, key=lambda item: (
        item["id"] not in pending,
        progress["problems"].get(item["id"], {}).get("attempt_count", 0),
        item.get("week_day", 99), item["id"],
    ))[0]
    return {
        "status": "recommended", "problem_id": chosen["id"], "title": chosen["title"],
        "reason": "长期档案中仍待复盘" if chosen["id"] in pending else "练习次数较少",
    }


def session_dir(root: Path, session_id: str) -> Path:
    if not session_id.startswith("alg-") or not session_id[4:].isalnum():
        raise RuntimeError("session_id 格式无效")
    directory = (root / "algorithms" / "sessions" / session_id).resolve()
    if not directory.is_dir():
        raise RuntimeError(f"找不到练习会话：{session_id}")
    return directory


def load_session(root: Path, session_id: str) -> tuple[Path, dict[str, Any]]:
    directory = session_dir(root, session_id)
    return directory, read_json(directory / "session.json")


def attempts(directory: Path) -> list[dict[str, Any]]:
    path = directory / "attempts.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_attempt(directory: Path, attempt: dict[str, Any]) -> None:
    append_private_text(
        directory / "attempts.jsonl",
        json.dumps(attempt, ensure_ascii=False) + "\n",
    )


def java_starter(problem: dict[str, Any]) -> str:
    spec = problem.get("java")
    if not spec:
        return JAVA_GENERIC_STARTER
    return "public class Solution {\n    public %s {\n        throw new UnsupportedOperationException(\"请实现\");\n    }\n}\n" % spec["signature"]


def start(root: Path, problem_query: str, language: str = "python") -> dict[str, Any]:
    problem = get_problem(problem_query)
    validate_early_material(problem)
    session_id = "alg-" + uuid.uuid4().hex[:12]
    directory = root / "algorithms" / "sessions" / session_id
    directory.mkdir(parents=True, exist_ok=False)
    work = root / "algorithms" / "private" / session_id
    work.mkdir(parents=True, exist_ok=False)
    work.chmod(0o700)
    solution = work / ("solution.py" if language == "python" else "Solution.java")
    write_private_text(solution, STARTER if language == "python" else java_starter(problem))
    session = {
        "schema_version": 1, "session_id": session_id, "problem_id": problem["id"],
        "topic": problem["topic"], "created_at": now_iso(), "hint_level": 0,
        "language": language, "solution": str(solution.relative_to(root)),
    }
    write_json(directory / "session.json", session)
    progress = load_progress(root)
    start_session(progress, problem, session_id, language)
    save_progress(root, progress)
    return {
        "status": "started", "session_id": session_id, "problem_id": problem["id"],
        "problem": problem["title"], "statement": problem["statement"],
        "difficulty": problem["difficulty"], "constraints": problem.get("constraints", []),
        "public_tests": [item for item in problem["tests"] if item["public"]],
        "solution": str(solution), "language": language, "stage": "understanding",
        "hint_level": 0, "next_action": "ask_approach", "prompt": NEXT_ACTIONS["understanding"][1],
    }


def hint(root: Path, session_id: str) -> dict[str, Any]:
    directory, session = load_session(root, session_id)
    problem = get_problem(session["problem_id"])
    level = session.get("hint_level", 0)
    hints = problem.get("hints", [])
    if level >= 3:
        return {"status": "needs_reveal_confirmation", "session_id": session_id,
                "message": "L1-L3 已用完；完整答案需执行 reveal --confirm。"}
    if level == 0:
        text = L1_PROMPT
    else:
        value = hints[0] if level == 1 else hints[-1]
        text = value["text"] if isinstance(value, dict) else value
    session["hint_level"] = level + 1
    write_json(directory / "session.json", session)
    progress = load_progress(root)
    record_hint(progress, problem, level + 1)
    save_progress(root, progress)
    return {"status": "hint", "session_id": session_id, "level": level + 1,
            "text": text, "next_action": "answer_current_hint"}


def reveal(root: Path, session_id: str, confirmed: bool) -> dict[str, Any]:
    if not confirmed:
        raise RuntimeError("展示完整答案会结束引导模式；确认后添加 --confirm")
    directory, session = load_session(root, session_id)
    problem = get_problem(session["problem_id"])
    reference = problem.get("reference_solution", {}).get(session.get("language", "java"))
    if not reference:
        raise RuntimeError("该题暂未内置此语言的参考答案")
    session["hint_level"] = 4
    session["answer_revealed"] = True
    write_json(directory / "session.json", session)
    progress = load_progress(root)
    record_reveal(progress, problem)
    save_progress(root, progress)
    return {"status": "revealed", "session_id": session_id, "level": 4,
            "assisted": True, "reference_solution": reference}


def record_learning(root: Path, session_id: str, stage: str, concepts: list[str],
                    mistakes: list[str]) -> dict[str, Any]:
    _, session = load_session(root, session_id)
    problem = get_problem(session["problem_id"])
    progress = load_progress(root)
    item = ensure_problem(progress, problem)
    stated_concepts = [str(value).strip().lower() for value in (item.get("concepts", []) + concepts) if str(value).strip()]
    if stage == "optimization" and not stated_concepts:
        raise RuntimeError("进入优化讨论前，必须先记录用户主动提出的优化方向；不得由系统代填")
    action, prompt = NEXT_ACTIONS[stage]
    if problem["id"] == "leetcode-1" and stage == "optimization" and any(
        term in " ".join(stated_concepts) for term in ("hash", "哈希", "map", "映射")
    ):
        action, prompt = "ask_key_value_and_lookup_insert_order", "请明确哈希表的 key/value，以及查询与插入的先后顺序。"
    item = record_stage(progress, problem, stage, concepts, mistakes, action)
    save_progress(root, progress)
    return {"status": "recorded", "session_id": session_id, "stage": stage,
            "mastery": item["mastery"], "next_action": action, "prompt": prompt}


def inspect_submission(root: Path, session_id: str, solution_arg: Path | None) -> dict[str, Any]:
    _, session = load_session(root, session_id)
    if session.get("language") != "java":
        raise RuntimeError("inspect-java 仅检查 Java 会话")
    solution = solution_arg.expanduser().resolve() if solution_arg else (root / session["solution"]).resolve()
    if not solution.is_file() or solution.suffix != ".java":
        raise RuntimeError("请提供存在的 Java 解答文件")
    findings = inspect_java(solution, session["problem_id"])
    progress = load_progress(root)
    item = ensure_problem(progress, get_problem(session["problem_id"]))
    for finding in findings:
        add_mistake(item, finding["category"], "static_inspection", None)
    if findings:
        item["stage"] = "code_review"
        if progress.get("current_session"):
            progress["current_session"]["stage"] = "code_review"
            progress["current_session"]["pending_action"] = "fix_first_reported_issue"
        save_progress(root, progress)
    return {"status": "inspected", "session_id": session_id, "actual_tested": False,
            "findings": findings, "claim": "仅静态检查；未编译、未运行、不能判定通过"}


def record_profile(root: Path, session: dict[str, Any], attempt: dict[str, Any], item: dict[str, Any]) -> None:
    problem_id = session["problem_id"]
    event_id = f"algorithm:{problem_id}"
    previous = latest_events(load_revisions(root)).get(event_id)
    passed = attempt["passed"] == attempt["total"]
    event = {
        "event_id": event_id, "revision": previous["revision"] + 1 if previous else 1,
        "recorded_at": now_iso(), "case_id": session["session_id"], "change_id": attempt["attempt_id"],
        "type": "algorithm_weakness", "key": f"algorithm/{problem_id}", "status": "pending",
        "evidence": [attempt["attempt_id"]],
        "reason": "用例通过，复杂度与表达仍待人工核查" if passed else "存在未通过的算法用例",
        "value": {
            "topic": session["topic"], "passed": attempt["passed"], "total": attempt["total"],
            "explanation": attempt["explanation"], "mastery": item["mastery"],
            "next_review_date": item["next_review_date"], "source_links": item["source_links"],
        },
    }
    event["content_hash"] = content_hash(event)
    append_event(root, event)
    rebuild_profile(root)


def submit(root: Path, session_id: str, solution_arg: Path | None, explanation: str,
           runner: str = "docker", trust_solution: bool = False, image: str | None = None,
           practiced_on: date | None = None) -> dict[str, Any]:
    directory, session = load_session(root, session_id)
    problem = get_problem(session["problem_id"])
    solution = solution_arg.expanduser().resolve() if solution_arg else (root / session["solution"]).resolve()
    language = session.get("language", "python")
    suffix = ".py" if language == "python" else ".java"
    if not solution.is_file() or solution.suffix != suffix:
        raise RuntimeError(f"请提供存在的 {language} 解答文件")
    if solution.stat().st_size > 131072:
        raise RuntimeError("解答文件超过 128 KiB 限制")
    if runner == "local" and not trust_solution:
        raise RuntimeError("本机执行需要明确添加 --trust-solution；仅可用于已审阅的可信代码")
    if runner == "docker":
        docker_available(image or IMAGES[language])
    if runner == "local" and language == "java" and (not shutil.which("javac") or not shutil.which("java")):
        raise RuntimeError("本机 Java 执行需要 JDK（javac 和 java）")
    snapshot = root / "algorithms" / "private" / session_id / f"attempt-{len(attempts(directory))+1:03d}{suffix}"
    shutil.copy2(solution, snapshot)
    static_findings = inspect_java(snapshot, problem["id"]) if language == "java" else []
    results = []
    for index, test in enumerate(problem["tests"], start=1):
        outcome = run_case(snapshot, test["input"], language, runner, image, problem.get("java"))
        actual = outcome.get("result")
        if problem.get("unordered_result") and isinstance(actual, list):
            passed = "error" not in outcome and sorted(actual) == sorted(test["expected"])
        else:
            passed = "error" not in outcome and actual == test["expected"]
        row = {"test": index, "public": test["public"], "passed": passed, "elapsed_ms": outcome["elapsed_ms"]}
        if test["public"] or not passed:
            row.update({"input": test["input"], "expected": test["expected"], "actual": outcome.get("result")})
            if "error" in outcome:
                row["error"] = outcome["error"]
        results.append(row)
    attempt = {
        "attempt_id": f"{session_id}-a{len(attempts(directory))+1:03d}", "created_at": now_iso(),
        "source_sha256": sha256_file(snapshot), "source": str(snapshot.relative_to(root)),
        "passed": sum(item["passed"] for item in results), "total": len(results), "results": results,
        "actual_tested": True, "static_findings": static_findings,
        "execution_findings": classify_java_errors(
            [row["error"] for row in results if "error" in row]
        ) if language == "java" else [],
        "explanation": explanation.strip(),
        "hint_level": session.get("hint_level", 0), "answer_revealed": session.get("answer_revealed", False),
        "complexity_target": problem["target_complexity"], "review_points": problem["review_points"],
        "human_review_status": "pending", "language": language, "runner": runner,
    }
    write_attempt(directory, attempt)
    categories = [item["category"] for item in static_findings + attempt["execution_findings"]]
    if attempt["passed"] < attempt["total"]:
        categories.append("compile_or_test_failure" if any("error" in row for row in results) else "wrong_answer")
    progress = load_progress(root)
    item = record_attempt(progress, problem, attempt, categories, practiced_on)
    save_progress(root, progress)
    record_profile(root, session, attempt, item)
    write_json(directory / "latest-report.json", attempt)
    return {"status": "evaluated", "session_id": session_id, "mastery": item["mastery"],
            "next_review_date": item["next_review_date"], **attempt}


def resume(root: Path) -> dict[str, Any]:
    progress = load_progress(root)
    current = progress.get("current_session")
    if current is None:
        sessions = sorted((root / "algorithms" / "sessions").glob("*/session.json"), key=lambda path: path.stat().st_mtime)
        if not sessions:
            return {"status": "empty", "message": "尚无算法练习会话"}
        session = read_json(sessions[-1])
        problem = get_problem(session["problem_id"])
        start_session(progress, problem, session["session_id"], session.get("language", "python"))
        save_progress(root, progress)
        current = progress["current_session"]
    item = progress["problems"][current["problem_id"]]
    return {"status": "resumed", "current_session": current, "problem": public_progress_item(item),
            "next_action": current["pending_action"]}


def saved_plan_day(root: Path, on_date: date) -> tuple[str | None, list[dict[str, str]]]:
    for path in sorted((root / "algorithms" / "plans").glob("week-*.json"), reverse=True):
        plan = read_json(path)
        for day in plan.get("days", []):
            if day.get("date") == on_date.isoformat():
                return str(path), day.get("tasks", [])
    return None, []


def today(root: Path, on_date: date, minutes: int, day: int | None = None) -> dict[str, Any]:
    if minutes < 40:
        raise RuntimeError("单次训练至少需要 40 分钟；默认 60 分钟")
    progress = load_progress(root)
    due = due_problem_ids(progress, on_date)
    source_plan, planned_tasks = saved_plan_day(root, on_date) if day is None else (None, [])
    week = sorted((item for item in problems() if item.get("week_day")), key=lambda item: item["week_day"])
    new = next((item["id"] for item in week if item["id"] not in progress["problems"]), None)
    if day in FIRST_WEEK:
        selected = due[0] if due else FIRST_WEEK[day]
        mode = "due_review" if due else "new_problem"
    elif day == 6:
        selected = due[0] if due else FIRST_WEEK[1]
        mode = "weekly_review"
    elif day == 7:
        selected = due[0] if due else FIRST_WEEK[1]
        mode = "mock_interview"
    elif planned_tasks and not due:
        selected = planned_tasks[0]["problem_id"]
        mode = planned_tasks[0]["type"]
    else:
        selected = due[0] if due else new or recommend(root)["problem_id"]
        mode = "due_review" if due else "new_problem"
    blocks = [
        {"minutes": 10, "activity": "复述题意、提出方案并检查正确性"},
        {"minutes": 15, "activity": "基于你主动提出的方案讨论复杂度"},
        {"minutes": minutes - 35, "activity": "独立 Java 编码和真实测试"},
        {"minutes": 10, "activity": "错误归因与安排复习"},
    ]
    if mode == "weekly_review":
        blocks = [
            {"minutes": 10, "activity": "回忆本周错误类型"},
            {"minutes": minutes - 20, "activity": "限时重做最薄弱题"},
            {"minutes": 10, "activity": "对照旧错误并更新复习日期"},
        ]
    elif mode == "mock_interview":
        blocks = [
            {"minutes": 5, "activity": "面试官口述题目"},
            {"minutes": minutes - 15, "activity": "边讲边写并运行测试"},
            {"minutes": 10, "activity": "复杂度追问与复盘"},
        ]
    return {
        "status": "planned", "date": on_date.isoformat(), "minutes": minutes,
        "week_day": day, "priority": mode, "due_reviews": due,
        "selected_problem": selected, "source_plan": source_plan,
        "planned_tasks": planned_tasks,
        "blocks": blocks,
    }


def link_gap(root: Path, problem_query: str, case_id: str, question_id: str,
             knowledge_key: str, source_path: str) -> dict[str, Any]:
    if not case_id.strip() or not question_id.strip() or not knowledge_key.strip():
        raise RuntimeError("case、question 和 knowledge-key 不能为空")
    if any(len(value) > 160 for value in (case_id, question_id, knowledge_key)):
        raise RuntimeError("来源标识过长；这里只保存稳定 ID，不保存问题原文")
    if source_path:
        relative = PurePosixPath(source_path)
        if relative.is_absolute() or ".." in relative.parts or len(source_path) > 512:
            raise RuntimeError("source-path 必须是数据目录内的安全相对路径")
    problem = get_problem(problem_query)
    progress = load_progress(root)
    item = ensure_problem(progress, problem)
    link = {"case_id": case_id, "question_id": question_id,
            "knowledge_key": knowledge_key, "source_path": source_path}
    before = len(item["source_links"])
    link_source(item, link)
    if len(item["source_links"]) == before:
        return {"status": "already_linked", "problem_id": problem["id"],
                "source_link": link, "profile_event_id": f"algorithm:{problem['id']}"}
    save_progress(root, progress)
    event_id = f"algorithm:{problem['id']}"
    previous = latest_events(load_revisions(root)).get(event_id)
    event = {
        "event_id": event_id, "revision": previous["revision"] + 1 if previous else 1,
        "recorded_at": now_iso(), "case_id": case_id, "change_id": f"link:{question_id}",
        "type": "algorithm_weakness", "key": f"algorithm/{problem['id']}", "status": "pending",
        "evidence": [question_id], "reason": "面试知识缺口已关联算法练习",
        "value": {"topic": problem["topic"], "source_links": item["source_links"]},
    }
    event["content_hash"] = content_hash(event)
    append_event(root, event)
    rebuild_profile(root)
    return {"status": "linked", "problem_id": problem["id"], "source_link": link,
            "profile_event_id": event_id}


def reset_training(root: Path, scope: str, confirmation: str) -> dict[str, Any]:
    required = "RESET_ALGORITHM_PROGRESS" if scope == "progress" else "DELETE_ALGORITHM_TRAINING"
    if confirmation != required:
        raise RuntimeError(f"危险操作未确认；请添加 --confirm {required}")
    algorithms = root / "algorithms"
    if scope == "progress":
        path = algorithms / "progress.json"
        if path.exists():
            path.unlink()
    else:
        if algorithms.exists():
            shutil.rmtree(algorithms)
        algorithms.mkdir(parents=True, exist_ok=True)
    return {"status": "reset", "scope": scope, "recoverable": False}


def main() -> int:
    parser = argparse.ArgumentParser(description="本地算法面试陪练")
    parser.add_argument("--root", required=True, type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    sub.add_parser("recommend")
    sub.add_parser("resume")
    sub.add_parser("progress")
    history_parser = sub.add_parser("scan-gaps")
    history_parser.add_argument("--apply", action="store_true")
    week_parser = sub.add_parser("week-plan")
    week_parser.add_argument("--week-start", type=date.fromisoformat, default=date.today())
    week_parser.add_argument("--save", action="store_true")
    export_parser = sub.add_parser("export")
    export_parser.add_argument("--kind", choices=("mistakes", "weekly", "cram"), required=True)
    export_parser.add_argument("--output", type=Path, required=True)
    export_parser.add_argument("--on-date", type=date.fromisoformat, default=date.today())
    export_parser.add_argument("--week-start", type=date.fromisoformat)
    export_parser.add_argument("--force", action="store_true")
    today_parser = sub.add_parser("today")
    today_parser.add_argument("--on-date", type=date.fromisoformat, default=date.today())
    today_parser.add_argument("--minutes", type=int, default=60)
    today_parser.add_argument("--day", type=int, choices=range(1, 8))
    start_parser = sub.add_parser("start")
    start_parser.add_argument("--problem")
    start_parser.add_argument("--day", type=int, choices=range(1, 6))
    start_parser.add_argument("--language", choices=("python", "java"))
    for command in ("hint", "review", "reveal", "inspect-java", "record", "oral-review"):
        item = sub.add_parser(command)
        item.add_argument("--session", required=True)
        if command == "reveal": item.add_argument("--confirm", action="store_true")
        if command == "inspect-java": item.add_argument("--solution", type=Path)
        if command == "oral-review": item.add_argument("--transcript", type=Path, required=True)
        if command == "record":
            item.add_argument("--stage", choices=tuple(NEXT_ACTIONS), required=True)
            item.add_argument("--concept", action="append", default=[])
            item.add_argument("--mistake", action="append", default=[])
    submit_parser = sub.add_parser("submit")
    submit_parser.add_argument("--session", required=True)
    submit_parser.add_argument("--solution", type=Path)
    submit_parser.add_argument("--explanation", default="")
    submit_parser.add_argument("--runner", choices=("docker", "local"), default="docker")
    submit_parser.add_argument("--trust-solution", action="store_true")
    submit_parser.add_argument("--image", help="已缓存的本地容器镜像")
    submit_parser.add_argument("--on-date", type=date.fromisoformat)
    link_parser = sub.add_parser("link-gap")
    link_parser.add_argument("--problem", required=True)
    link_parser.add_argument("--case", required=True)
    link_parser.add_argument("--question", required=True)
    link_parser.add_argument("--knowledge-key", required=True)
    link_parser.add_argument("--source-path", default="")
    reset_parser = sub.add_parser("reset")
    reset_parser.add_argument("--scope", choices=("progress", "all"), required=True)
    reset_parser.add_argument("--confirm", required=True)
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    if not (root / "config.json").is_file():
        raise RuntimeError("数据目录尚未初始化")
    require_store_compatible(root)
    if args.command == "list":
        result = {"status": "ok", "problems": [
            {key: item.get(key) for key in ("id", "leetcode_id", "title", "difficulty", "week_day")}
            for item in problems()
        ]}
    elif args.command == "recommend": result = recommend(root)
    elif args.command == "week-plan":
        result = build_week_plan(root, problems(), args.week_start, args.save)
    elif args.command == "export":
        result = export_report(root, args.kind, args.output, args.on_date, args.week_start, args.force)
    elif args.command == "scan-gaps":
        result = scan_history(root)
        if args.apply:
            applied = []
            for candidate in result["candidates"]:
                applied.append(link_gap(
                    root, candidate["problem_id"], candidate["case_id"], candidate["question_id"],
                    candidate["knowledge_key"], candidate["source_path"],
                ))
            result["write_performed"] = True
            result["applied"] = applied
    elif args.command == "today": result = today(root, args.on_date, args.minutes, args.day)
    elif args.command == "start":
        selected = args.problem or (FIRST_WEEK[args.day] if args.day else recommend(root)["problem_id"])
        canonical = get_problem(selected)["id"]
        language = args.language or ("java" if canonical in FIRST_WEEK.values() else "python")
        result = start(root, selected, language)
    elif args.command == "hint": result = hint(root, args.session)
    elif args.command == "reveal": result = reveal(root, args.session, args.confirm)
    elif args.command == "record": result = record_learning(root, args.session, args.stage, args.concept, args.mistake)
    elif args.command == "inspect-java": result = inspect_submission(root, args.session, args.solution)
    elif args.command == "oral-review":
        _, session = load_session(root, args.session)
        result = review_oral(root, session, get_problem(session["problem_id"]), args.transcript)
    elif args.command == "resume": result = resume(root)
    elif args.command == "progress":
        progress = load_progress(root)
        result = {
            "status": "ok",
            "progress": {
                **progress,
                "problems": {
                    key: public_progress_item(value) for key, value in progress["problems"].items()
                },
            },
        }
    elif args.command == "review":
        directory, session = load_session(root, args.session)
        result = {"status": "ok", "session": session, "attempts": attempts(directory)}
    elif args.command == "link-gap":
        result = link_gap(root, args.problem, args.case, args.question, args.knowledge_key, args.source_path)
    elif args.command == "reset": result = reset_training(root, args.scope, args.confirm)
    else:
        result = submit(root, args.session, args.solution, args.explanation,
                        args.runner, args.trust_solution, args.image, args.on_date)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        raise SystemExit(1)
