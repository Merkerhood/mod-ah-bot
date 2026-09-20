import math
import unittest

import recipe_rank_prices as rrp


def sample(item=1, rank=200, skill=165, quality=2, sell=100, product=0):
    return rrp.Sample(item=item, rank=rank, skill=skill, quality=quality,
                      sell_price=sell, product_price=product)


class LeastSquaresTest(unittest.TestCase):
    def test_recovers_exact_coefficients(self):
        # y = 3 + 2*x over three points determines [3, 2] exactly
        X = [[1.0, 0.0], [1.0, 1.0], [1.0, 2.0]]
        y = [3.0, 5.0, 7.0]
        b = rrp.solve_least_squares(X, y)
        self.assertAlmostEqual(b[0], 3.0, places=6)
        self.assertAlmostEqual(b[1], 2.0, places=6)

    def test_singular_system_does_not_raise(self):
        # duplicated column: no unique solution, must still return a vector
        X = [[1.0, 1.0], [1.0, 1.0]]
        y = [2.0, 2.0]
        self.assertEqual(len(rrp.solve_least_squares(X, y)), 2)


class FeatureTest(unittest.TestCase):
    def test_missing_product_price_contributes_nothing(self):
        skills = [165, 197]
        with_product = rrp.features(sample(product=1000), skills)
        without = rrp.features(sample(product=0), skills)
        # the log term is zeroed and the indicator distinguishes the two cases
        self.assertEqual(without[rrp.IDX_LOG_PRODUCT], 0.0)
        self.assertEqual(without[rrp.IDX_HAS_PRODUCT], 0.0)
        self.assertAlmostEqual(with_product[rrp.IDX_LOG_PRODUCT], math.log(1000))
        self.assertEqual(with_product[rrp.IDX_HAS_PRODUCT], 1.0)

    def test_profession_is_one_hot(self):
        skills = [165, 197, 333]
        f = rrp.features(sample(skill=197), skills)
        tail = f[-len(skills):]
        self.assertEqual(tail, [0.0, 1.0, 0.0])

    def test_every_quality_the_exporter_admits_gets_its_own_slot(self):
        # the class-9 export filters Quality <= 5, so a legendary recipe must
        # not land in the same all-zero slot as an unrecognised quality
        seen = [rrp.features(sample(quality=q), [])[6:] for q in range(6)]
        for f in seen:
            self.assertEqual(sum(f), 1.0)
        self.assertEqual(len({tuple(f) for f in seen}), 6)

    def test_unknown_profession_gets_no_one_hot(self):
        f = rrp.features(sample(skill=999), [165, 197])
        self.assertEqual(f[-2:], [0.0, 0.0])


class FitPredictTest(unittest.TestCase):
    def test_reproduces_a_log_linear_rank_relationship(self):
        # price doubles roughly every 100 skill points, one profession only
        samples = [(sample(item=i, rank=r, product=0), math.exp(5 + 0.7 * r / 100.0))
                   for i, r in enumerate(range(25, 450, 25))]
        model = rrp.fit(samples)
        got = rrp.predict(model, sample(rank=300))
        self.assertAlmostEqual(got, math.exp(5 + 0.7 * 3.0), delta=math.exp(5 + 0.7 * 3.0) * 0.02)

    def test_taught_item_value_raises_the_prediction(self):
        # identical rank, price tracks the taught item's value
        samples = []
        for i, p in enumerate((1000, 4000, 16000, 64000)):
            samples.append((sample(item=i, rank=200, product=p), p * 0.6))
        model = rrp.fit(samples)
        cheap = rrp.predict(model, sample(rank=200, product=2000))
        rich = rrp.predict(model, sample(rank=200, product=32000))
        self.assertGreater(rich, cheap)


class BandTest(unittest.TestCase):
    def test_band_is_taken_from_the_matching_rank_bucket(self):
        samples = [(sample(item=i, rank=110), 100.0) for i in range(10)]
        samples += [(sample(item=100 + i, rank=310), 900000.0) for i in range(10)]
        bands = rrp.rank_bands(samples)
        self.assertEqual(bands.for_rank(110), bands.for_rank(124))
        self.assertNotEqual(bands.for_rank(110), bands.for_rank(310))

    def test_thin_bucket_falls_back_to_the_global_band(self):
        samples = [(sample(item=i, rank=110), 100.0) for i in range(10)]
        samples += [(sample(item=99, rank=310), 900000.0)]  # single observation
        bands = rrp.rank_bands(samples, min_bucket=5)
        self.assertEqual(bands.for_rank(310), bands.global_band)


class PriceRowsTest(unittest.TestCase):
    def setUp(self):
        self.samples = []
        for i, r in enumerate(range(25, 450, 25)):
            self.samples.append((sample(item=1000 + i, rank=r), math.exp(5 + 0.7 * r / 100.0)))
        self.model = rrp.fit(self.samples)
        self.bands = rrp.rank_bands(self.samples, min_bucket=1)

    def test_skips_an_item_that_already_has_an_override(self):
        rows, _ = rrp.price_rows(self.model, self.bands, [sample(item=7)], {7: (1, 1)})
        self.assertEqual(rows, [])

    def test_skips_an_item_with_no_skill_rank(self):
        rows, _ = rrp.price_rows(self.model, self.bands, [sample(item=7, rank=0)], {})
        self.assertEqual(rows, [])

    def test_never_prices_below_the_items_vendor_value(self):
        # a rank-25 recipe the model prices low, but the vendor pays a lot for it
        rows, report = rrp.price_rows(
            self.model, self.bands, [sample(item=7, rank=25, sell=5_000_000)], {})
        self.assertEqual(len(rows), 1)
        self.assertGreaterEqual(rows[0][1], 5_000_000)
        self.assertEqual(report[0]["clamp"], "vendor-floor")

    def test_clamps_a_prediction_above_the_bucket_ceiling(self):
        bands = rrp.Bands({}, (100, 1000))
        rows, report = rrp.price_rows(self.model, bands, [sample(item=7, rank=440, sell=1)], {})
        self.assertEqual(rows[0][1], 1000)
        self.assertEqual(report[0]["clamp"], "ceiling")

    def test_clamps_a_prediction_below_the_bucket_floor(self):
        bands = rrp.Bands({}, (500_000, 900_000))
        rows, report = rrp.price_rows(self.model, bands, [sample(item=7, rank=25, sell=1)], {})
        self.assertEqual(rows[0][1], 500_000)
        self.assertEqual(report[0]["clamp"], "floor")

    def test_min_price_is_the_configured_fraction_of_avg(self):
        rows, _ = rrp.price_rows(self.model, self.bands, [sample(item=7, sell=1)], {},
                                 min_ratio=0.5)
        self.assertEqual(rows[1 - 1][2], round(rows[0][1] * 0.5))

    def test_rows_are_sorted_by_item_id(self):
        targets = [sample(item=30, sell=1), sample(item=10, sell=1), sample(item=20, sell=1)]
        rows, _ = rrp.price_rows(self.model, self.bands, targets, {})
        self.assertEqual([r[0] for r in rows], [10, 20, 30])


if __name__ == "__main__":
    unittest.main()
