# ChromieCraft Price Backfill Tool — Design

Date: 2026-07-01
Status: Approved, pending implementation plan

## Problem

The AH-bot uses `mod_auctionhousebot_priceOverride` (`item`, `avgPrice`, `minPrice`) to drive
buy/sell base prices for auction listings. The C++ (`src/AuctionHouseBot.cpp`) loads every row
once into `itemPriceOverrides`, prioritizes listing items that have an override, and falls back
to fixed default prices when an item has no row.

The shipped SQL file (`data/sql/db-world/2023_11_16_mod_auctionhousebot_priceOverride.sql`)
carries ~7,236 hand-imported rows. WotLK has ~35–45k item IDs, so most AH-tradeable items are
uncovered and get generic default pricing. There is no importer in the repo — the current rows
were produced by hand.

## Goal

A standalone one-time tool that regenerates the price-override SQL from ChromieCraft's live
auction data, giving realistic, market-driven base prices for every item ChromieCraft has data
for. Existing rows are fully refreshed (no curated set is preserved).

## Data source

ChromieCraft AH history is exposed by the third-party tracker **wowauctions.net** (Next.js app).
Plain fetches 403; a browser `User-Agent` returns 200.

A clean JSON endpoint avoids HTML parsing:

```
GET /_next/data/{buildId}/auctionHouse/chromie-craft/chromiecraft/mergedAh/{anyslug}-{itemId}.json
```

- The name slug is ignored — **only the numeric item ID matters**, so item names are not needed.
- `buildId` comes from `__NEXT_DATA__` on any page; it changes when the site redeploys, so the
  tool resolves it fresh at start (and re-resolves mid-run if 404s spike).

Response `pageProps.item.stats` provides:

| Field             | Use                                             |
|-------------------|-------------------------------------------------|
| `avg_price`       | → `avgPrice`                                     |
| `minimum_buyout`  | → `minPrice`                                      |
| `item_count`      | liquidity signal (how many concurrent listings)  |
| `item_last_seen`  | staleness signal (timestamp of last scan)        |

`pageProps.item.item_info.SellPrice` gives the vendor sell price (used as a price floor).
Items ChromieCraft has never scanned return no `stats` → skipped (they keep C++ default pricing).

## Decisions (locked)

| Decision            | Choice                                                        |
|---------------------|--------------------------------------------------------------|
| Coverage            | All AH-tradeable items (self-bounds to items CC has data for) |
| Existing rows       | Full refresh from ChromieCraft; nothing preserved            |
| Cadence             | One-time backfill script, generated SQL committed to repo    |
| `PRICE_SCALE`       | `1.0` (mirror ChromieCraft; tuning knob for later)           |
| `MIN_ITEM_COUNT`    | `1` (accept any data, even single listings)                  |
| `MAX_AGE_DAYS`      | `180` (lenient staleness gate; drops only ancient scans)     |
| Item ID source      | Live `acore_world.item_template` on the Merkerhood realm      |
| `DEVIATION_FACTOR`  | `3.0` (flag items where CC vs existing price differs ≥ 3×)    |

## Architecture — 5 stages

0. **Load existing overrides** — parse the current
   `2023_11_16_mod_auctionhousebot_priceOverride.sql` into `{item: (avgPrice, minPrice)}` so the
   new CC prices can be compared against what is shipped today (for the deviation report).

1. **Candidate IDs** — query `acore_world.item_template` for AH-plausible items instead of
   blindly hitting the full 1..60000 range:
   - `bonding IN (0,2,3)` (never-bound / BoE / BoU) — drops the large BoP gear tail that never
     reaches the AH
   - `Quality <= 5` (exclude artifact/heirloom edge classes as needed)
   - exclude quest/key item classes (`class NOT IN (12,13)`)
   - select `entry`, `SellPrice`
   The tool also accepts a pre-exported CSV of `(entry, SellPrice)` so it can run on a host
   without direct DB access (IDs exported once via SSH).

2. **Build-ID resolver** — GET homepage, parse `__NEXT_DATA__` `buildId`. Re-resolve if a run of
   consecutive 404s suggests a redeploy.

3. **Fetcher** — for each ID, GET the `_next/data` JSON with a browser UA.
   - small concurrency (default 6), polite rate limit
   - exponential backoff on 429 / 5xx
   - checkpoint/resume file (append-completed IDs) so a multi-thousand-item run survives
     interruption and re-runs cheaply

4. **Validate + transform** — keep a row only if `stats` present and:
   - `item_count >= MIN_ITEM_COUNT` (1)
   - `item_last_seen` within `MAX_AGE_DAYS` (180)
   - `avgPrice = round(avg_price * PRICE_SCALE)`, `minPrice = round(minimum_buyout * PRICE_SCALE)`
   - clamp `minPrice <= avgPrice`
   - floor both at vendor `SellPrice` so the bot never lists below vendor
   - drop rows where the resulting price is 0

5. **Emit** —
   - rewrite `data/sql/db-world/2023_11_16_mod_auctionhousebot_priceOverride.sql` in the exact
     existing format (`SET NAMES` / `DROP TABLE` / `CREATE TABLE` / one `INSERT ... VALUES` per row)
   - write `skipped.csv` (item, reason: no-data / stale / zero-price) for transparency
   - write `deviations.csv` — every item where a shipped override exists **and** the new CC price
     differs by ≥ `DEVIATION_FACTOR` on either `avgPrice` or `minPrice`. Columns:
     `item, item_name, existing_avg, existing_min, cc_avg, cc_min, avg_ratio, min_ratio,
     cc_item_count, cc_last_seen`, sorted by largest ratio first. The SQL is still written with
     the CC values (full-refresh decision stands); this report is the manual-review worklist to
     decide, per item, which price is better. Item names are pulled from the fetched JSON /
     item_template to make the review readable.

## Config

A small config block / CLI flags: `DB_DSN` or `--ids-csv`, `PRICE_SCALE`, `MIN_ITEM_COUNT`,
`MAX_AGE_DAYS`, `CONCURRENCY`, `RATE_LIMIT`, `--limit N` (smoke run), `--resume`.

## Error handling

- Transient HTTP (429/5xx/timeouts): one retry then exponential backoff; give up on an ID after
  N attempts and record it in `skipped.csv` (reason: fetch-failed).
- `buildId` staleness mid-run: detect 404 burst → re-resolve → continue.
- Malformed / missing `stats`: treat as no-data, skip, do not abort the run.
- The generated SQL is a drop-in replacement; the run is idempotent given the same source data.

## Testing

- Unit tests for stage 4 against fixture JSON: thin data (count=1), stale (> MAX_AGE_DAYS),
  `avg < min`, sub-vendor price, missing `stats`, zero price.
- Unit test for the deviation report: item with an existing override whose CC price is ≥ 3× (and
  one just under 3×) — verify only the former lands in `deviations.csv` with correct ratios, and
  that the SQL still emits the CC value regardless.
- Unit test for the existing-SQL parser (stage 0) against a fixture with the shipped INSERT format.
- `--limit 50` smoke run validating end-to-end fetch → SQL emit before the full pull.

## Out of scope

- No periodic/cron refresh (one-time by decision).
- No dynamic supply/demand pricing (tracked separately in `planned_things.md`).
- No changes to the C++ bot logic — this only regenerates the override data.

## Language / placement

Python 3 (host has `python3`). Tool lives in `tools/price-backfill/` in the `mod-ah-bot` repo.
Generated SQL overwrites the existing file under `data/sql/db-world/`.
