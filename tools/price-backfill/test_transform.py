import unittest
from datetime import datetime

import transform
from transform import Config, PriceRow


NOW = datetime(2026, 7, 1, 12, 0, 0)
CFG = Config(price_scale=1.0, min_item_count=1, max_age_days=180, deviation_factor=1.5)


def item_with(stats, sell_price=0):
    return {"item_info": {"SellPrice": sell_price}, "stats": stats}


class BuildRowTest(unittest.TestCase):
    def test_happy_path_maps_avg_and_min(self):
        item = item_with(
            {"avg_price": 4999, "minimum_buyout": 4850, "item_count": 53,
             "item_last_seen": "2026-07-01 11:45:09"},
            sell_price=750,
        )
        row, reason = transform.build_row(4389, item, CFG, NOW)
        self.assertIsNone(reason)
        self.assertEqual(row, PriceRow(4389, 4999, 4850))

    def test_no_stats_is_no_data(self):
        row, reason = transform.build_row(1, {"item_info": {}, "stats": None}, CFG, NOW)
        self.assertIsNone(row)
        self.assertEqual(reason, "no-data")

    def test_thin_below_min_count(self):
        cfg = CFG._replace(min_item_count=3)
        item = item_with({"avg_price": 10, "minimum_buyout": 10, "item_count": 1,
                          "item_last_seen": "2026-07-01 11:45:09"})
        row, reason = transform.build_row(1, item, cfg, NOW)
        self.assertIsNone(row)
        self.assertEqual(reason, "thin")

    def test_stale_beyond_max_age(self):
        item = item_with({"avg_price": 10, "minimum_buyout": 10, "item_count": 5,
                          "item_last_seen": "2025-01-01 00:00:00"})
        row, reason = transform.build_row(1, item, CFG, NOW)
        self.assertIsNone(row)
        self.assertEqual(reason, "stale")

    def test_min_clamped_to_avg(self):
        item = item_with({"avg_price": 100, "minimum_buyout": 250, "item_count": 5,
                          "item_last_seen": "2026-07-01 11:45:09"})
        row, _ = transform.build_row(1, item, CFG, NOW)
        self.assertEqual(row, PriceRow(1, 100, 100))

    def test_vendor_floor_applied(self):
        item = item_with({"avg_price": 5, "minimum_buyout": 5, "item_count": 5,
                          "item_last_seen": "2026-07-01 11:45:09"}, sell_price=90)
        row, _ = transform.build_row(1, item, CFG, NOW)
        self.assertEqual(row, PriceRow(1, 90, 90))

    def test_zero_price_rejected(self):
        item = item_with({"avg_price": 0, "minimum_buyout": 0, "item_count": 5,
                          "item_last_seen": "2026-07-01 11:45:09"}, sell_price=0)
        row, reason = transform.build_row(1, item, CFG, NOW)
        self.assertIsNone(row)
        self.assertEqual(reason, "zero-price")

    def test_price_scale_applied(self):
        cfg = CFG._replace(price_scale=0.5)
        item = item_with({"avg_price": 100, "minimum_buyout": 80, "item_count": 5,
                          "item_last_seen": "2026-07-01 11:45:09"})
        row, _ = transform.build_row(1, item, cfg, NOW)
        self.assertEqual(row, PriceRow(1, 50, 40))


class DeviationTest(unittest.TestCase):
    def test_flags_when_ratio_at_or_above_factor(self):
        row = PriceRow(35, 300, 300)
        dev = transform.compute_deviation(
            row, existing=(100, 100), item_name="Thing",
            stats={"item_count": 5, "item_last_seen": "2026-07-01 11:45:09"}, factor=1.5)
        self.assertIsNotNone(dev)
        self.assertEqual(dev.avg_ratio, 3.0)

    def test_not_flagged_when_below_factor(self):
        row = PriceRow(35, 140, 140)
        dev = transform.compute_deviation(
            row, existing=(100, 100), item_name="Thing",
            stats={"item_count": 5, "item_last_seen": "x"}, factor=1.5)
        self.assertIsNone(dev)

    def test_no_existing_never_flags(self):
        row = PriceRow(35, 999999, 999999)
        dev = transform.compute_deviation(
            row, existing=None, item_name="Thing",
            stats={"item_count": 5, "item_last_seen": "x"}, factor=1.5)
        self.assertIsNone(dev)

    def test_cheaper_direction_also_flags(self):
        row = PriceRow(35, 100, 100)  # existing 500 -> 5x cheaper
        dev = transform.compute_deviation(
            row, existing=(500, 500), item_name="Thing",
            stats={"item_count": 5, "item_last_seen": "x"}, factor=1.5)
        self.assertIsNotNone(dev)
        self.assertEqual(dev.avg_ratio, 5.0)


if __name__ == "__main__":
    unittest.main()
