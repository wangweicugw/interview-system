from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_practice_answer_depth.py"


class PracticeAnswerDepthTest(unittest.TestCase):
    def test_flags_short_outline_but_not_expanded_answer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = root / "interviews" / "fictional-case"
            case.mkdir(parents=True)
            questions = [
                {
                    "question_id": "q-001",
                    "reference_answer": {
                        "steps": [
                            {"heading": "30 秒参考答法", "body": "先说结论，再说依据。"},
                            {"heading": "2 分钟展开", "body": "补充机制和例子。"},
                        ]
                    },
                },
                {
                    "question_id": "q-002",
                    "reference_answer": {
                        "steps": [
                            {"heading": "30 秒参考答法", "body": "先核对请求路径与指标。"},
                            {"heading": "2 分钟展开", "body": "先核对请求路径与指标。" + "逐段记录请求进入服务、读取配置、访问数据源及返回结果的耗时；如果配置读取变慢，先看缓存命中和错误率，再复测同一请求。" * 4},
                        ]
                    },
                },
            ]
            (case / "practice-answers.json").write_text(
                json.dumps({"questions": questions}, ensure_ascii=False), encoding="utf-8"
            )
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--root", str(root)],
                check=True,
                capture_output=True,
                text=True,
            )
            report = json.loads(result.stdout)
            self.assertEqual(report["flagged"], 1)
            self.assertEqual(report["items"][0]["question_id"], "q-001")
            self.assertIn("writing_instruction", report["items"][0]["flags"])


if __name__ == "__main__":
    unittest.main()
