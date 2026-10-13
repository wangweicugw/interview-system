from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL / "scripts"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(SCRIPTS))

from algorithm_progress import default_progress, ensure_problem, record_attempt  # noqa: E402


def run_script(name: str, *args: str, expect: int = 0) -> dict:
    result = subprocess.run([sys.executable, str(SCRIPTS / name), *args], text=True,
                            capture_output=True, check=False)
    if result.returncode != expect:
        raise AssertionError(f"{name} exit={result.returncode}\nstdout={result.stdout}\nstderr={result.stderr}")
    return json.loads(result.stdout.strip().splitlines()[-1])


class AlgorithmP1Test(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "data"
        run_script("init_store.py", "--root", str(self.root))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def coach(self, *args: str, expect: int = 0) -> dict:
        return run_script("algorithm_coach.py", "--root", str(self.root), *args, expect=expect)

    def test_week_plan_reduces_load_after_real_failure(self) -> None:
        started = self.coach("start", "--problem", "max-subarray")
        Path(started["solution"]).write_text("def solve(data):\n    return 0\n", encoding="utf-8")
        self.coach("submit", "--session", started["session_id"], "--runner", "local", "--trust-solution")
        plan = self.coach("week-plan", "--week-start", "2026-10-12", "--save")
        self.assertEqual(plan["new_problem_target"], 3)
        self.assertIn("题量降到", plan["adjustment"])
        self.assertTrue(Path(plan["saved_to"]).is_file())
        today = self.coach("today", "--on-date", "2026-10-12")
        self.assertEqual(today["source_plan"], plan["saved_to"])
        self.assertTrue(today["planned_tasks"])
        self.assertTrue(plan["days"][5]["tasks"])
        self.assertTrue(plan["days"][6]["tasks"])

    def test_week_plan_increases_load_after_independent_pass(self) -> None:
        started = self.coach("start", "--problem", "max-subarray")
        Path(started["solution"]).write_text(
            "def solve(data):\n"
            "    best = current = data['nums'][0]\n"
            "    for value in data['nums'][1:]:\n"
            "        current = max(value, current + value)\n"
            "        best = max(best, current)\n"
            "    return best\n",
            encoding="utf-8",
        )
        self.coach("submit", "--session", started["session_id"], "--runner", "local", "--trust-solution")
        plan = self.coach("week-plan", "--week-start", "2026-10-12")
        self.assertEqual(plan["new_problem_target"], 6)
        self.assertIn("提高到 6", plan["adjustment"])

    def test_reports_export_without_copying_interview_text(self) -> None:
        started = self.coach("start", "--problem", "1")
        self.coach("record", "--session", started["session_id"], "--stage", "code_review",
                   "--mistake", "returned_value_instead_of_index")
        self.coach("link-gap", "--problem", "1", "--case", "case-1", "--question", "q-1",
                   "--knowledge-key", "hash-map", "--source-path", "interviews/case-1/qa-tree.json")
        output = Path(self.temp.name) / "cram.md"
        exported = self.coach("export", "--kind", "cram", "--output", str(output),
                              "--on-date", "2026-10-11")
        self.assertEqual(exported["status"], "exported")
        body = output.read_text(encoding="utf-8")
        self.assertIn("case-1/q-1", body)
        self.assertNotIn("面试敏感原文", body)
        for kind, heading in (("mistakes", "算法错题摘要"), ("weekly", "算法学习周报")):
            extra = Path(self.temp.name) / f"{kind}.md"
            self.coach("export", "--kind", kind, "--output", str(extra),
                       "--on-date", "2026-10-11", "--week-start", "2026-10-05")
            self.assertIn(heading, extra.read_text(encoding="utf-8"))
        blocked = self.coach("export", "--kind", "cram", "--output", str(output), expect=1)
        self.assertIn("--force", blocked["error"])

    def test_history_scan_is_read_only_until_apply(self) -> None:
        created = run_script(
            "ingest_transcript.py", "--root", str(self.root),
            "--transcript", str(FIXTURES / "interview.md"), "--company", "示例公司", "--date", "2026-10-10",
        )
        case = Path(created["case_path"])
        (case / "qa-tree.json").write_text(json.dumps({
            "schema_version": 1, "case_id": created["case_id"],
            "questions": [{"question_id": "q-alg", "question": "两数之和如何用 HashMap 避免重复下标？"}],
        }, ensure_ascii=False), encoding="utf-8")
        scanned = self.coach("scan-gaps")
        self.assertFalse(scanned["write_performed"])
        self.assertEqual(scanned["candidates"][0]["problem_id"], "leetcode-1")
        self.assertFalse((self.root / "algorithms" / "progress.json").exists())
        applied = self.coach("scan-gaps", "--apply")
        self.assertTrue(applied["write_performed"])
        progress = json.loads((self.root / "algorithms" / "progress.json").read_text(encoding="utf-8"))
        link = progress["problems"]["leetcode-1"]["source_links"][0]
        self.assertEqual((link["case_id"], link["question_id"]), (created["case_id"], "q-alg"))

    def test_oral_review_is_heuristic_and_does_not_upgrade_mastery(self) -> None:
        started = self.coach("start", "--problem", "1")
        transcript = Path(self.temp.name) / "oral.txt"
        transcript.write_text(
            "我遍历数组，用哈希表先查补数再存当前值。时间 O(n)，空间 O(n)，还要考虑重复值边界。",
            encoding="utf-8",
        )
        result = self.coach("oral-review", "--session", started["session_id"],
                            "--transcript", str(transcript))
        self.assertEqual(result["method"], "keyword_heuristic")
        self.assertFalse(result["semantic_correctness_verified"])
        self.assertFalse(result["mastery_changed"])
        self.assertEqual(result["coverage_score"], 1.0)
        progress = self.coach("progress")["progress"]["problems"]["leetcode-1"]
        self.assertEqual(progress["mastery"], "in_progress")
        feedback = self.root / "algorithms" / "sessions" / started["session_id"] / "oral-feedback.jsonl"
        self.assertNotIn("我遍历数组", feedback.read_text(encoding="utf-8"))

    def test_transferable_requires_independent_pass_on_second_similar_problem(self) -> None:
        progress = default_progress()
        first = {"id": "a", "title": "A", "topic": "双指针", "topic_key": "two-pointers"}
        second = {"id": "b", "title": "B", "topic": "双指针", "topic_key": "two-pointers"}
        ensure_problem(progress, first)
        ensure_problem(progress, second)
        base = {"created_at": "2026-10-11T00:00:00+00:00", "actual_tested": True,
                "passed": 1, "total": 1}
        first_state = record_attempt(progress, first, {**base, "attempt_id": "a-1"}, [], None)
        second_state = record_attempt(progress, second, {**base, "attempt_id": "b-1"}, [], None)
        self.assertEqual(first_state["mastery"], "independent_passed")
        self.assertEqual(second_state["mastery"], "transferable")

    @unittest.skipUnless(shutil.which("javac") and shutil.which("java"), "缺少 JDK")
    def test_java_linked_list_tree_and_grid_signatures(self) -> None:
        solutions = {
            "206": (
                "class ListNode { int val; ListNode next; ListNode(int v){val=v;} } "
                "public class Solution { public ListNode reverseList(ListNode head) {"
                "ListNode p=null,c=head; while(c!=null){ListNode n=c.next;c.next=p;p=c;c=n;} return p;} }"
            ),
            "104": (
                "public class Solution { public int maxDepth(TreeNode root) {"
                "return root==null?0:1+Math.max(maxDepth(root.left),maxDepth(root.right));} }"
            ),
            "200": (
                "public class Solution { public int numIslands(char[][] g){int n=0;for(int r=0;r<g.length;r++)"
                "for(int c=0;c<g[r].length;c++)if(g[r][c]=='1'){n++;f(g,r,c);}return n;}"
                "void f(char[][]g,int r,int c){if(r<0||r==g.length||c<0||c==g[r].length||g[r][c]!='1')return;"
                "g[r][c]='0';f(g,r+1,c);f(g,r-1,c);f(g,r,c+1);f(g,r,c-1);} }"
            ),
        }
        for problem, source in solutions.items():
            started = self.coach("start", "--problem", problem, "--language", "java")
            Path(started["solution"]).write_text(source, encoding="utf-8")
            attempt = self.coach("submit", "--session", started["session_id"],
                                 "--runner", "local", "--trust-solution")
            self.assertEqual(attempt["passed"], attempt["total"], problem)


if __name__ == "__main__":
    unittest.main()
