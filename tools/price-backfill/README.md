# ChromieCraft Price Backfill

Regenerates `data/sql/db-world/2023_11_16_mod_auctionhousebot_priceOverride.sql`
from ChromieCraft live auction data (via wowauctions.net).

## 1. Export candidate item IDs from the world DB

Run against the Merkerhood realm's `acore_world` (adjust host/creds):

```bash
mysql -N -B acore_world -e "
  SELECT entry FROM item_template
  WHERE bonding IN (0,2,3) AND Quality <= 5 AND class NOT IN (12,13)
  ORDER BY entry;" > candidates.csv
```

## 2. Run the backfill

```bash
python3 backfill.py \
  --ids-csv candidates.csv \
  --existing-sql ../../data/sql/db-world/2023_11_16_mod_auctionhousebot_priceOverride.sql
```

The out-SQL defaults to the existing file (in-place regen). Use `--out-sql`
to write elsewhere first if you want to diff before committing.

Interruptible: re-run with `--resume` to continue from `checkpoint.jsonl`.
Smoke test first with `--limit 50`.

## Outputs

- regenerated `.sql` (full refresh, ChromieCraft prices)
- `skipped.csv` — items with no/thin/stale/zero data (keep C++ default pricing)
- `deviations.csv` — items where the new price differs >=1.5x from the shipped
  override; the manual-review worklist, worst-first.

## Tuning

`--price-scale` (default 1.0), `--min-item-count` (1), `--max-age-days` (180),
`--deviation-factor` (1.5), `--concurrency` (6), `--rate-delay` (0.15s).

## Tests

```bash
python3 -m unittest discover -v
```
