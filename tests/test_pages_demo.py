from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_pages_demo import build  # noqa: E402


class PagesDemoTest(unittest.TestCase):
    def test_public_site_contains_only_static_fictional_pages(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "site"
            result = build(output)
            files = {path.relative_to(output).as_posix() for path in output.rglob("*") if path.is_file()}
            self.assertEqual(result["scope"], "fictional_static_only")
            self.assertEqual(len(files), 7)
            self.assertIn(".nojekyll", files)
            self.assertEqual(len([name for name in files if name.endswith(".html")]), 6)
            self.assertFalse(any(name.endswith((".json", ".md", ".txt")) for name in files))
            for page in output.rglob("*.html"):
                content = page.read_text(encoding="utf-8")
                self.assertIn("虚构材料 · 只读演示", content)
                self.assertIn("connect-src 'none'", content)
                self.assertIn("button.disabled = true", content)
                self.assertNotIn("本页含个人面试资料", content)
                self.assertNotIn("真实面试题", content)

    def test_existing_output_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            marker = output / "keep.txt"
            marker.write_text("keep", encoding="utf-8")
            with self.assertRaises(ValueError):
                build(output)
            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")


if __name__ == "__main__":
    unittest.main()
