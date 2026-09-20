"""Fit a BuyPrice multiplier per item bucket and price the market-data gap.

Items nobody has ever listed on ChromieCraft get no override row, so the AH bot
falls back to `SellPrice` times a quality multiplier. Measured against the items
we do have market data for, that anchor is worthless: the market-to-`SellPrice`
ratio spans two orders of magnitude inside a single quality tier. `BuyPrice` --
what a vendor charges for the item -- tracks market value far more closely.

So: fit the median market-to-`BuyPrice` ratio on the items that have both, then
apply it to the items that have only a `BuyPrice`.
"""
import statistics
from collections import defaultdict, namedtuple

Item = namedtuple("Item", "entry cls sub quality buy_price sell_price")
Market = namedtuple("Market", "entry avg_price min_price")
Config = namedtuple("Config", "min_bucket cap_percentile")

CLASS_SUBCLASS_QUALITY = "class-subclass-quality"
CLASS_QUALITY = "class-quality"
QUALITY = "quality"
GLOBAL = "global"
NO_ANCHOR = "no-anchor"

_KEYS = (
    (CLASS_SUBCLASS_QUALITY, lambda it: (it.cls, it.sub, it.quality)),
    (CLASS_QUALITY, lambda it: (it.cls, it.quality)),
    (QUALITY, lambda it: it.quality),
)


def _percentile(values, fraction):
    ordered = sorted(values)
    idx = int(round(fraction * (len(ordered) - 1)))
    return ordered[max(0, min(len(ordered) - 1, idx))]


class Calibration:
    """Fitted multipliers and price caps, resolved most-specific-first."""

    def __init__(self, ratios, prices, cfg):
        self._ratios = ratios
        self._prices = prices
        self._cfg = cfg
        pooled = [r for key, _ in _KEYS if key == QUALITY
                  for bucket in ratios[key].values() for r in bucket]
        self._global = self._medians(pooled) if pooled else (1.0, 1.0)

    @staticmethod
    def _medians(pairs):
        return (statistics.median([a for a, _ in pairs]),
                statistics.median([m for _, m in pairs]))

    def multiplier(self, it):
        """Return ((avg_multiplier, min_multiplier), tier) for an item."""
        for tier, keyfn in _KEYS:
            bucket = self._ratios[tier].get(keyfn(it), [])
            if bucket and len(bucket) >= self._cfg.min_bucket:
                return self._medians(bucket), tier
        return self._global, GLOBAL

    def cap(self, it):
        """Highest price the item's own bucket has ever actually fetched."""
        for tier, keyfn in _KEYS:
            bucket = self._prices[tier].get(keyfn(it), [])
            if bucket and len(bucket) >= self._cfg.min_bucket:
                return _percentile(bucket, self._cfg.cap_percentile)
        return None


def fit(items, market, cfg):
    """Fit multipliers from the items that have both a market price and a
    `BuyPrice`. Market rows for unknown items are ignored: the override table
    outlives `item_template` edits."""
    by_entry = {it.entry: it for it in items}
    ratios = {tier: defaultdict(list) for tier, _ in _KEYS}
    prices = {tier: defaultdict(list) for tier, _ in _KEYS}
    for row in market:
        it = by_entry.get(row.entry)
        if it is None:
            continue
        for tier, keyfn in _KEYS:
            prices[tier][keyfn(it)].append(row.avg_price)
        if it.buy_price <= 0:
            continue
        pair = (row.avg_price / it.buy_price, row.min_price / it.buy_price)
        for tier, keyfn in _KEYS:
            ratios[tier][keyfn(it)].append(pair)
    return Calibration(ratios, prices, cfg)


def derive_rows(gap_items, calibration):
    """Price the gap items. Returns ([(entry, avg, min)], report rows)."""
    rows = []
    report = []
    for it in sorted(gap_items, key=lambda x: x.entry):
        if it.buy_price <= 0:
            report.append({"item": it.entry, "tier": NO_ANCHOR, "multiplier": 0,
                           "avg": 0, "min": 0, "capped": False, "floored": False})
            continue
        (m_avg, m_min), tier = calibration.multiplier(it)
        avg = it.buy_price * m_avg
        mn = min(it.buy_price * m_min, avg)

        cap = calibration.cap(it)
        capped = cap is not None and avg > cap
        if capped:
            avg = cap
            mn = min(mn, avg)

        # Never price below what a vendor pays for the item. minPrice is the
        # buyout the bot actually asks, so the floor has to lift it too.
        floored = avg < it.sell_price or mn < it.sell_price
        avg = max(avg, it.sell_price)
        mn = max(mn, it.sell_price)
        mn = max(1, min(round(mn), round(avg)))
        avg = max(1, round(avg))

        rows.append((it.entry, avg, mn))
        report.append({"item": it.entry, "tier": tier,
                       "multiplier": round(m_avg, 4), "avg": avg, "min": mn,
                       "capped": capped, "floored": floored})
    return rows, report
