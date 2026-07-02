import struct
import tempfile
import os
import unittest

import recipe_prices
import spelldbc

FIELD_COUNT = 234

def _record(fields):
    r = [0] * FIELD_COUNT
    for idx, val in fields.items():
        r[idx] = val
    return struct.pack("<%di" % FIELD_COUNT, *r)

def _dbc(records):
    body = b"".join(records)
    header = struct.pack("<4siiii", b"WDBC", len(records), FIELD_COUNT, FIELD_COUNT * 4, 0)
    return header + body

# spell 3961: creates item 4389 (Effect_1=24, EffectItemType_1=4389),
# reagents 3575 x1 and 10558 x1
GYRO = {0: 3961, 71: 24, 107: 4389, 52: 3575, 53: 10558, 60: 1, 61: 1}
# a non-create spell (Effect_1=6) -> must be ignored
NOISE = {0: 100, 71: 6, 107: 0}

class ParseTest(unittest.TestCase):
    def _write(self, records):
        fd, path = tempfile.mkstemp(suffix=".dbc")
        os.write(fd, _dbc(records)); os.close(fd)
        self.addCleanup(os.remove, path)
        return path

    def test_parses_create_item_recipe(self):
        path = self._write([_record(GYRO), _record(NOISE)])
        recipes = spelldbc.parse_spell_dbc(path)
        self.assertIn(4389, recipes)
        yld, reagents = recipes[4389]
        self.assertEqual(yld, 1)
        self.assertEqual(sorted(reagents), [(3575, 1), (10558, 1)])
        self.assertNotIn(0, recipes)  # noise spell created nothing

    def test_ignores_zero_count_reagents(self):
        rec = dict(GYRO); rec[54] = 9999; rec[62] = 0  # reagent slot 3 with count 0
        path = self._write([_record(rec)])
        recipes = spelldbc.parse_spell_dbc(path)
        self.assertEqual(sorted(recipes[4389][1]), [(3575, 1), (10558, 1)])

    def test_validate_ok_and_fail(self):
        recipes = {4389: (1, [(3575, 1), (10558, 1)])}
        # too few recipes -> raises
        with self.assertRaises(ValueError):
            spelldbc.validate(recipes)
        # enough recipes incl. the known one -> ok
        big = {i: (1, [(3575, 1)]) for i in range(1000)}
        big[4389] = (1, [(3575, 1), (10558, 1)])
        spelldbc.validate(big)  # no raise


class YieldExtractionTest(unittest.TestCase):
    """EffectBasePoints (field 80-82) drives the recipe yield instead of a
    hardcoded 1: yield = basePoints + 1 (WotLK 3.3.5a effect-value formula)."""

    def _write(self, records):
        fd, path = tempfile.mkstemp(suffix=".dbc")
        os.write(fd, _dbc(records)); os.close(fd)
        self.addCleanup(os.remove, path)
        return path

    def test_yield_one_recipe_unchanged(self):
        # basePoints unset (0) -> yield 1, same as before this fix.
        rec = {0: 4001, 71: 24, 107: 5001, 52: 3575, 60: 1}
        path = self._write([_record(rec)])
        recipes = spelldbc.parse_spell_dbc(path)
        yld, reagents = recipes[5001]
        self.assertEqual(yld, 1)
        self.assertEqual(reagents, [(3575, 1)])

    def test_fixed_multi_yield_recipe(self):
        # basePoints=4, dieSides unset (0, non-ambiguous) -> yield 5.
        rec = {0: 4002, 71: 24, 107: 5002, 52: 3575, 60: 2, 80: 4}
        path = self._write([_record(rec)])
        recipes = spelldbc.parse_spell_dbc(path)
        yld, reagents = recipes[5002]
        self.assertEqual(yld, 5)
        self.assertEqual(reagents, [(3575, 2)])

    def test_multi_yield_propagates_to_recursive_recipe_cost(self):
        # Tier-1: item 5002 crafted from 2x reagent 3575 (cc-priced 100), yield 5
        # -> per-unit cost = 200 / 5 = 40.
        rec = {0: 4002, 71: 24, 107: 5002, 52: 3575, 60: 2, 80: 4}
        path = self._write([_record(rec)])
        recipes = dict(spelldbc.parse_spell_dbc(path))
        # Tier-2: item 6000 crafted from 1x item 5002 (the tier-1 output).
        recipes[6000] = (1, [(5002, 1)])
        cfg = recipe_prices.DeriveConfig(margin_avg=1.0, margin_min=1.0, max_depth=10)
        cost = recipe_prices.resolve_price(6000, recipes, {3575: 100}, {}, cfg)
        self.assertEqual(cost, 40)


if __name__ == "__main__":
    unittest.main()
