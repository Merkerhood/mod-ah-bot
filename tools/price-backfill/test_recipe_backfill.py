import unittest

import recipe_backfill as rb


def row(entry, rank=200, skill=165, quality=2, sell=100, teach=0,
        obtainable=1, free_vendor=0):
    return {"entry": entry, "rank": rank, "skill": skill, "quality": quality,
            "sell_price": sell, "teach_spell": teach, "obtainable": obtainable,
            "free_vendor": free_vendor}


class BuildTest(unittest.TestCase):
    def test_a_priced_recipe_becomes_a_training_sample(self):
        train, targets = rb.build([row(7)], {}, {7: (5000, 4000)})
        self.assertEqual(targets, [])
        self.assertEqual(len(train), 1)
        self.assertEqual(train[0][0].item, 7)
        self.assertEqual(train[0][1], 5000.0)  # avgPrice is the label

    def test_an_unpriced_obtainable_recipe_becomes_a_target(self):
        train, targets = rb.build([row(7)], {}, {})
        self.assertEqual(train, [])
        self.assertEqual([t.item for t in targets], [7])

    def test_an_unobtainable_recipe_is_neither(self):
        train, targets = rb.build([row(7, obtainable=0)], {}, {})
        self.assertEqual((train, targets), ([], []))

    def test_a_freely_vendor_sold_recipe_is_neither(self):
        # the vendor price ceiling handles these; see README
        train, targets = rb.build([row(7, free_vendor=1)], {}, {})
        self.assertEqual((train, targets), ([], []))

    def test_a_recipe_with_no_skill_rank_is_neither(self):
        train, targets = rb.build([row(7, rank=0)], {}, {})
        self.assertEqual((train, targets), ([], []))

    def test_taught_item_price_is_looked_up_through_the_craft_spell(self):
        _, targets = rb.build([row(7, teach=3961)], {3961: 4389}, {4389: (2500, 2000)})
        self.assertEqual(targets[0].product_price, 2500)

    def test_unpriced_taught_item_leaves_the_product_price_at_zero(self):
        _, targets = rb.build([row(7, teach=3961)], {3961: 4389}, {})
        self.assertEqual(targets[0].product_price, 0)

    def test_a_recipe_teaching_nothing_leaves_the_product_price_at_zero(self):
        _, targets = rb.build([row(7, teach=0)], {}, {})
        self.assertEqual(targets[0].product_price, 0)

    def test_free_vendor_recipe_that_is_already_priced_still_trains(self):
        # it stays out of the target set, but its market price is real data
        train, targets = rb.build([row(7, free_vendor=1)], {}, {7: (5000, 4000)})
        self.assertEqual(len(train), 1)
        self.assertEqual(targets, [])


if __name__ == "__main__":
    unittest.main()
