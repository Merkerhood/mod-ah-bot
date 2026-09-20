# ChromieCraft Price Backfill

Regenerates `data/sql/db-world/2023_11_16_mod_auctionhousebot_priceOverride.sql`
from ChromieCraft live auction data (via wowauctions.net).

## 1. Export candidate item IDs from the world DB

Run against the Merkerhood realm's `acore_world` (adjust host/creds):

```bash
mysql -N -B acore_world -e "
  SELECT entry FROM item_template
  WHERE bonding IN (0,2,3) AND Quality <= 5 AND class NOT IN (12,13)
    AND entry NOT IN (SELECT item FROM npc_vendor
                      WHERE ExtendedCost = 0 AND maxcount = 0)
    AND entry NOT IN (SELECT item FROM game_event_npc_vendor
                      WHERE ExtendedCost = 0 AND maxcount = 0)
  ORDER BY entry;" > candidates.csv
```

An item a vendor stocks without limit and sells for gold stays out of the
override table. A player buys it from the NPC whenever they want at a fixed
price, so an auction for it is bait: the scraped market price is either far
above what the vendor charges or so close to it that the listing has no reason
to exist. Without an override the seller prices off the item's own vendor value
and the vendor price ceiling caps the listing there.

The two qualifiers matter. `ExtendedCost` rows are paid in honor or tokens
rather than gold, and a limited `maxcount` makes the item scarce enough that an
auction house premium is legitimate; both keep their override. This is the same
line the vendor price ceiling draws in `AHBConfig::LoadVendorGoldPrices`.

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

## Calibrated BuyPrice fallback (items with no market data at all)

An item nobody has ever listed on ChromieCraft gets no override row, so
`AuctionHouseBot.cpp` prices it off `SellPrice` times a quality multiplier.
Measured against the items we do have market data for, that anchor is useless:
the market-to-`SellPrice` ratio spans 26x to 1,476x between p10 and p90 inside a
single quality tier. `BuyPrice` tracks market value far more closely, so fit the
median market-to-`BuyPrice` ratio on the items that have both and apply it to
the items that have only a `BuyPrice`.

1. Export item attributes and the freely-vendor-sold flag:
   ```bash
   mysql -N -B acore_world -e "
     SELECT it.entry, it.class, it.subclass, it.Quality, it.BuyPrice, it.SellPrice,
       (it.entry IN (SELECT item FROM npc_vendor WHERE ExtendedCost=0 AND maxcount=0)
        OR it.entry IN (SELECT item FROM game_event_npc_vendor
                        WHERE ExtendedCost=0 AND maxcount=0)) AS vendor
     FROM item_template it
     WHERE it.bonding IN (0,2,3) AND it.Quality <= 5 AND it.class NOT IN (12,13)
     ORDER BY it.entry;" > items.tsv
   ```
2. Export the items the trash filter disables, reusing its own `WHERE` clause
   from `data/sql/db-world/z_filter_disabled_and_trash.sql`, so test, deprecated
   and junk items neither get priced nor shape a multiplier:
   ```bash
   mysql -N -B acore_world -e "SELECT entry FROM item_template WHERE <that clause>;" \
     > disabled.txt
   ```
3. Merge the derived prices in:
   ```bash
   python3 fallback.py --items-tsv items.tsv --disabled-csv disabled.txt \
     --existing-sql ../../data/sql/db-world/2023_11_16_mod_auctionhousebot_priceOverride.sql \
     --report reports/calibrated-fallback.csv
   ```

Derived rows only ADD items with no existing override, so the merge is additive
and leaves every scraped price untouched.

### How a price is chosen

The multiplier comes from the item's own `(class, subclass, quality)` bucket,
falling back to `(class, quality)`, then quality-wide, then a global median when
a bucket holds fewer than `--min-bucket` fitted items. The report's `tier` column
records which one was used.

Two guards sit on the result. The price is capped at the `--cap-percentile` of
what the bucket has actually fetched, because a loose bucket applied to an
unusually expensive item produces a number the market has never seen. It is then
floored at `SellPrice`, since no seller lists below vendor buyback.

Recipes (class 9) are excluded by default: they need a pricing basis of their
own rather than a class multiplier. Items with `BuyPrice = 0` have no anchor and
are left to the C++ default; the report lists them as `no-anchor`.

Tuning: `--min-bucket` (30), `--cap-percentile` (0.99), `--exclude-classes` (9).
