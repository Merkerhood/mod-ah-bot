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
`--deviation-factor` (1.5), `--concurrency` (6), `--rate-delay` (0.15s),
`--redeploy-threshold` (25), `--sentinel-id` (4389), `--resweep-rounds` (2).

## Transient fetch failures

Over a long run some requests hit transient HTTP/network errors that survive the
per-request retries (`_get` retries 5x with exponential backoff). At the end of a run
the tool re-sweeps any `fetch-failed` items for `--resweep-rounds` bounded passes until
none remain, so a single run doesn't leave a gap of items that simply weren't reached.

## Redeploy detection

wowauctions.net is a Next.js app whose `buildId` changes on redeploy, which makes
the old data URLs 404. Since roughly half of WotLK items also legitimately have no
ChromieCraft data (a normal 404), the tool cannot treat a raw 404 streak as a
redeploy. After `--redeploy-threshold` consecutive 404s it checks a known-good
`--sentinel-id` (default 4389, Gyrochronatom): only if that item also 404s is the
buildId re-resolved and a scoped recovery re-fetch triggered. This prevents a
no-data streak from causing a re-resolve storm that rate-limits the site.

## Tests

```bash
python3 -m unittest discover -v
```

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
