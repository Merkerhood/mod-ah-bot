"""Which items the calibrated fallback is allowed to price."""
import unittest

import calibrate
import fallback


def item(entry, cls=4, sub=1, quality=2, buy=100, sell=10, vendor=0):
    return fallback.Row(calibrate.Item(entry, cls, sub, quality, buy, sell),
                        vendor)


BASE = [item(1), item(2), item(3)]


class GapSelectionTest(unittest.TestCase):
    def _gap(self, rows, overrides=(), disabled=(), exclude_classes=(9,)):
        return [it.entry for it in fallback.select_gap(
            rows, set(overrides), set(disabled), set(exclude_classes))]

    def test_item_with_an_existing_override_is_left_alone(self):
        self.assertEqual(self._gap(BASE, overrides=[2]), [1, 3])

    def test_item_the_pool_builder_never_lists_is_skipped(self):
        # InitializeBins() skips SellPrice = 0 items, so an override row for
        # one is dead weight.
        rows = BASE + [item(4, sell=0)]
        self.assertEqual(self._gap(rows), [1, 2, 3])

    def test_freely_vendor_sold_item_is_skipped(self):
        rows = BASE + [item(4, vendor=1)]
        self.assertEqual(self._gap(rows), [1, 2, 3])

    def test_grey_quality_is_skipped(self):
        rows = BASE + [item(4, quality=0)]
        self.assertEqual(self._gap(rows), [1, 2, 3])

    def test_excluded_class_is_skipped(self):
        # Recipes need their own pricing basis, not a class multiplier.
        rows = BASE + [item(4, cls=9)]
        self.assertEqual(self._gap(rows), [1, 2, 3])

    def test_disabled_item_is_skipped(self):
        # Test, deprecated and trash items never reach the auction house.
        self.assertEqual(self._gap(BASE, disabled=[2]), [1, 3])


class FitPopulationTest(unittest.TestCase):
    def test_disabled_items_stay_out_of_the_fit(self):
        # A disabled item's scraped price is not a price anyone can act on, so
        # it must not shape the multiplier either.
        rows = [item(i) for i in range(1, 6)]
        pop = fallback.select_fit(rows, {1, 2, 3, 4, 5}, disabled={5})
        self.assertEqual([it.entry for it in pop], [1, 2, 3, 4])

    def test_only_items_with_a_market_price_are_fitted(self):
        rows = [item(i) for i in range(1, 6)]
        pop = fallback.select_fit(rows, {2, 4}, disabled=set())
        self.assertEqual([it.entry for it in pop], [2, 4])

    def test_previously_derived_rows_stay_out_of_the_fit(self):
        # A row this tool wrote on an earlier run is not a market observation.
        # Re-running as real data accumulates must not fit on its own output.
        rows = [item(i) for i in range(1, 6)]
        pop = fallback.select_fit(rows, {1, 2, 3, 4, 5}, disabled=set(),
                                  derived={3, 5})
        self.assertEqual([it.entry for it in pop], [1, 2, 4])


class PriorReportTest(unittest.TestCase):
    def test_reads_derived_item_ids_from_a_prior_report(self):
        import io
        csv_text = ("item,tier,multiplier,avg,min,capped,floored\n"
                    "101,class-quality,2.0,200,100,0,0\n"
                    "102,no-anchor,0,0,0,0,0\n")
        self.assertEqual(fallback.read_derived(io.StringIO(csv_text)), {101})


if __name__ == "__main__":
    unittest.main()
