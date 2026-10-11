"""Offline algorithm practice with executable cases and append-only feedback."""

from __future__ import annotations

import argparse
import json
import shutil
import uuid
from pathlib import Path
from typing import Any

from check_data_compatibility import require_store_compatible
from coach_runner import IMAGES, docker_available, run_case
from common import now_iso, read_json, sha256_file, write_json
from profile_store import append_event, content_hash, latest_events, load_revisions, rebuild_profile


BANK = Path(__file__).resolve().parents[1] / "assets" / "algorithm-problems.json"
STARTER = """def solve(data):
    # data 是题目描述中的 JSON 对象；返回可序列化为 JSON 的结果。
    raise NotImplementedError("请实现 solve")
"""
JAVA_STARTER = """import java.util.Map;

public class Solution {
    // data 是题目描述中的 JSON 对象；数字为 Long 或 Double，数组为 List。
    public static Object solve(Map<String, Object> data) {
        throw new UnsupportedOperationException("请实现 solve");
    }
}
"""


def problems() -> list[dict[str, Any]]:
    return read_json(BANK)


def recommend(root: Path) -> dict[str, Any]:
    bank = problems()
    revisions = latest_events(load_revisions(root))
    pending = {
        event.get("key", "").removeprefix("algorithm/")
        for event in revisions.values()
        if event.get("type") == "algorithm_weakness" and event.get("status") == "pending"
    }
    counts: dict[str, int] = {}
    for session_path in (root / "algorithms" / "sessions").glob("*/session.json"):
        try:
            problem_id = read_json(session_path).get("problem_id")
            counts[problem_id] = counts.get(problem_id, 0) + 1
        except (OSError, ValueError):
            continue
    chosen = sorted(bank, key=lambda item: (item["id"] not in pending, counts.get(item["id"], 0), item["id"]))[0]
    return {
        "status": "recommended", "problem_id": chosen["id"], "title": chosen["title"],
        "reason": "长期档案中仍待复盘" if chosen["id"] in pending else "练习次数较少",
    }


def get_problem(problem_id: str) -> dict[str, Any]:
    for problem in problems():
        if problem["id"] == problem_id:
            return problem
    raise RuntimeError(f"未知题目：{problem_id}")


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


def write_attempt(directory: Path, attempt: dict[str, Any]) -> None:
    with (directory / "attempts.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(attempt, ensure_ascii=False) + "\n")


def attempts(directory: Path) -> list[dict[str, Any]]:
    path = directory / "attempts.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def record_profile(root: Path, session: dict[str, Any], attempt: dict[str, Any]) -> None:
    problem_id = session["problem_id"]
    event_id = f"algorithm:{problem_id}"
    latest = latest_events(load_revisions(root))
    previous = latest.get(event_id)
    passed = attempt["passed"] == attempt["total"]
    event = {
        "event_id": event_id,
        "revision": previous["revision"] + 1 if previous else 1,
        "recorded_at": now_iso(),
        "case_id": session["session_id"],
        "change_id": attempt["attempt_id"],
        "type": "algorithm_weakness",
        "key": f"algorithm/{problem_id}",
        "status": "pending",
        "evidence": [attempt["attempt_id"]],
        "reason": "用例通过，复杂度与解释待人工核查" if passed else "存在未通过的算法用例",
        "value": {
            "topic": session["topic"], "passed": attempt["passed"],
            "total": attempt["total"], "explanation": attempt["explanation"],
        },
    }
    event["content_hash"] = content_hash(event)
    append_event(root, event)
    rebuild_profile(root)


def start(root: Path, problem_id: str, language: str = "python") -> dict[str, Any]:
    problem = get_problem(problem_id)
    session_id = "alg-" + uuid.uuid4().hex[:12]
    directory = root / "algorithms" / "sessions" / session_id
    directory.mkdir(parents=True, exist_ok=False)
    work = root / "algorithms" / "private" / session_id
    work.mkdir(parents=True, exist_ok=False)
    work.chmod(0o700)
    solution = work / ("solution.py" if language == "python" else "Solution.java")
    solution.write_text(STARTER if language == "python" else JAVA_STARTER, encoding="utf-8")
    session = {
        "schema_version": 1, "session_id": session_id,
        "problem_id": problem_id, "topic": problem["topic"],
        "created_at": now_iso(), "hint_level": 0, "language": language,
        "solution": str(solution.relative_to(root)),
    }
    write_json(directory / "session.json", session)
    return {
        "status": "started", "session_id": session_id, "problem": problem["title"],
        "statement": problem["statement"], "topic": problem["topic"],
        "difficulty": problem["difficulty"],
        "public_tests": [item for item in problem["tests"] if item["public"]],
        "solution": str(solution), "language": language,
    }


def hint(root: Path, session_id: str) -> dict[str, Any]:
    directory, session = load_session(root, session_id)
    problem = get_problem(session["problem_id"])
    level = session.get("hint_level", 0)
    if level >= len(problem["hints"]):
        return {"status": "no_more_hints", "session_id": session_id}
    session["hint_level"] = level + 1
    write_json(directory / "session.json", session)
    return {"status": "hint", "session_id": session_id, "level": level + 1, "text": problem["hints"][level]}


def submit(root: Path, session_id: str, solution_arg: Path | None, explanation: str,
           runner: str = "docker", trust_solution: bool = False, image: str | None = None) -> dict[str, Any]:
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
    snapshot = (root / "algorithms" / "private" / session_id / f"attempt-{len(attempts(directory))+1:03d}{suffix}")
    shutil.copy2(solution, snapshot)
    results = []
    for index, test in enumerate(problem["tests"], start=1):
        outcome = run_case(snapshot, test["input"], language, runner, image)
        passed = "error" not in outcome and outcome.get("result") == test["expected"]
        row = {"test": index, "public": test["public"], "passed": passed, "elapsed_ms": outcome["elapsed_ms"]}
        if test["public"] or not passed:
            row["input"] = test["input"]
            row["expected"] = test["expected"]
            row["actual"] = outcome.get("result")
            if "error" in outcome:
                row["error"] = outcome["error"]
        results.append(row)
    attempt = {
        "attempt_id": f"{session_id}-a{len(attempts(directory))+1:03d}",
        "created_at": now_iso(), "source_sha256": sha256_file(snapshot),
        "source": str(snapshot.relative_to(root)),
        "passed": sum(item["passed"] for item in results),
        "total": len(results), "results": results,
        "explanation": explanation.strip(), "hint_level": session.get("hint_level", 0),
        "complexity_target": problem["target_complexity"],
        "review_points": problem["review_points"],
        "human_review_status": "pending", "language": language, "runner": runner,
    }
    write_attempt(directory, attempt)
    record_profile(root, session, attempt)
    write_json(directory / "latest-report.json", attempt)
    return {"status": "evaluated", "session_id": session_id, **attempt}


def main() -> int:
    parser = argparse.ArgumentParser(description="本地算法面试陪练")
    parser.add_argument("--root", required=True, type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    sub.add_parser("recommend")
    start_parser = sub.add_parser("start")
    start_parser.add_argument("--problem")
    start_parser.add_argument("--language", choices=("python", "java"), default="python")
    for command in ("hint", "review"):
        item = sub.add_parser(command)
        item.add_argument("--session", required=True)
    submit_parser = sub.add_parser("submit")
    submit_parser.add_argument("--session", required=True)
    submit_parser.add_argument("--solution", type=Path)
    submit_parser.add_argument("--explanation", default="")
    submit_parser.add_argument("--runner", choices=("docker", "local"), default="docker")
    submit_parser.add_argument("--trust-solution", action="store_true")
    submit_parser.add_argument("--image", help="已缓存的本地容器镜像")
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    if not (root / "config.json").is_file():
        raise RuntimeError("数据目录尚未初始化")
    require_store_compatible(root)
    if args.command == "list":
        result = {"status": "ok", "problems": [
            {key: item[key] for key in ("id", "title", "topic", "difficulty")}
            for item in problems()
        ]}
    elif args.command == "recommend":
        result = recommend(root)
    elif args.command == "start":
        result = start(root, args.problem or recommend(root)["problem_id"], args.language)
    elif args.command == "hint":
        result = hint(root, args.session)
    elif args.command == "review":
        directory, session = load_session(root, args.session)
        result = {"status": "ok", "session": session, "attempts": attempts(directory)}
    else:
        result = submit(root, args.session, args.solution, args.explanation,
                        args.runner, args.trust_solution, args.image)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        raise SystemExit(1)
