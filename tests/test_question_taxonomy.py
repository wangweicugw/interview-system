from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import question_taxonomy  # noqa: E402


class QuestionTaxonomyTest(unittest.TestCase):
    def test_aliases_collapse_to_canonical_values(self) -> None:
        for value, expected in (
            ("design", "system_design"),
            ("Architecture", "system_design"),
            ("experience", "project"),
            ("leadership", "behavioral"),
            ("coding", "algorithm"),
            ("database", "technical"),
            ("motivation", "career"),
            (" Technical ", "technical"),
        ):
            with self.subTest(value=value):
                self.assertEqual(question_taxonomy.normalize(value), expected)
                self.assertTrue(question_taxonomy.is_canonical(question_taxonomy.normalize(value)))

    def test_canonical_values_pass_through(self) -> None:
        for value in question_taxonomy.CATEGORIES:
            with self.subTest(value=value):
                self.assertEqual(question_taxonomy.normalize(value), value)
                self.assertTrue(question_taxonomy.is_canonical(value))

    def test_unknown_value_is_reported_not_guessed(self) -> None:
        self.assertFalse(question_taxonomy.is_canonical("随便写的"))
        self.assertEqual(question_taxonomy.normalize("随便写的"), "随便写的")
        self.assertEqual(question_taxonomy.unknown(["technical", "随便写的"]), ["随便写的"])

    def test_label_falls_back_to_raw_value(self) -> None:
        self.assertEqual(question_taxonomy.label("design"), "系统设计与架构")
        self.assertEqual(question_taxonomy.label("随便写的"), "随便写的")


if __name__ == "__main__":
    unittest.main()
