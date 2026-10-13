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


def run_script(name: str, *args: str, expect: int = 0) -> dict:
    result = subprocess.run([sys.executable, str(SCRIPTS / name), *args], text=True,
                            capture_output=True, check=False)
    if result.returncode != expect:
        raise AssertionError(f"{name} exit={result.returncode}\nstdout={result.stdout}\nstderr={result.stderr}")
    return json.loads(result.stdout.strip().splitlines()[-1])


class AlgorithmTrainingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "data"
        run_script("init_store.py", "--root", str(self.root))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def coach(self, *args: str, expect: int = 0) -> dict:
        return run_script("algorithm_coach.py", "--root", str(self.root), *args, expect=expect)

    def start_java(self, problem: str = "1") -> dict:
        return self.coach("start", "--problem", problem, "--language", "java")

    def test_guided_start_and_one_level_hint(self) -> None:
        started = self.start_java()
        self.assertEqual((started["stage"], started["hint_level"], started["next_action"]),
                         ("understanding", 0, "ask_approach"))
        self.assertNotIn("reference_solution", started)
        self.assertNotIn("topic", started)
        self.assertNotRegex(
            json.dumps(started, ensure_ascii=False).lower(),
            r"hashmap|hashset|动态规划|滑动窗口|双指针|二分查找|拓扑排序|并查集|栈|队列|o\(n\)",
        )
        skeleton = Path(started["solution"]).read_text(encoding="utf-8").lower()
        self.assertNotRegex(skeleton, r"hashmap|hashset|stack|queue|dynamic|two.?pointer|window")
        first = self.coach("hint", "--session", started["session_id"])
        second = self.coach("hint", "--session", started["session_id"])
        self.assertEqual((first["level"], second["level"]), (1, 2))
        self.assertNotRegex(first["text"].lower(), r"hashmap|hashset|栈|队列|双指针|窗口|o\(n\)")

    def test_first_week_defaults_to_java_without_breaking_legacy_tasks(self) -> None:
        canonical = self.coach("start", "--problem", "1")
        legacy = self.coach("start", "--problem", "max-subarray")
        self.assertEqual(canonical["language"], "java")
        self.assertEqual(legacy["language"], "python")

    def test_teaching_stages_record_concepts_and_resume(self) -> None:
        started = self.start_java()
        session = started["session_id"]
        brute = self.coach("record", "--session", session, "--stage", "independent_thinking",
                           "--mistake", "loop_boundary")
        self.assertEqual(brute["next_action"], "verify_current_solution")
        optimized = self.coach("record", "--session", session, "--stage", "optimization",
                               "--concept", "hash_map")
        self.assertEqual(optimized["next_action"], "ask_key_value_and_lookup_insert_order")
        resumed = self.coach("resume")
        self.assertEqual(resumed["current_session"]["session_id"], session)
        self.assertEqual(resumed["next_action"], "ask_key_value_and_lookup_insert_order")
        self.assertNotIn("topic", resumed["problem"])

    def test_optimization_cannot_be_inferred_or_auto_filled(self) -> None:
        started = self.start_java()
        blocked = self.coach(
            "record", "--session", started["session_id"], "--stage", "optimization", expect=1,
        )
        self.assertIn("主动提出", blocked["error"])
        self.assertNotIn("哈希表", blocked["error"])

    def test_progress_and_list_hide_internal_strategy_topics(self) -> None:
        started = self.start_java()
        listed = self.coach("list")
        self.assertNotIn("topic", listed["problems"][0])
        progress = self.coach("progress")
        self.assertNotIn("topic", progress["progress"]["problems"]["leetcode-1"])
        self.assertNotIn("topic_key", progress["progress"]["problems"]["leetcode-1"])

    def test_contains_duplicate_semantics_lead_to_coding(self) -> None:
        started = self.start_java("217")
        result = self.coach("record", "--session", started["session_id"],
                            "--stage", "independent_coding", "--concept", "hashset_add_semantics")
        self.assertEqual(result["next_action"], "request_java_code")

    def test_static_java_checks_do_not_claim_actual_execution(self) -> None:
        started = self.start_java()
        solution = Path(started["solution"])
        solution.write_text(
            "import java.util.*; public class Solution { public int[] twoSum(int[] nums,int target) {"
            "Map<Integer,Integer> m=new HashMap<>(); for(int i=0;i<nums.length;i++){m.put(nums[i],i);"
            "if(m.containsKey(target-nums[i])) return new int{nums[i],target-nums[i]};} return null;} }",
            encoding="utf-8",
        )
        result = self.coach("inspect-java", "--session", started["session_id"])
        categories = {item["category"] for item in result["findings"]}
        self.assertFalse(result["actual_tested"])
        self.assertTrue({"array_initializer_syntax", "returned_value_instead_of_index",
                         "insert_before_lookup_self_pair"}.issubset(categories))
        saved = self.coach("progress")["progress"]["problems"]["leetcode-1"]
        self.assertEqual(saved["stage"], "code_review")
        self.assertTrue(any(item["category"] == "array_initializer_syntax" for item in saved["mistakes"]))

    def test_full_answer_requires_confirmation_and_marks_assisted(self) -> None:
        started = self.start_java()
        blocked = self.coach("reveal", "--session", started["session_id"], expect=1)
        self.assertIn("--confirm", blocked["error"])
        revealed = self.coach("reveal", "--session", started["session_id"], "--confirm")
        self.assertTrue(revealed["assisted"])
        progress = self.coach("progress")["progress"]["problems"]["leetcode-1"]
        self.assertEqual((progress["hint_level"], progress["mastery"]), (4, "assisted"))

    @unittest.skipUnless(shutil.which("javac") and shutil.which("java"), "缺少 JDK")
    def test_java_compile_failure_is_from_actual_execution(self) -> None:
        started = self.start_java()
        Path(started["solution"]).write_text(
            "public class Solution { public int[] twoSum(int[] nums, int target) { return new int{0,1}; } }",
            encoding="utf-8",
        )
        attempt = self.coach("submit", "--session", started["session_id"], "--runner", "local",
                             "--trust-solution", "--on-date", "2026-10-11")
        self.assertTrue(attempt["actual_tested"])
        self.assertLess(attempt["passed"], attempt["total"])
        self.assertIn("Java 编译失败", attempt["results"][0]["error"])

    @unittest.skipUnless(shutil.which("javac") and shutil.which("java"), "缺少 JDK")
    def test_actual_java_tests_and_spaced_review(self) -> None:
        started = self.start_java()
        Path(started["solution"]).write_text(
            "import java.util.*; public class Solution { public int[] twoSum(int[] nums,int target) {"
            "Map<Integer,Integer> m=new HashMap<>(); for(int i=0;i<nums.length;i++){int n=target-nums[i];"
            "if(m.containsKey(n)) return new int[]{m.get(n),i}; m.put(nums[i],i);} throw new RuntimeException();} }",
            encoding="utf-8",
        )
        attempt = self.coach("submit", "--session", started["session_id"], "--runner", "local",
                             "--trust-solution", "--on-date", "2026-10-11")
        self.assertTrue(attempt["actual_tested"])
        self.assertEqual((attempt["passed"], attempt["total"]), (3, 3))
        self.assertEqual((attempt["mastery"], attempt["next_review_date"]),
                         ("independent_passed", "2026-10-12"))
        plan = self.coach("today", "--on-date", "2026-10-12")
        self.assertEqual((plan["priority"], plan["selected_problem"]), ("due_review", "leetcode-1"))

    def test_first_week_review_and_mock_days_are_explicit(self) -> None:
        review = self.coach("today", "--day", "6", "--on-date", "2026-10-11")
        mock = self.coach("today", "--day", "7", "--on-date", "2026-10-11")
        self.assertEqual(review["priority"], "weekly_review")
        self.assertEqual(mock["priority"], "mock_interview")

    def test_gap_link_uses_identifiers_without_copying_sensitive_text(self) -> None:
        result = self.coach("link-gap", "--problem", "1", "--case", "case-private",
                            "--question", "q-007", "--knowledge-key", "hash-table/order",
                            "--source-path", "interviews/case-private/qa-tree.json")
        self.assertEqual(result["profile_event_id"], "algorithm:leetcode-1")
        raw = (self.root / "algorithms" / "progress.json").read_text(encoding="utf-8")
        self.assertIn("q-007", raw)
        self.assertNotIn("面试原文", raw)
        state = json.loads((self.root / "profile" / "state.json").read_text(encoding="utf-8"))
        event = state["by_type"]["algorithm_weakness"][0]
        self.assertEqual(event["value"]["source_links"][0]["question_id"], "q-007")
        blocked = self.coach("link-gap", "--problem", "1", "--case", "case-private",
                             "--question", "q-008", "--knowledge-key", "hash-table/order",
                             "--source-path", "../outside.json", expect=1)
        self.assertIn("安全相对路径", blocked["error"])

    def test_progress_corruption_blocks_writes(self) -> None:
        progress = self.root / "algorithms" / "progress.json"
        progress.parent.mkdir(parents=True, exist_ok=True)
        progress.write_text('{"schema_version":99,"problems":{}}', encoding="utf-8")
        result = self.coach("start", "--problem", "1", expect=1)
        self.assertIn("兼容性检查未通过", result["error"])


if __name__ == "__main__":
    unittest.main()
