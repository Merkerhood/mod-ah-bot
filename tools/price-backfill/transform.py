"""Validate ChromieCraft item stats into price rows and detect deviations."""
from collections import namedtuple
from datetime import datetime, timedelta
from typing import Optional, Tuple

Config = namedtuple("Config", "price_scale min_item_count max_age_days deviation_factor")
PriceRow = namedtuple("PriceRow", "item avg_price min_price")
Deviation = namedtuple(
    "Deviation",
    "item item_name existing_avg existing_min cc_avg cc_min "
    "avg_ratio min_ratio cc_item_count cc_last_seen",
)


def build_row(item_id: int, item: dict, cfg: Config, now: datetime):
    stats = item.get("stats")
    if not stats:
        return None, "no-data"
    if (stats.get("item_count") or 0) < cfg.min_item_count:
        return None, "thin"
    if not _within_age(stats.get("item_last_seen"), cfg.max_age_days, now):
        return None, "stale"
    avg = round((stats.get("avg_price") or 0) * cfg.price_scale)
    mn = round((stats.get("minimum_buyout") or 0) * cfg.price_scale)
    if mn > avg:
        mn = avg
    sell = (item.get("item_info") or {}).get("SellPrice") or 0
    if sell > 0:
        avg = max(avg, sell)
        mn = max(mn, sell)
    if avg <= 0 or mn <= 0:
        return None, "zero-price"
    return PriceRow(item_id, avg, mn), None


def compute_deviation(row: PriceRow, existing: Optional[Tuple[int, int]],
                      item_name: str, stats: dict, factor: float):
    if existing is None:
        return None
    ex_avg, ex_min = existing
    avg_ratio = _ratio(row.avg_price, ex_avg)
    min_ratio = _ratio(row.min_price, ex_min)
    if avg_ratio >= factor or min_ratio >= factor:
        return Deviation(
            row.item, item_name, ex_avg, ex_min, row.avg_price, row.min_price,
            round(avg_ratio, 2), round(min_ratio, 2),
            stats.get("item_count"), stats.get("item_last_seen"),
        )
    return None


def _within_age(last_seen, max_age_days: int, now: datetime) -> bool:
    if not last_seen:
        return False
    try:
        seen = datetime.strptime(last_seen, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return False
    return seen >= now - timedelta(days=max_age_days)


def _ratio(a: int, b: int) -> float:
    if a <= 0 or b <= 0:
        return float("inf") if a != b else 1.0
    return max(a / b, b / a)
