"""Merge calibrated BuyPrice-fallback prices into the override SQL.

See calibrate.py for the fitting itself and the README section "Calibrated
BuyPrice fallback" for the export commands that produce the inputs.
"""
import argparse
import csv
from collections import namedtuple

import calibrate
import sqlio

# An item_template row plus whether a vendor sells it freely for gold.
Row = namedtuple("Row", "item vendor")

RECIPE_CLASS = 9


def load_items(path):
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            parts = line.split()
            if len(parts) != 7 or not parts[0].isdigit():
                continue
            entry, cls, sub, quality, buy, sell, vendor = (int(p) for p in parts)
            rows.append(Row(calibrate.Item(entry, cls, sub, quality, buy, sell),
                            vendor))
    return rows


def load_ids(path):
    if not path:
        return set()
    with open(path, encoding="utf-8") as fh:
        return {int(line.split()[0]) for line in fh
                if line.strip() and line.split()[0].isdigit()}


def select_gap(rows, overrides, disabled, exclude_classes):
    """Items with no market price that the AH bot would otherwise price off
    the useless SellPrice anchor."""
    return [r.item for r in rows
            if r.item.entry not in overrides
            and r.item.entry not in disabled
            and r.item.sell_price > 0
            and not r.vendor
            and r.item.quality > 0
            and r.item.cls not in exclude_classes]


def read_derived(fh):
    """Item IDs this tool priced on an earlier run, from its report CSV."""
    return {int(row["item"]) for row in csv.DictReader(fh)
            if row.get("tier") != calibrate.NO_ANCHOR}


def select_fit(rows, overrides, disabled, derived=frozenset()):
    """Items whose market price is real enough to fit a multiplier on.

    A row this tool wrote on an earlier run is a fabricated price, not a market
    observation. Feeding it back in would let the fit reinforce itself and let
    a sparse bucket look well populated, so prior output is excluded."""
    return [r.item for r in rows
            if r.item.entry in overrides
            and r.item.entry not in disabled
            and r.item.entry not in derived]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--items-tsv", required=True,
                   help="entry class subclass quality BuyPrice SellPrice vendor")
    p.add_argument("--existing-sql", required=True)
    p.add_argument("--disabled-csv", default=None,
                   help="item IDs the trash filter disables; they never list")
    p.add_argument("--out-sql", default=None, help="defaults to --existing-sql")
    p.add_argument("--report", default="calibrated-fallback.csv")
    p.add_argument("--prior-report", action="append", default=[],
                   help="report CSV from an earlier run; its items are kept "
                        "out of the fit. Repeatable.")
    p.add_argument("--min-bucket", type=int, default=30)
    p.add_argument("--cap-percentile", type=float, default=0.99)
    p.add_argument("--exclude-classes", default=str(RECIPE_CLASS),
                   help="comma-separated item classes to leave unpriced")
    args = p.parse_args()

    if args.min_bucket < 1:
        p.error("--min-bucket must be at least 1")

    out_sql = args.out_sql or args.existing_sql
    cfg = calibrate.Config(args.min_bucket, args.cap_percentile)
    exclude = {int(c) for c in args.exclude_classes.split(",") if c.strip()}

    rows = load_items(args.items_tsv)
    disabled = load_ids(args.disabled_csv)
    existing = sqlio.parse_override_sql(args.existing_sql)

    derived_before = set()
    for path in args.prior_report:
        with open(path, encoding="utf-8") as fh:
            derived_before |= read_derived(fh)

    fit_items = select_fit(rows, set(existing), disabled, derived_before)
    market = [calibrate.Market(it.entry, *existing[it.entry]) for it in fit_items]
    calibration = calibrate.fit(fit_items, market, cfg)

    gap = select_gap(rows, set(existing), disabled, exclude)
    derived, report = calibrate.derive_rows(gap, calibration)

    merged = [(entry, avg, mn) for entry, (avg, mn) in existing.items()]
    merged.extend(derived)
    sqlio.write_override_sql(out_sql, merged)

    with open(args.report, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["item", "tier", "multiplier", "avg", "min", "capped",
                    "floored"])
        for r in report:
            w.writerow([r["item"], r["tier"], r["multiplier"], r["avg"],
                        r["min"], int(r["capped"]), int(r["floored"])])

    print("fit=%d gap=%d derived=%d merged=%d -> %s | report %s"
          % (len(fit_items), len(gap), len(derived), len(merged), out_sql,
             args.report))


if __name__ == "__main__":
    main()
