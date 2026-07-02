# Planned Improvements and Features for AuctionHouseBot

Tracker for the original 10-item wishlist (items 11-20 were exact duplicates of 1-10 and have been folded in).

## Covered

- Competitive pricing: ChromieCraft market-price overrides (PRs #11, #13, #15).
- Inventory management: per-item listing count overrides (PRs #16, #17, #18) and reasonable stack sizes (PR #19).
- Item rarity consideration: per-rarity price/bid ratio config exists upstream and is retained.
- Market analysis: wowauctions.net backfill toolchain (tools/price-backfill).

## In progress

- Dynamic supply/demand pricing, price history analysis, user feedback integration: player-reactive demand multiplier, feat/demand-multiplier branch. The auction_history capture and moving-average pricing already merged cover the history-analysis foundation.
- Bid increment strategy, buyout price strategy: this branch, feat/bid-buyout-refinement.

## Deferred

- Time-based pricing adjustments: low value on a low-population realm.
