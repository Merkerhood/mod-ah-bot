"""Merge reagent-cost-derived prices for craftable gap items into the override SQL."""
import argparse
import csv
import json

import recipe_prices
import sqlio


def load_recipes(path):
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    return {int(k): (int(v[0]), [(int(a), int(b)) for a, b in v[1]]) for k, v in raw.items()}


def load_vendor(path):
    vendor = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            parts = line.replace("\t", " ").split()
            if len(parts) >= 2 and parts[0].isdigit() and parts[1].lstrip("-").isdigit():
                vendor[int(parts[0])] = int(parts[1])
    return vendor


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--recipes-json", required=True)
    p.add_argument("--existing-sql", required=True)
    p.add_argument("--vendor-csv", required=True)
    p.add_argument("--out-sql", default=None, help="defaults to --existing-sql")
    p.add_argument("--report", default="derived-prices.csv")
    p.add_argument("--margin-avg", type=float, default=1.3)
    p.add_argument("--margin-min", type=float, default=1.0)
    p.add_argument("--max-depth", type=int, default=10)
    args = p.parse_args()

    out_sql = args.out_sql or args.existing_sql
    cfg = recipe_prices.DeriveConfig(args.margin_avg, args.margin_min, args.max_depth)

    recipes = load_recipes(args.recipes_json)
    vendor = load_vendor(args.vendor_csv)
    existing = sqlio.parse_override_sql(args.existing_sql)  # {item:(avg,min)}
    overrides = {item: avg for item, (avg, _mn) in existing.items()}

    rows, report = recipe_prices.derive_rows(recipes, overrides, vendor, cfg)

    merged = [(item, avg, mn) for item, (avg, mn) in existing.items()]
    have = set(existing)
    for item, avg, mn in rows:
        if item not in have:
            merged.append((item, avg, mn))
    sqlio.write_override_sql(out_sql, merged)

    with open(args.report, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["item", "cost", "avg_price", "min_price", "reagent_sources"])
        for r in report:
            w.writerow([r["item"], r["cost"], r["avg"], r["min"], r["reagent_sources"]])

    print("existing=%d derived=%d merged=%d -> %s | report %s"
          % (len(existing), len(rows), len(merged), out_sql, args.report))


if __name__ == "__main__":
    main()
