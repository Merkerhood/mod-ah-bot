# Reagent-Cost Price Derivation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Derive AH prices for craftable no-price items from their reagents' costs and merge them into the override SQL.

**Architecture:** Parse `Spell.dbc` → `recipes.json` (item → yield + reagents). A recursive, memoized, cycle-safe resolver prices each craftable gap item from ChromieCraft reagent prices (falling back to vendor SellPrice), applies a margin, and merges derived rows into `priceOverride.sql`. Plus a derived-prices report.

**Tech Stack:** Python 3.12, stdlib only (`struct`, `json`, `csv`, `argparse`, `unittest`). Reuses `sqlio.py`.

## Global Constraints

- Zero third-party dependencies — Python 3.12 stdlib only.
- All modules in `tools/price-backfill/`; tests alongside, run via `python3 -m unittest discover` from that dir.
- `Spell.dbc` = WDBC: 20-byte header `magic(4s) record_count(i) field_count(i) record_size(i) string_block(i)`, then `record_count` records of `field_count` little-endian int32. 3.3.5a has `field_count == 234`, `record_size == 936`.
- Verified 0-based field offsets: Id=0; Effect_1/2/3 = 71/72/73 (CREATE_ITEM value 24, also accept 66); EffectItemType_1/2/3 = 107/108/109; Reagent_1..8 = 52..59; ReagentCount_1..8 = 60..67.
- Yield defaults to 1 (stack-yield refinement is out of scope for this plan).
- Resolution order for any item price: ChromieCraft override → recursive recipe derivation → vendor SellPrice → 0.
- Margin: `avgPrice = round(cost * 1.3)`, `minPrice = round(cost * 1.0)`.
- Derived rows only ADD items with no existing override row; never overwrite a ChromieCraft-priced item.

---

### Task 1: Spell.dbc recipe parser (`spelldbc.py`)

**Files:**
- Create: `tools/price-backfill/spelldbc.py`
- Test: `tools/price-backfill/test_spelldbc.py`

**Interfaces:**
- Produces:
  - `parse_spell_dbc(path: str) -> dict[int, tuple[int, list[tuple[int,int]]]]` — maps created-item id → `(yield, [(reagent_item, count), ...])`.
  - `validate(recipes: dict) -> None` — raises `ValueError` if the known recipe (item 4389 from 3575×1 and 10558×1) is absent/wrong or the recipe count is implausibly low (<500).
  - `CREATE_ITEM_EFFECTS = (24, 66)`.

- [ ] **Step 1: Write the failing test** (builds a minimal in-memory WDBC fixture at the real offsets)

```python
# tools/price-backfill/test_spelldbc.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd tools/price-backfill && python3 -m unittest test_spelldbc -v`
Expected: FAIL `ModuleNotFoundError: No module named 'spelldbc'`.

- [ ] **Step 3: Write minimal implementation**

```python
# tools/price-backfill/spelldbc.py
"""Parse WotLK 3.3.5a Spell.dbc into a created-item -> recipe map."""
import struct
from typing import Dict, List, Tuple

CREATE_ITEM_EFFECTS = (24, 66)
_ID = 0
_EFFECT = (71, 72, 73)
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
        for ei, ii in zip(_EFFECT, _EFFECT_ITEM):
            if fields[ei] in CREATE_ITEM_EFFECTS and fields[ii] > 0:
                out = fields[ii]
                # first recipe with reagents wins; keep deterministic
                recipes.setdefault(out, (1, reagents))
    return recipes


def validate(recipes: Dict[int, Tuple[int, List[Tuple[int, int]]]]) -> None:
    if len(recipes) < 500:
        raise ValueError("only %d recipes parsed; offsets/build likely wrong" % len(recipes))
    known = recipes.get(4389)
    if not known or sorted(known[1]) != [(3575, 1), (10558, 1)]:
        raise ValueError("known recipe 4389 missing/wrong: %r" % (known,))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd tools/price-backfill && python3 -m unittest test_spelldbc -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add tools/price-backfill/spelldbc.py tools/price-backfill/test_spelldbc.py
git commit -m "feat(derive): Spell.dbc recipe parser with validation gate"
```

---

### Task 2: Recursive price resolver (`recipe_prices.py`)

**Files:**
- Create: `tools/price-backfill/recipe_prices.py`
- Test: `tools/price-backfill/test_recipe_prices.py`

**Interfaces:**
- Consumes: recipe map from `spelldbc` (shape `{item: (yield, [(reagent,count)])}`).
- Produces:
  - `DeriveConfig = namedtuple("DeriveConfig", "margin_avg margin_min max_depth")`.
  - `resolve_price(item, recipes, overrides, vendor, cfg, memo=None, visiting=None) -> float` — price of one item by the resolution order (override → recipe → vendor → 0). `overrides` and `vendor` are `dict[int,int]`.
  - `price_source(item, recipes, overrides, vendor) -> str` — one of `cc`/`derived`/`vendor`/`none` for a direct reagent (no recursion; `derived` if it is a recipe output lacking an override).
  - `derive_rows(recipes, overrides, vendor, cfg) -> tuple[list[tuple[int,int,int]], list[dict]]` — `(rows, report)`. `rows` are `(item, avg, min)` for craftable items lacking an override with cost > 0. `report` dicts: `{item, cost, avg, min, reagent_sources}` where `reagent_sources` is a comma-joined `"itemid:source"` list.

- [ ] **Step 1: Write the failing test**

```python
# tools/price-backfill/test_recipe_prices.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd tools/price-backfill && python3 -m unittest test_recipe_prices -v`
Expected: FAIL `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# tools/price-backfill/recipe_prices.py
"""Derive craftable-item prices from reagent costs (cc -> recipe -> vendor -> 0)."""
from collections import namedtuple
from typing import Dict, List, Tuple

DeriveConfig = namedtuple("DeriveConfig", "margin_avg margin_min max_depth")


def resolve_price(item, recipes, overrides, vendor, cfg, memo=None, visiting=None):
    if memo is None:
        memo = {}
    if visiting is None:
        visiting = set()
    if item in overrides:
        return float(overrides[item])
    if item in memo:
        return memo[item]
    if item in recipes and item not in visiting and len(visiting) < cfg.max_depth:
        visiting.add(item)
        yld, reagents = recipes[item]
        total = 0.0
        for r, cnt in reagents:
            total += resolve_price(r, recipes, overrides, vendor, cfg, memo, visiting) * cnt
        visiting.discard(item)
        cost = total / (yld if yld > 0 else 1)
        memo[item] = cost
        return cost
    if vendor.get(item, 0) > 0:
        return float(vendor[item])
    return 0.0


def price_source(item, recipes, overrides, vendor):
    if item in overrides:
        return "cc"
    if item in recipes:
        return "derived"
    if vendor.get(item, 0) > 0:
        return "vendor"
    return "none"


def derive_rows(recipes, overrides, vendor, cfg):
    rows: List[Tuple[int, int, int]] = []
    report: List[dict] = []
    memo: Dict[int, float] = {}
    for item in sorted(recipes):
        if item in overrides:
            continue
        cost = resolve_price(item, recipes, overrides, vendor, cfg, memo)
        if cost <= 0:
            continue
        avg = round(cost * cfg.margin_avg)
        mn = round(cost * cfg.margin_min)
        if avg <= 0 or mn <= 0:
            continue
        rows.append((item, avg, mn))
        srcs = ",".join(
            "%d:%s" % (r, price_source(r, recipes, overrides, vendor))
            for r, _ in recipes[item][1])
        report.append({"item": item, "cost": round(cost), "avg": avg, "min": mn,
                       "reagent_sources": srcs})
    return rows, report
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd tools/price-backfill && python3 -m unittest test_recipe_prices -v`
Expected: PASS (10 tests).

- [ ] **Step 5: Commit**

```bash
git add tools/price-backfill/recipe_prices.py tools/price-backfill/test_recipe_prices.py
git commit -m "feat(derive): recursive reagent-cost resolver and row derivation"
```

---

### Task 3: CLI, merge, README (`derive.py`)

**Files:**
- Create: `tools/price-backfill/derive.py`
- Modify: `tools/price-backfill/.gitignore` (add `vendor.csv`, `recipes.json` stays committed)
- Modify: `tools/price-backfill/README.md` (add a "Reagent-cost derivation" section)

**Interfaces:**
- Consumes `spelldbc` output (via `recipes.json`), `recipe_prices.derive_rows`, `sqlio.parse_override_sql`/`write_override_sql`.
- Produces a CLI entrypoint.

**Behavior:**
- `recipes.json` format: JSON object mapping `str(item) -> [yield, [[reagent, count], ...]]`. A helper `load_recipes(path)` returns `{int: (yield, [(int,int)])}`.
- Reads vendor CSV (`entry,SellPrice` or tab/space separated; skip non-numeric lines) → `{item: sellprice}`.
- `overrides = {item: avg for item,(avg,min) in sqlio.parse_override_sql(existing_sql).items()}` for resolver input; keep the full `(avg,min)` map for the merged output.
- Merged output = existing rows (item,avg,min) + derived rows for items not already present; write via `sqlio.write_override_sql`.
- Report CSV: `item,cost,avg_price,min_price,reagent_sources`.

- [ ] **Step 1: Write the CLI**

```python
# tools/price-backfill/derive.py
"""Merge reagent-cost-derived prices for craftable gap items into the override SQL."""
import argparse
import csv
import json

import recipe_prices
import sqlio


def load_recipes(path):
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    return {int(k): (int(v[0]), [(int(a), int(b)) for a, b in v[1]]) for k, v in raw.items()}


def load_vendor(path):
    vendor = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            parts = line.replace("\t", " ").split()
            if len(parts) >= 2 and parts[0].isdigit() and parts[1].lstrip("-").isdigit():
                vendor[int(parts[0])] = int(parts[1])
    return vendor


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--recipes-json", required=True)
    p.add_argument("--existing-sql", required=True)
    p.add_argument("--vendor-csv", required=True)
    p.add_argument("--out-sql", default=None, help="defaults to --existing-sql")
    p.add_argument("--report", default="derived-prices.csv")
    p.add_argument("--margin-avg", type=float, default=1.3)
    p.add_argument("--margin-min", type=float, default=1.0)
    p.add_argument("--max-depth", type=int, default=10)
    args = p.parse_args()

    out_sql = args.out_sql or args.existing_sql
    cfg = recipe_prices.DeriveConfig(args.margin_avg, args.margin_min, args.max_depth)

    recipes = load_recipes(args.recipes_json)
    vendor = load_vendor(args.vendor_csv)
    existing = sqlio.parse_override_sql(args.existing_sql)  # {item:(avg,min)}
    overrides = {item: avg for item, (avg, _mn) in existing.items()}

    rows, report = recipe_prices.derive_rows(recipes, overrides, vendor, cfg)

    merged = [(item, avg, mn) for item, (avg, mn) in existing.items()]
    have = set(existing)
    for item, avg, mn in rows:
        if item not in have:
            merged.append((item, avg, mn))
    sqlio.write_override_sql(out_sql, merged)

    with open(args.report, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["item", "cost", "avg_price", "min_price", "reagent_sources"])
        for r in report:
            w.writerow([r["item"], r["cost"], r["avg"], r["min"], r["reagent_sources"]])

    print("existing=%d derived=%d merged=%d -> %s | report %s"
          % (len(existing), len(rows), len(merged), out_sql, args.report))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Integration smoke test with fixtures**

Run:
```bash
cd tools/price-backfill
printf '{"10":[1,[[5,2],[6,1]]]}' > /tmp/recipes_smoke.json
printf '6 40\n' > /tmp/vendor_smoke.csv   # reagent 6 vendor 40; reagent 5 will come from SQL
printf 'SET NAMES utf8mb4;\nSET FOREIGN_KEY_CHECKS = 0;\nINSERT INTO `mod_auctionhousebot_priceOverride` VALUES (5, 100, 90);\nSET FOREIGN_KEY_CHECKS = 1;\n' > /tmp/existing_smoke.sql
python3 derive.py --recipes-json /tmp/recipes_smoke.json --existing-sql /tmp/existing_smoke.sql --vendor-csv /tmp/vendor_smoke.csv --out-sql /tmp/merged_smoke.sql --report /tmp/report_smoke.csv
grep 'VALUES (10,' /tmp/merged_smoke.sql
cat /tmp/report_smoke.csv
```
Expected: prints `existing=1 derived=1 merged=2`; item 10 cost = 2×100 + 1×40 = 240 → `VALUES (10, 312, 240)` (avg 240×1.3=312, min 240); report row `10,240,312,240,5:cc,6:vendor`.

- [ ] **Step 3: Add gitignore + README section**

Append to `tools/price-backfill/.gitignore`:
```
vendor.csv
```
Append to `tools/price-backfill/README.md`:
```markdown
## Reagent-cost derivation (craftable gap items)

For items with no ChromieCraft price that are craftable, derive a price from
reagent costs.

1. Parse `Spell.dbc` into `recipes.json` (run on the realm host, which has the DBC):
   ```bash
   python3 -c "import json, spelldbc; r=spelldbc.parse_spell_dbc('/srv/wow/data/dbc/Spell.dbc'); spelldbc.validate(r); json.dump({str(k):[v[0],[list(t) for t in v[1]]] for k,v in r.items()}, open('recipes.json','w'))"
   ```
   Commit `recipes.json` (reusable; no runtime DBC dependency after this).
2. Export vendor prices: `mysql -N -B acore_world -e "SELECT entry, SellPrice FROM item_template;" > vendor.csv`.
3. Merge derived prices into the override SQL:
   ```bash
   python3 derive.py --recipes-json recipes.json \
     --existing-sql ../../data/sql/db-world/2023_11_16_mod_auctionhousebot_priceOverride.sql \
     --vendor-csv vendor.csv --report reports/derived-prices.csv
   ```
   Derived rows only ADD craftable items with no existing override. Review
   `reports/derived-prices.csv` (the `reagent_sources` column shows confidence).

Tuning: `--margin-avg` (1.3), `--margin-min` (1.0), `--max-depth` (10).
```

- [ ] **Step 4: Run the full suite**

Run: `cd tools/price-backfill && python3 -m unittest discover -v`
Expected: PASS (all spelldbc + recipe_prices + existing backfill tests).

- [ ] **Step 5: Commit**

```bash
git add tools/price-backfill/derive.py tools/price-backfill/.gitignore tools/price-backfill/README.md
git commit -m "feat(derive): CLI to merge reagent-derived prices + docs"
```

---

## Post-build execution (not a task; run after the three tasks pass review)

1. Generate `recipes.json` on the realm host (`ssh testcore`), copy it into `tools/price-backfill/`, commit it.
2. Run `derive.py` against the **ChromieCraft-refreshed** `priceOverride.sql` (from the `data/chromiecraft-price-refresh` branch / PR #13) as `--existing-sql`, writing the merged SQL and `reports/derived-prices-2026-07-01.csv`.
3. Sanity-check the merged SQL via `sqlio` (no dup ids, no malformed rows), review the derived-prices report, commit the merged SQL + report + `recipes.json`, and open a PR.
