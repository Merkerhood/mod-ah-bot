"""Derive craftable-item prices from reagent costs (cc -> recipe -> vendor -> 0)."""
from collections import namedtuple
from typing import Dict, List, Tuple

DeriveConfig = namedtuple("DeriveConfig", "margin_avg margin_min max_depth")


def resolve_price(item, recipes, overrides, vendor, cfg, memo=None, visiting=None):
    if memo is None:
        memo = {}
    if visiting is None:
        visiting = set()
    if item in overrides:
        return float(overrides[item])
    if item in memo:
        return memo[item]
    if item in recipes and item not in visiting and len(visiting) < cfg.max_depth:
        visiting.add(item)
        yld, reagents = recipes[item]
        total = 0.0
        for r, cnt in reagents:
            total += resolve_price(r, recipes, overrides, vendor, cfg, memo, visiting) * cnt
        visiting.discard(item)
        cost = total / (yld if yld > 0 else 1)
        memo[item] = cost
        return cost
    if vendor.get(item, 0) > 0:
        return float(vendor[item])
    return 0.0


def price_source(item, recipes, overrides, vendor):
    if item in overrides:
        return "cc"
    if item in recipes:
        return "derived"
    if vendor.get(item, 0) > 0:
        return "vendor"
    return "none"


def derive_rows(recipes, overrides, vendor, cfg):
    rows: List[Tuple[int, int, int]] = []
    report: List[dict] = []
    memo: Dict[int, float] = {}
    for item in sorted(recipes):
        if item in overrides:
            continue
        cost = resolve_price(item, recipes, overrides, vendor, cfg, memo)
        if cost <= 0:
            continue
        avg = round(cost * cfg.margin_avg)
        mn = round(cost * cfg.margin_min)
        if avg <= 0 or mn <= 0:
            continue
        rows.append((item, avg, mn))
        srcs = ",".join(
            "%d:%s" % (r, price_source(r, recipes, overrides, vendor))
            for r, _ in recipes[item][1])
        report.append({"item": item, "cost": round(cost), "avg": avg, "min": mn,
                       "reagent_sources": srcs})
    return rows, report
