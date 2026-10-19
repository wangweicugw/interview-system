from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from audit_question_coverage import audit_case  # noqa: E402


class QuestionCoverageTest(unittest.TestCase):
    def test_distinguishes_missing_evidence_from_possible_missing_question(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            case = Path(temporary)
            (case / "qa-tree.json").write_text(json.dumps({
                "case_id": "case-1", "questions": [{
                    "question_id": "q-001", "question": "介绍架构",
                    "question_evidence": ["s-001"],
                    "answer_evidence": ["s-002", "s-004"],
                }],
            }), encoding="utf-8")
            (case / "transcript.revised.json").write_text(json.dumps({
                "segments": [
                    {"segment_id": "s-001", "role": "interviewer", "text_raw": "请介绍架构？"},
                    {"segment_id": "s-002", "role": "candidate", "text_raw": "我先说明数据流向与组件边界，然后介绍故障处理。"},
                    {"segment_id": "s-003", "role": "interviewer", "text_raw": "具体为什么这样设计路由？"},
                    {"segment_id": "s-004", "role": "candidate", "text_raw": "因为需要明确流量边界，避免故障时把请求送到不可用节点，还要保证恢复时的路由一致。"},
                    {"segment_id": "s-005", "role": "interviewer", "text_raw": "底层负载均衡是怎么实现的？"},
                    {"segment_id": "s-006", "role": "candidate", "text_raw": "我们先按入口流量划分，再将请求转发到具体实例处理，并保留失败时的回退路径。"},
                ],
            }), encoding="utf-8")
            candidates = audit_case(case)
            self.assertEqual([item["segment_id"] for item in candidates], ["s-003", "s-005"])
            self.assertEqual(candidates[0]["review_hint"], "可能漏标提问证据")
            self.assertEqual(candidates[1]["review_hint"], "可能漏建原题")


if __name__ == "__main__":
    unittest.main()
