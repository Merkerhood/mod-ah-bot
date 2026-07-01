# Part B — Lifelike AH via per-item count overrides (implementation plan)

Date: 2026-07-01
Status: Plan (C++ module change; test on TEST realm before LIVE).
Depends on: Part A data (`tools/price-backfill/reports/item-counts-2026-07-01.csv`).

## Goal

Make the bot list quantities per item that mirror ChromieCraft's real auction house,
instead of the uniform per-quality counts it uses today.

## Measured data (from the 2026-07-01 fetch)

9,419 items have a live ChromieCraft listing count (`stats.item_count`). The distribution
is heavily skewed:

| stat | value |
|---|---|
| items with a count | 9,419 |
| count == 1 | 5,380 (57%) |
| median | 1 |
| p90 | 18 |
| p95 | 44 |
| p99 | 300 |
| max | 102,000 |

Raw counts cannot be used directly: a bot listing 102,000 (or even 300) of an item would
be absurd and would swamp the AH. The mapping must clamp the tail.

## Recommended mapping

`targetCount = min(cc_item_count, CAP)`, `CAP = 50` (≈p95).

- Preserves the real shape for ~95% of items (most stay scarce — median 1 — which is
  realistic), while capping the 148 items with ≥200 listings and the 102k outlier to 50.
- `CAP` is the single tunable; `50` keeps commodities (cloth/ore/arrows) feeling stocked
  without flooding. Alternatives: `20` (≈p90, leaner) or a log-bucket
  (`1→1, 2-5→2, 6-20→4, 21-100→8, >100→15`) for a smoother feel. Pick before generating.

**Semantics (recommended):** the override is a per-item **max duplicates / target count** —
it replaces the global `DuplicatesCount` (and the per-quality maximum) for that specific
item. Items with no override keep current behaviour. (Open alternative: treat it as a
maintained target the bot tops up toward; more invasive, defer.)

## Module change

1. New table:
   ```sql
   DROP TABLE IF EXISTS `mod_auctionhousebot_countOverride`;
   CREATE TABLE `mod_auctionhousebot_countOverride` (
     `item` mediumint(8) NOT NULL,
     `targetCount` int NOT NULL,
     PRIMARY KEY (`item`) USING BTREE
   ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
   ```

2. Loader mirroring the price-override path in `src/AuctionHouseBot.cpp`
   (`LoadPriceOverrides` / `itemPriceOverrides`, shared across alliance/horde/neutral configs
   via `Initialize`): add `itemCountOverrides` (`std::unordered_map<uint32,uint32>`), a
   `LoadCountOverrides()` that `SELECT item, targetCount FROM mod_auctionhousebot_countOverride`,
   load once and share across the three configs the same way price overrides are shared, plus
   a `GetCountOverrideForItem(uint32 itemId)` accessor.

3. Listing path: in `getElement` / `Sell`, where the per-item duplicate cap is applied today
   (the `maxDup` / `DuplicatesCount` logic around `getElement`, and the per-quality maximum),
   consult `GetCountOverrideForItem(itemID)` first: if present, use it as `maxDup` / the
   item's target count; otherwise fall back to the current global value. Keep the change
   localized to where the count is read.

## Generating the countOverride SQL from the dataset

```bash
# CAP=50 clamp; emit an INSERT-per-row SQL like the priceOverride file
python3 - <<'PY'
import csv
CAP=50
rows=[]
with open("tools/price-backfill/reports/item-counts-2026-07-01.csv", newline="") as f:
    for r in csv.DictReader(f):
        c=int(r["cc_item_count"])
        if c>0: rows.append((int(r["item"]), min(c, CAP)))
rows.sort()
with open("data/sql/db-world/mod_auctionhousebot_countOverride.sql","w") as o:
    o.write("SET NAMES utf8mb4;\nSET FOREIGN_KEY_CHECKS = 0;\n")
    o.write("DROP TABLE IF EXISTS `mod_auctionhousebot_countOverride`;\n")
    o.write("CREATE TABLE `mod_auctionhousebot_countOverride` (`item` mediumint(8) NOT NULL, `targetCount` int NOT NULL, PRIMARY KEY (`item`) USING BTREE) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;\n")
    for i,c in rows:
        o.write("INSERT INTO `mod_auctionhousebot_countOverride` VALUES (%d, %d);\n"%(i,c))
    o.write("\nSET FOREIGN_KEY_CHECKS = 1;\n")
PY
```

## Open decisions before building

- `CAP` value / mapping shape (50 clamp recommended; 20 or log-bucket alternatives).
- Semantics: max-cap (recommended) vs maintained-target.
- Whether a single-scan snapshot is enough, or counts should be averaged over repeated
  scans (more scraping) for stability.

## Testing

- Unit-test the loader/accessor as the price-override path is tested.
- On the TEST realm: apply the countOverride SQL, let the bot populate the AH, and confirm
  high-count items (cloth/ore) list in bulk while median items stay scarce, with no absurd
  quantities. Only then apply to LIVE.
