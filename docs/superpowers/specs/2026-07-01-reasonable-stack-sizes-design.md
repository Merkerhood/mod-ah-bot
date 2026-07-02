# Reasonable AH stack sizes — design

Date: 2026-07-01
Status: Approved, implementing.

## Problem

`AuctionHouseBot::getStackCount` picks a stack size with `urand(1, min(stackable, maxStackSize))`
(uniform random, with a `// TODO: This is not good` comment). Result: commodities list at
unrealistic sizes (7, 13, ...) and any item whose template `stackable > 1` (e.g. glyphs on
this dataset) gets quantities a real player would never post. Real players sell glyphs,
recipes, cut gems, etc. one at a time, and post commodities in clean 1/5/10/20/full stacks.

## Approach

Rewrite `getStackCount` to be category-aware and use realistic breakpoints. It takes the
`ItemTemplate*` (already in scope at the two `Sell` call sites) to branch on class /
GemProperties. Pure arithmetic + a `switch` + one `urand` — O(1), no DB lookup, no
allocation (not a performance path). All existing ceilings are preserved: the caller still
passes `item->GetMaxStackCount()` (template `stackable`) and applies the config
`GetMaxStackSize` and per-quality `GetMaxStack` caps.

## Rules (in order)

1. **Always single (return 1)** regardless of template stackable:
   - `ITEM_CLASS_GLYPH` (16), `ITEM_CLASS_RECIPE` (9), `ITEM_CLASS_QUEST` (12),
     `ITEM_CLASS_CONTAINER` (1), `ITEM_CLASS_KEY` (13)
   - `ITEM_CLASS_GEM` (3) **when `prototype->GemProperties != 0`** (cut/socketable gem).
     Raw gems (class 3, `GemProperties == 0`) fall through and stack like materials.
2. **Non-stackable** (`max <= 1`) -> 1.
3. **Commodities** -> weighted human breakpoint, capped at `cap = maxStackSize > 0 ?
   min(max, maxStackSize) : max`:
   - roll `urand(1,100)`: `<=30` -> full stack (`cap`); `<=50` -> `1`; else a random pick of
     the breakpoints `{5,10,20}` that are `<= cap` (if none valid, full stack).
   - Weights are tunable; the intent is "biased toward what players actually post, never a
     random 7/13".

The existing `DivisibleStacks` config branch is kept as an alternate mode for admins who set
it; only the uniform-random default is replaced. Category rules (step 1) apply before either.

## Signature change

- `getStackCount(AHBConfig* config, uint32 max)` -> `getStackCount(AHBConfig* config, uint32 max, ItemTemplate const* prototype)`.
- Update the two call sites in `Sell` (they already have `prototype`) to pass it.
- Header declaration updated to match.

## Safety / perf

- No behaviour change to the `Sell` gating or the quality/config caps — only the number
  chosen within the ceiling changes.
- No DB access, no heap allocation; a fixed 3-element local array for the mid breakpoints.
- Degrades sensibly for odd `cap` values (e.g. a `cap` with no valid mid breakpoint returns
  the full stack).

## Testing

C++ can't be unit-tested locally (needs the full core). Gates:
- CI `-Werror` build (compile + no unused-param/var).
- TEST-realm validation: confirm glyphs / recipes / cut gems list as quantity 1, raw gems
  and commodities (cloth/ore/herbs/potions) list as clean 1/5/10/20/full stacks, no random
  odd sizes.

## Out of scope

- The pre-existing `DivisibleStacks` `ret == 0` edge (when `max` isn't divisible by 3/4/5) —
  left untouched.
- Any per-item stack override table (rules cover the need; revisit only if exceptions surface).
