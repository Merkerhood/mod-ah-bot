"""Calibrated BuyPrice fallback: bucket fitting, tier fallback, cap and floor."""
import unittest

import calibrate


def item(entry, cls=4, sub=1, quality=2, buy=100, sell=10):
    return calibrate.Item(entry, cls, sub, quality, buy, sell)


def market(entry, avg, mn=None):
    return calibrate.Market(entry, avg, avg // 2 if mn is None else mn)


CFG = calibrate.Config(min_bucket=4, cap_percentile=0.99)


class BucketTierTest(unittest.TestCase):
    def test_own_class_subclass_quality_bucket_wins(self):
        # Four cloth-armour greens priced at 2x BuyPrice, and a much larger
        # leather-armour green population at 10x. The cloth item must fit
        # against cloth, not against the class-wide mix.
        items = [item(i, cls=4, sub=1) for i in range(1, 5)]
        items += [item(i, cls=4, sub=2) for i in range(10, 30)]
        mk = [market(i, 200) for i in range(1, 5)]
        mk += [market(i, 1000) for i in range(10, 30)]

        cal = calibrate.fit(items, mk, CFG)
        (mult, _mn), tier = cal.multiplier(item(999, cls=4, sub=1))

        self.assertAlmostEqual(mult, 2.0)
        self.assertEqual(tier, "class-subclass-quality")

    def test_sparse_subclass_falls_back_to_class_quality(self):
        # Only two cloth greens: below min_bucket, so the cloth gap item must
        # borrow the class-wide green multiplier instead.
        items = [item(i, cls=4, sub=1) for i in (1, 2)]
        items += [item(i, cls=4, sub=2) for i in range(10, 20)]
        mk = [market(i, 200) for i in (1, 2)]
        mk += [market(i, 1000) for i in range(10, 20)]

        cal = calibrate.fit(items, mk, CFG)
        (mult, _mn), tier = cal.multiplier(item(999, cls=4, sub=1))

        self.assertEqual(tier, "class-quality")
        self.assertGreater(mult, 2.0)

    def test_sparse_class_falls_back_to_quality_wide(self):
        # Nothing in class 6 at all; the green population lives in class 4.
        items = [item(i, cls=4, sub=2, quality=2) for i in range(1, 10)]
        mk = [market(i, 500) for i in range(1, 10)]

        cal = calibrate.fit(items, mk, CFG)
        (mult, _mn), tier = cal.multiplier(item(999, cls=6, sub=0, quality=2))

        self.assertEqual(tier, "quality")
        self.assertAlmostEqual(mult, 5.0)

    def test_unknown_quality_falls_back_to_global(self):
        items = [item(i, quality=2) for i in range(1, 10)]
        mk = [market(i, 300) for i in range(1, 10)]

        cal = calibrate.fit(items, mk, CFG)
        (mult, _mn), tier = cal.multiplier(item(999, quality=4))

        self.assertEqual(tier, "global")
        self.assertAlmostEqual(mult, 3.0)


class MedianTest(unittest.TestCase):
    def test_single_listing_troll_price_does_not_move_the_multiplier(self):
        # One item listed at 1000x drags a mean far off; the median holds.
        items = [item(i) for i in range(1, 6)]
        mk = [market(i, 200) for i in range(1, 5)] + [market(5, 100000)]

        cal = calibrate.fit(items, mk, CFG)
        (mult, _mn), _ = cal.multiplier(item(999))

        self.assertAlmostEqual(mult, 2.0)


class CapAndFloorTest(unittest.TestCase):
    def test_price_capped_at_bucket_market_percentile(self):
        # The bucket never trades above 400c, so a gap item with a huge
        # BuyPrice cannot be priced above what the bucket actually fetches.
        items = [item(i, buy=100) for i in range(1, 6)]
        mk = [market(i, 200) for i in range(1, 5)] + [market(5, 400)]
        cal = calibrate.fit(items, mk, calibrate.Config(min_bucket=4,
                                                        cap_percentile=1.0))

        rows, report = calibrate.derive_rows([item(999, buy=1000000)], cal)

        self.assertEqual(rows[0][1], 400)
        self.assertTrue(report[0]["capped"])

    def test_price_floored_at_sell_price(self):
        # A bucket multiplier below 1 would price the item under the vendor's
        # own buyback, which no seller would ever accept.
        items = [item(i, buy=1000) for i in range(1, 6)]
        mk = [market(i, 100) for i in range(1, 6)]
        cal = calibrate.fit(items, mk, CFG)

        rows, report = calibrate.derive_rows(
            [item(999, buy=1000, sell=900)], cal)

        self.assertEqual(rows[0][1], 900)
        self.assertTrue(report[0]["floored"])

    def test_floor_lifts_the_min_price_too(self):
        # The floor exists because no seller lists below vendor buyback, and
        # minPrice is the buyout the AH bot actually asks.
        items = [item(i, buy=1000) for i in range(1, 6)]
        mk = [market(i, 100, mn=50) for i in range(1, 6)]
        cal = calibrate.fit(items, mk, CFG)

        rows, _ = calibrate.derive_rows([item(999, buy=1000, sell=900)], cal)

        _entry, avg, mn = rows[0]
        self.assertEqual(avg, 900)
        self.assertEqual(mn, 900)

    def test_min_price_never_exceeds_avg_price(self):
        # A bucket whose recorded minimum buyout sits above its average price
        # (bad scrape data) must not emit a row the AH bot would price upside
        # down.
        items = [item(i, buy=100) for i in range(1, 6)]
        mk = [market(i, 200, mn=300) for i in range(1, 6)]
        cal = calibrate.fit(items, mk, CFG)

        rows, _ = calibrate.derive_rows([item(999, buy=100, sell=1)], cal)

        _entry, avg, mn = rows[0]
        self.assertLessEqual(mn, avg)


class DegenerateInputTest(unittest.TestCase):
    def test_empty_bucket_never_satisfies_the_minimum(self):
        # A min_bucket of 0 would otherwise make every empty bucket "big
        # enough" and take the median of nothing.
        cal = calibrate.fit([], [], calibrate.Config(min_bucket=0,
                                                     cap_percentile=0.99))

        (mult, _mn), tier = cal.multiplier(item(999))

        self.assertEqual(tier, "global")
        self.assertEqual(mult, 1.0)
        self.assertIsNone(cal.cap(item(999)))


class EligibilityTest(unittest.TestCase):
    def test_item_without_buy_price_yields_no_row(self):
        items = [item(i) for i in range(1, 6)]
        mk = [market(i, 200) for i in range(1, 6)]
        cal = calibrate.fit(items, mk, CFG)

        rows, report = calibrate.derive_rows([item(999, buy=0)], cal)

        self.assertEqual(rows, [])
        self.assertEqual(report[0]["tier"], "no-anchor")

    def test_market_row_without_matching_item_is_ignored(self):
        # The override table outlives item_template edits; a stale row must
        # not crash the fit or pollute a bucket.
        items = [item(i) for i in range(1, 6)]
        mk = [market(i, 200) for i in range(1, 6)] + [market(77777, 9999)]

        cal = calibrate.fit(items, mk, CFG)
        (mult, _mn), _ = cal.multiplier(item(999))

        self.assertAlmostEqual(mult, 2.0)

    def test_fitting_population_skips_items_without_buy_price(self):
        # A zero BuyPrice yields no ratio at all; including it as 0 would drag
        # the median down.
        items = [item(i) for i in range(1, 6)] + [item(6, buy=0)]
        mk = [market(i, 200) for i in range(1, 6)] + [market(6, 5000)]

        cal = calibrate.fit(items, mk, CFG)
        (mult, _mn), _ = cal.multiplier(item(999))

        self.assertAlmostEqual(mult, 2.0)


if __name__ == "__main__":
    unittest.main()
