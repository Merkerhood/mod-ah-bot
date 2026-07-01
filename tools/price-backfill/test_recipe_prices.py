import unittest
import recipe_prices as rp

CFG = rp.DeriveConfig(margin_avg=1.3, margin_min=1.0, max_depth=10)

class ResolveTest(unittest.TestCase):
    def test_override_wins(self):
        r = rp.resolve_price(5, {}, {5: 100}, {}, CFG)
        self.assertEqual(r, 100)

    def test_single_tier_derivation(self):
        # item 10 = 2x reagent 5 (cc-priced 100) + 1x reagent 6 (cc 50), yield 1
        recipes = {10: (1, [(5, 2), (6, 1)])}
        r = rp.resolve_price(10, recipes, {5: 100, 6: 50}, {}, CFG)
        self.assertEqual(r, 250)

    def test_yield_divides_cost(self):
        recipes = {10: (5, [(5, 10)])}  # 10x reagent 5 (100) / yield 5 = 200
        self.assertEqual(rp.resolve_price(10, recipes, {5: 100}, {}, CFG), 200)

    def test_multi_tier_recursion(self):
        # 20 crafted from 2x item 10; item 10 crafted from 3x reagent 5 (cc 100)
        recipes = {20: (1, [(10, 2)]), 10: (1, [(5, 3)])}
        self.assertEqual(rp.resolve_price(20, recipes, {5: 100}, {}, CFG), 600)

    def test_vendor_fallback_for_unpriced_reagent(self):
        recipes = {10: (1, [(5, 1)])}  # reagent 5 has no cc price, vendor=40
        self.assertEqual(rp.resolve_price(10, recipes, {}, {5: 40}, CFG), 40)

    def test_unpriceable_reagent_is_zero(self):
        recipes = {10: (1, [(5, 1)])}  # no cc, no vendor
        self.assertEqual(rp.resolve_price(10, recipes, {}, {}, CFG), 0)

    def test_cycle_is_safe(self):
        recipes = {10: (1, [(20, 1)]), 20: (1, [(10, 1)])}  # cycle, no base price
        # must terminate and return 0 (nothing priceable), not recurse forever
        self.assertEqual(rp.resolve_price(10, recipes, {}, {}, CFG), 0)

class DeriveRowsTest(unittest.TestCase):
    def test_emits_only_gap_craftables_with_margin(self):
        recipes = {10: (1, [(5, 2)])}      # craftable, no override -> derive
        overrides = {5: 100, 99: 7}        # 5 is a reagent; 99 unrelated
        rows, report = rp.derive_rows(recipes, overrides, {}, CFG)
        self.assertEqual(rows, [(10, 260, 200)])   # cost 200 -> avg *1.3, min *1.0
        self.assertEqual(report[0]["item"], 10)
        self.assertIn("5:cc", report[0]["reagent_sources"])

    def test_skips_craftable_that_already_has_override(self):
        recipes = {10: (1, [(5, 2)])}
        rows, _ = rp.derive_rows(recipes, {10: 999, 5: 100}, {}, CFG)
        self.assertEqual(rows, [])

    def test_skips_zero_cost(self):
        recipes = {10: (1, [(5, 1)])}  # reagent unpriceable -> cost 0 -> skip
        rows, _ = rp.derive_rows(recipes, {}, {}, CFG)
        self.assertEqual(rows, [])

if __name__ == "__main__":
    unittest.main()
