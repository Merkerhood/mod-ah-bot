"""Price profession recipes from their required skill rank.

A recipe is not the item it teaches. Reagent-cost derivation (recipe_prices.py)
prices the crafted product; the recipe itself is a separate good whose value
comes from the access it grants. The anchor here is the required skill rank,
modified by the value of the item the recipe teaches where that link exists,
fitted against the recipes that already carry a market price.
"""
import math
import statistics
from collections import namedtuple
from typing import Dict, List, Sequence, Tuple

Sample = namedtuple("Sample", "item rank skill quality sell_price product_price")

# Feature layout: intercept, rank, log(product), has-product, log(sell), has-sell,
# quality one-hot, profession one-hot.
IDX_INTERCEPT = 0
IDX_RANK = 1
IDX_LOG_PRODUCT = 2
IDX_HAS_PRODUCT = 3
IDX_LOG_SELL = 4
IDX_HAS_SELL = 5
_QUALITIES = (0, 1, 2, 3, 4, 5)  # matches the Quality <= 5 export filter
RANK_BUCKET = 25

Model = namedtuple("Model", "coeffs skills")


def solve_least_squares(X: Sequence[Sequence[float]], y: Sequence[float]) -> List[float]:
    """Normal-equation least squares by Gauss-Jordan, stdlib only."""
    n = len(X[0])
    aug = [[sum(row[i] * row[j] for row in X) for j in range(n)] +
           [sum(row[i] * v for row, v in zip(X, y))] for i in range(n)]
    for i in range(n):
        pivot = max(range(i, n), key=lambda r: abs(aug[r][i]))
        aug[i], aug[pivot] = aug[pivot], aug[i]
        if abs(aug[i][i]) < 1e-9:
            # Collinear or under-determined column; ridge it rather than divide by ~0.
            aug[i][i] += 1e-6
        for r in range(n):
            if r == i:
                continue
            f = aug[r][i] / aug[i][i]
            for c in range(i, n + 1):
                aug[r][c] -= f * aug[i][c]
    return [aug[i][n] / aug[i][i] for i in range(n)]


def features(s: Sample, skills: Sequence[int]) -> List[float]:
    f = [0.0] * 6
    f[IDX_INTERCEPT] = 1.0
    f[IDX_RANK] = s.rank / 100.0
    if s.product_price > 0:
        f[IDX_LOG_PRODUCT] = math.log(s.product_price)
        f[IDX_HAS_PRODUCT] = 1.0
    if s.sell_price > 0:
        f[IDX_LOG_SELL] = math.log(s.sell_price)
        f[IDX_HAS_SELL] = 1.0
    f += [1.0 if s.quality == q else 0.0 for q in _QUALITIES]
    f += [1.0 if s.skill == k else 0.0 for k in skills]
    return f


def fit(samples: Sequence[Tuple[Sample, float]]) -> Model:
    """Fit log(price) on the recipes that already have a market price."""
    skills = sorted({s.skill for s, _ in samples if s.skill > 0})
    X = [features(s, skills) for s, _ in samples]
    y = [math.log(p) for _, p in samples]
    return Model(solve_least_squares(X, y), skills)


def predict(model: Model, s: Sample) -> float:
    return math.exp(sum(c * f for c, f in zip(model.coeffs, features(s, model.skills))))


class Bands:
    """Per-rank-bucket [floor, ceiling] taken from observed recipe prices."""

    def __init__(self, buckets: Dict[int, Tuple[float, float]], global_band: Tuple[float, float]):
        self.buckets = buckets
        self.global_band = global_band

    def for_rank(self, rank: int) -> Tuple[float, float]:
        return self.buckets.get(rank // RANK_BUCKET, self.global_band)


def _band(prices: Sequence[float]) -> Tuple[float, float]:
    ordered = sorted(prices)
    lo = ordered[int(len(ordered) * 0.05)]
    hi = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
    return (lo, hi)


def rank_bands(samples: Sequence[Tuple[Sample, float]], min_bucket: int = 8) -> Bands:
    grouped: Dict[int, List[float]] = {}
    for s, price in samples:
        grouped.setdefault(s.rank // RANK_BUCKET, []).append(price)
    buckets = {k: _band(v) for k, v in grouped.items() if len(v) >= min_bucket}
    return Bands(buckets, _band([p for _, p in samples]))


def price_rows(model: Model, bands: Bands, targets: Sequence[Sample],
               existing: Dict[int, Tuple[int, int]], min_ratio: float = 0.85):
    """Emit (item, avgPrice, minPrice) rows for recipes with no override yet."""
    rows: List[Tuple[int, int, int]] = []
    report: List[dict] = []
    for s in sorted(targets, key=lambda t: t.item):
        if s.item in existing or s.rank <= 0:
            continue
        raw = predict(model, s)
        lo, hi = bands.for_rank(s.rank)
        clamp = "none"
        price = raw
        if price > hi:
            price, clamp = hi, "ceiling"
        elif price < lo:
            price, clamp = lo, "floor"
        # A recipe is never worth less to a player than the vendor pays for it.
        if price < s.sell_price:
            price, clamp = float(s.sell_price), "vendor-floor"
        avg = round(price)
        if avg <= 0:
            continue
        mn = max(1, round(avg * min_ratio))
        rows.append((s.item, avg, mn))
        report.append({"item": s.item, "rank": s.rank, "skill": s.skill,
                       "product_price": s.product_price, "sell_price": s.sell_price,
                       "raw": round(raw), "avg": avg, "min": mn, "clamp": clamp})
    return rows, report


def holdout_error(samples: Sequence[Tuple[Sample, float]], folds: int = 5) -> float:
    """Median multiplicative error under k-fold holdout, for the run report."""
    errs = []
    for k in range(folds):
        train = [x for i, x in enumerate(samples) if i % folds != k]
        test = [x for i, x in enumerate(samples) if i % folds == k]
        if not train or not test:
            continue
        m = fit(train)
        for s, price in test:
            errs.append(abs(math.log(predict(m, s)) - math.log(price)))
    return math.exp(statistics.median(errs)) if errs else float("nan")
