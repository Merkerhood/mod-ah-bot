"""Parse WotLK 3.3.5a Spell.dbc into a created-item -> recipe map."""
import struct
from typing import Dict, List, Tuple

CREATE_ITEM_EFFECTS = (24, 66)
_ID = 0
_EFFECT = (71, 72, 73)
_EFFECT_DIE_SIDES = (74, 75, 76)
_EFFECT_BASE_POINTS = (80, 81, 82)
_EFFECT_ITEM = (107, 108, 109)
_REAGENT = range(52, 60)
_REAGENT_COUNT = range(60, 68)


def parse_spell_dbc(path: str) -> Dict[int, Tuple[int, List[Tuple[int, int]]]]:
    with open(path, "rb") as fh:
        blob = fh.read()
    magic, rc, fc, rs, _sb = struct.unpack("<4siiii", blob[:20])
    if magic != b"WDBC":
        raise ValueError("not a WDBC file: %r" % magic)
    if rs != fc * 4:
        raise ValueError("record_size %d != field_count*4 %d" % (rs, fc * 4))
    recipes: Dict[int, Tuple[int, List[Tuple[int, int]]]] = {}
    off = 20
    for _ in range(rc):
        fields = struct.unpack("<%di" % fc, blob[off:off + rs])
        off += rs
        reagents = []
        for ri, ci in zip(_REAGENT, _REAGENT_COUNT):
            item, cnt = fields[ri], fields[ci]
            if item > 0 and cnt > 0:
                reagents.append((item, cnt))
        if not reagents:
            continue
        for ei, ii, bi in zip(_EFFECT, _EFFECT_ITEM, _EFFECT_BASE_POINTS):
            if fields[ei] in CREATE_ITEM_EFFECTS and fields[ii] > 0:
                out = fields[ii]
                # In 3.3.5a DBC data the effect's actual value is EffectBasePoints + 1
                # plus a random roll of up to EffectDieSides (see _EFFECT_DIE_SIDES).
                # EffectDieSides > 1 means the true yield varies at cast time and can't
                # be determined from static DBC data -- we use the minimum determinable
                # yield (basePoints + 1) as a conservative floor rather than guessing
                # the roll outcome.
                yield_amt = fields[bi] + 1
                # first recipe with reagents wins; keep deterministic
                recipes.setdefault(out, (yield_amt, reagents))
    return recipes


def validate(recipes: Dict[int, Tuple[int, List[Tuple[int, int]]]]) -> None:
    if len(recipes) < 500:
        raise ValueError("only %d recipes parsed; offsets/build likely wrong" % len(recipes))
    known = recipes.get(4389)
    if not known or sorted(known[1]) != [(3575, 1), (10558, 1)]:
        raise ValueError("known recipe 4389 missing/wrong: %r" % (known,))
