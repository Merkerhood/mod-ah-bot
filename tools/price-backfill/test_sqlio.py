import os
import tempfile
import unittest

import sqlio


SAMPLE = (
    "SET NAMES utf8mb4;\n"
    "SET FOREIGN_KEY_CHECKS = 0;\n"
    "\n"
    "DROP TABLE IF EXISTS `mod_auctionhousebot_priceOverride`;\n"
    "INSERT INTO `mod_auctionhousebot_priceOverride` VALUES (35, 1667656, 1000594);\n"
    "INSERT INTO `mod_auctionhousebot_priceOverride` VALUES (36, 8621, 6950);\n"
    "\n"
    "SET FOREIGN_KEY_CHECKS = 1;\n"
)


class ParseTest(unittest.TestCase):
    def test_parses_item_avg_min(self):
        with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False) as fh:
            fh.write(SAMPLE)
            path = fh.name
        try:
            result = sqlio.parse_override_sql(path)
        finally:
            os.unlink(path)
        self.assertEqual(result, {35: (1667656, 1000594), 36: (8621, 6950)})


class EmitTest(unittest.TestCase):
    def test_roundtrip_and_sorting(self):
        with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False) as fh:
            path = fh.name
        try:
            sqlio.write_override_sql(path, [(36, 8621, 6950), (35, 1667656, 1000594)])
            text = open(path, encoding="utf-8").read()
            reparsed = sqlio.parse_override_sql(path)
        finally:
            os.unlink(path)
        self.assertEqual(reparsed, {35: (1667656, 1000594), 36: (8621, 6950)})
        # sorted ascending: item 35 line appears before item 36 line
        self.assertLess(text.index("VALUES (35,"), text.index("VALUES (36,"))
        # format fidelity
        self.assertIn(
            "INSERT INTO `mod_auctionhousebot_priceOverride` VALUES (35, 1667656, 1000594);",
            text,
        )
        self.assertTrue(text.startswith("SET NAMES utf8mb4;\n"))
        self.assertTrue(text.rstrip().endswith("SET FOREIGN_KEY_CHECKS = 1;"))


if __name__ == "__main__":
    unittest.main()
