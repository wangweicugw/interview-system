from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from render_interview_html import case_answer_save_form, question_topic, reference_source_notice  # noqa: E402


class RenderTopicPrivacyTest(unittest.TestCase):
    def test_generic_topics_remain_classifiable(self) -> None:
        examples = (
            ("technical", "MySQL 主从延迟", "mysql"),
            ("technical", "消息队列消费者", "messaging"),
            ("technical", "跨分片查询", "sharding"),
            ("career", "求职安排", "career"),
            ("project", "负责的项目", "project-role"),
        )
        for category, question, expected in examples:
            with self.subTest(question=question):
                self.assertEqual(question_topic({"category": category, "question": question}), expected)

    def test_case_answer_form_prefills_each_version_without_headings(self) -> None:
        form = case_answer_save_form("fictional-case", "q-001", {
            "reference_answer": {"steps": [
                {"heading": "30 秒参考答法", "body": "虚构短答。"},
                {"heading": "2 分钟展开", "body": "虚构长答，补充条件与例子。"},
            ]}
        }, [])
        self.assertIn('data-answer-short rows="4">虚构短答。</textarea>', form)
        self.assertIn('data-answer-full rows="8">虚构长答，补充条件与例子。</textarea>', form)
        self.assertNotIn('>30 秒参考答法：', form)
        self.assertNotIn('>2 分钟展开：', form)

    def test_unversioned_outline_does_not_fill_two_minute_field(self) -> None:
        form = case_answer_save_form("fictional-case", "q-002", {
            "reference_answer": {"steps": [
                {"heading": "先定位", "body": "核对虚构请求路径。"},
                {"heading": "再验证", "body": "复测虚构结果。"},
            ]}
        }, [])
        self.assertIn('data-answer-short rows="4"></textarea>', form)
        self.assertIn('data-answer-full rows="8"></textarea>', form)

    def test_reference_notice_escapes_private_metadata_and_keeps_caveat(self) -> None:
        notice = reference_source_notice({
            "inputs": {"reference_transcripts": [{
                "source_label": "示例 <script>",
                "use": "仅作核对 & 不替代原始转写",
            }]}
        })
        self.assertIn("示例 &lt;script&gt;", notice)
        self.assertIn("仅作核对 &amp; 不替代原始转写", notice)
        self.assertIn("不会自动重算本场分析", notice)
        self.assertNotIn("<script>", notice)


if __name__ == "__main__":
    unittest.main()
