# Player-reactive demand multiplier — design

Date: 2026-07-02
Status: Approved, implementing.

## Problem

Prices are anchored to a curated ChromieCraft baseline (`mod_auctionhousebot_priceOverride`),
which is static until the backfill tool is rerun by hand. On a low-population realm nothing in
the pricing path reacts to what players actually buy, so a baseline that's slightly off for a
given item stays off indefinitely, and there's no way for a run on an item to nudge its price up
the way a real market would.

## Signal

`AHBot_AuctionHouseScript::OnAuctionSuccessful` already fires for every completed auction and
already writes a row to `mod_auctionhousebot_auction_history`. When dynamic pricing is enabled
and the winning bidder is a real human (not one of the bot's own `gBotsId` characters, not a
mod-playerbots random bot account), bump that item's stored multiplier:

```
m_new = clamp(m_effective * (1 + BumpPercent/100), MinMultiplier, MaxMultiplier)
```

`m_effective` is the current decayed value (see below), so repeated bumps compound instead of
resetting the decay clock without capturing the earlier bump.

### Human detection

- Bot characters: buyer character GUID is checked against `gBotsId`, the module's own set of
  auctioning character GUIDs (already used the same way in `OnAuctionAdd`/`OnAuctionRemove`).
- Playerbots: there's no compile-time dependency on mod-playerbots, so bots are detected by
  account instead. The buyer's account id is resolved via `sCharacterCache`, then the account
  `username` (LoginDatabase) is checked against a configurable, case-insensitive prefix list
  (default `rndbot`, mod-playerbots' random-bot account prefix).
- Account id -> is-human verdicts are cached in memory (`gAccountHumanCache`) to avoid a
  LoginDatabase round trip on every trade; the world thread is single-threaded for all AHBot
  hooks so the cache needs no locking (same assumption the existing override maps already make).
- If GUID or account resolution is ambiguous or fails, the code does not bump — fail toward
  no-op, never toward a crash or a wrong guess.

## Decay

Lazy, computed on read, no periodic job and no background rewrite. Only a bump rewrites the
stored row.

```
effective = 1.0 + (stored - 1.0) * pow(0.5, hoursSince(last_bump) / DecayHalfLifeHours)
```

Snapped to exactly `1.0` once `|effective - 1.0| < 0.01`, so an old, fully-decayed row reads as
neutral instead of asymptotically approaching it forever.

## Table schema

`mod_auctionhousebot_demand`:

| column | type | notes |
|---|---|---|
| `item` | `INT UNSIGNED` | primary key |
| `multiplier` | `DOUBLE NOT NULL DEFAULT 1.0` | stored (pre-decay) value |
| `last_bump` | `BIGINT NOT NULL DEFAULT 0` | unix timestamp of the last bump |

Loaded into an in-memory `unordered_map<uint32, DemandEntry>` on startup and on `.reload config`,
alongside the existing price/count override maps (`AHBot_WorldScript::LoadSharedOverrides`).

## Application

`AuctionHouseBot::AdjustPrices` is the single choke point that finalizes an item's buyout/bid
before the seller applies its +-10% listing deviation. When dynamic pricing is enabled, both
`buyoutPrice` and `bidPrice` are multiplied by the item's effective demand multiplier there,
after the existing moving-average and override clamp. The existing bid<=buyout enforcement in
`Sell()` runs after `AdjustPrices` returns and after the deviation is applied, so it still
catches any inversion the multiplier could introduce.

## Config

`conf/mod_ahbot.conf.dist`:

- `AuctionHouseBot.DynamicPricing.Enable` (default 0)
- `AuctionHouseBot.DynamicPricing.BumpPercent` (default 8)
- `AuctionHouseBot.DynamicPricing.DecayHalfLifeHours` (default 72)
- `AuctionHouseBot.DynamicPricing.MinMultiplier` (default 0.5)
- `AuctionHouseBot.DynamicPricing.MaxMultiplier` (default 3.0)
- `AuctionHouseBot.DynamicPricing.BotAccountPrefixes` (default `rndbot`, comma-separated)

Bounds are validated on load (`Min <= 1 <= Max`, `BumpPercent > 0`, `HalfLife > 0`); nonsense
values are clamped to a sane default and logged with `LOG_WARN`, never a crash.

## Explicitly out of scope

- Supply-side signal: this feature only reacts to human *purchases*. A human *listing* or
  undercutting an item is not fed back into the multiplier. Revisit only if the buy-side signal
  proves insufficient on its own.
- Per-faction demand: the multiplier is per-item, not per-auction-house. A bump observed on one
  faction's AH is written to that config's in-memory map immediately and reaches the other two
  configs on the next full reload (same propagation model the existing price/count overrides
  use), not instantly.

## Testing

C++ can't be unit-tested locally (needs the full core). Gates:
- CI `-Werror` build (compile + no unused-param/var).
- TEST-realm validation: manually buy an item as a real character, confirm the row in
  `mod_auctionhousebot_demand` appears/updates, confirm the bot's next listing for that item
  reflects the bumped price, confirm playerbot purchases do not create/bump a row.
