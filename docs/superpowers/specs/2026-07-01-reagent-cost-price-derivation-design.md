# Reagent-Cost Price Derivation — Design

Date: 2026-07-01
Status: Approved, pending implementation plan

## Problem

The ChromieCraft backfill priced ~10,398 items, but ~10,115 have no ChromieCraft
auction data and fall back to the fork's fixed C++ default prices. Many of those are
craftable, and their reagents are mostly already priced (from the ChromieCraft run).
We can derive a realistic sale price for a craftable item from the cost of its reagents.

## Goal

For every craftable item with no ChromieCraft price, derive a sale price from reagent
costs; persist the recipe graph and derived prices as a reusable dataset; and merge the
derived prices into the AH-bot `mod_auctionhousebot_priceOverride` table.

## Data sources

1. **Recipes — `Spell.dbc`** (on the realm host at `/srv/wow/data/dbc/Spell.dbc`; WDBC,
   49,839 records, 234 fields, 936-byte records). Empirically verified 0-based field
   offsets (validated against spell 3961 → creates 4389 from Iron Bar 3575 ×1 + 10558 ×1):
   - `Id`: 0
   - `Effect_1/2/3`: 71 / 72 / 73 — CREATE_ITEM effect value = 24 (also accept 66,
     CREATE_ITEM_2)
   - `EffectItemType_1/2/3`: 107 / 108 / 109 — the created item id
   - `Reagent_1..8`: 52..59
   - `ReagentCount_1..8`: 60..67
   - Yield (items produced): default 1. Multi-yield crafts (bandages, ammo, some
     consumables) produce a stack; the `EffectBasePoints`/`EffectDieSides` offset for the
     create slot must be pinned and validated against a known stack-producing recipe
     during implementation. Until validated, yield defaults to 1 and the derived-prices
     report flags suspiciously high costs for review.

   The parser MUST validate against known recipes before use (spell 3961 above, plus 2–3
   more) and abort on mismatch. Because the DBC lives on the realm host, it is parsed
   once (on that host) into a committed `recipes.json`; the pricing tool consumes
   `recipes.json` and has no runtime DBC dependency.

2. **Reagent prices** — parse the current `priceOverride.sql` via `sqlio.parse_override_sql`
   → `{item: avgPrice}`. This is the ChromieCraft market price for the ~10,398 priced items.

3. **Vendor prices** — `item_template.SellPrice`, exported from the world DB to a CSV
   (`entry, SellPrice`), used as the last-resort reagent price.

## Algorithm

- Build `recipes = {output_item: (yield, [(reagent_item, count), ...])}` from `recipes.json`.
- Craftable gap items = recipe outputs that have no `priceOverride` row.
- `resolve_price(item)` with memoization and a visiting-set for cycle detection:
  - has ChromieCraft override → its `avgPrice`
  - else is a recipe output → `cost = sum(resolve_price(r) * count) / yield` (derive, memoize)
  - else vendor `SellPrice > 0` → `SellPrice`
  - else → 0 (unpriceable; contributes 0 to a parent's cost)
  - a recipe cycle (item already on the visiting stack) → break to vendor price or 0
- For each craftable gap item: `cost = resolve_price(item)`; if `cost <= 0` skip (leave to
  C++ default). Else `avgPrice = round(cost * 1.3)`, `minPrice = round(cost)`.

## Decisions (locked)

| Decision | Choice |
|---|---|
| Margin | `avgPrice = round(cost * 1.3)`, `minPrice = round(cost)` |
| Storage | Commit `recipes.json` + a derived-prices report, AND merge derived rows into `priceOverride.sql` |
| Unpriceable reagent | Fall back to `item_template.SellPrice`; if that is 0, contributes 0 |

## Storage / integration

- Commit `tools/price-backfill/data/recipes.json` — the parsed recipe graph, the reusable
  asset (so `Spell.dbc` never needs re-parsing).
- Emit `tools/price-backfill/reports/derived-prices-2026-07-01.csv`:
  `item, item_name, cost, avg_price, min_price, reagents_priced_via` (how each reagent was
  priced: cc / derived / vendor / none — the confidence signal).
- Merge: produce an updated `priceOverride.sql` = existing rows **plus** derived rows for
  craftable gap items. Derived rows only ADD items with no existing row; never overwrite a
  ChromieCraft-priced item. Reuse `sqlio.write_override_sql`.

## Components (all in `tools/price-backfill/`)

- `spelldbc.py` — `parse_spell_dbc(path) -> {out_item: (yield, [(reagent,count)])}` and a
  `validate(recipes)` gate against known recipes.
- `export-recipes` step — a documented command that runs `spelldbc.parse_spell_dbc` on the
  realm host and writes `recipes.json` locally (the DBC stays on the host).
- `recipe_prices.py` — `resolve_price` (memo + cycle detection) and
  `derive_rows(recipes, overrides, vendor, cfg) -> (rows, report)`.
- `derive.py` — CLI: read `recipes.json` + `priceOverride.sql` + `vendor.csv`, write the
  merged SQL + the derived-prices report.
- Reuses `sqlio.py`.

## Config / CLI

`--recipes-json`, `--existing-sql`, `--vendor-csv`, `--out-sql` (default = existing-sql),
`--report`, `--margin-avg` (1.3), `--margin-min` (1.0), `--max-depth` (10).

## Error handling

- Parser validation gate: abort if known recipes don't parse correctly (guards against
  wrong offsets / wrong DBC build).
- Cycle detection in `resolve_price` (visiting-set); a cycle breaks to vendor/0.
- Unpriceable reagent → vendor SellPrice → 0.
- Derived cost ≤ 0 → skip the item (no row emitted).

## Testing

- `spelldbc`: parse a fixture (or the real) `Spell.dbc`; assert known recipes parse with
  correct reagents, counts, and created item (spell 3961 → 4389; +2 more).
- `recipe_prices`: unit tests — single-tier derivation, multi-tier recursion, recipe cycle,
  vendor fallback, unpriceable → 0, yield division, margin math, and that CC-priced items
  are never overwritten.
- Integration smoke: run on a small recipe subset; confirm the merged SQL adds only new
  rows and is well-formed (via `sqlio`).

## Out of scope

- The "lifelike AH item-count override" follow-up (separate plan).
- Re-scraping wowauctions.net (craftable gap items 404 there; no recipe data).
- Changing the C++ bot; this only produces override data.
