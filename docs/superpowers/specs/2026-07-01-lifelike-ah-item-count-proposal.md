# Lifelike AH via per-item count overrides — proposal (small plan)

Date: 2026-07-01
Status: Proposal only (not scheduled) — follow-up to the ChromieCraft price work.

## Idea

Make the bot's auction house feel real by mirroring, per item, how many of that item
are actually listed on ChromieCraft's merged AH, instead of the bot's uniform per-quality
listing counts. A common reagent should show up in bulk; a rare recipe should be scarce.

## Signal we already have

wowauctions.net's JSON returns `stats.item_count` — the number of that item currently
listed on the ChromieCraft merged AH. That is a real liquidity snapshot per item id. The
scraper already reads it; the price run just didn't persist it.

## Current bot behaviour

`AuctionHouseBot::Sell` / `getElement` list items using a global `DuplicatesCount` cap and
per-quality target counts (`config->GetMaximum(AHB_*)`). Listing quantity does not vary per
item — every white trade good is treated alike.

## Plan

### Part A — Data (cheap, do first)
- Add `cc_item_count` (and keep `cc_last_seen`) to the scraper's per-item record and emit a
  count dataset: `item, cc_item_count, cc_last_seen`. One extra field in
  `tools/price-backfill` — capture it on the next run (we are re-running anyway).
- Decide a mapping from raw `cc_item_count` to a bot target count: raw is a single-scan
  snapshot and can be large (hundreds). Options: clamp to a max, log/bucket into
  small/medium/large tiers, or scale by a factor. Emit the mapped target in the dataset.

### Part B — Module (moderate C++, separate PR, test on TEST realm)
- New table `mod_auctionhousebot_countOverride (item mediumint PK, targetCount int)` loaded
  the same way `priceOverride` is (see `AuctionHouseBot::LoadPriceOverrides` /
  `itemPriceOverrides` sharing across configs).
- In the listing path (`getElement` / `Sell`), when a per-item override exists, use it as
  the max-duplicates / target count for that item instead of the global `DuplicatesCount`
  and per-quality maximum. Items without an override keep current behaviour.

## Open questions to settle before building Part B

- Mapping: raw vs scaled vs clamped vs bucketed `cc_item_count` → bot target.
- Semantics: is the override a MIN floor, a MAX cap, or a target the bot maintains?
- Snapshot vs average: `item_count` is one scan; a stable value would need repeated scans
  over time (more scraping).
- Interaction with existing `DuplicatesCount` and per-quality count config keys.

## Effort / risk

- Part A: trivial (one field + dataset export).
- Part B: moderate — new table + loader (mirrors the priceOverride path) and a change to the
  listing-generation logic; must be tested on the TEST realm before LIVE.

## Recommended first step

Capture `cc_item_count` on the next scraper run (near-free), build the dataset, then decide
the mapping and semantics before touching the C++ listing path.
