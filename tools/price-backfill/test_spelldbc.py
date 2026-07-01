import struct
import tempfile
import os
import unittest

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

if __name__ == "__main__":
    unittest.main()
