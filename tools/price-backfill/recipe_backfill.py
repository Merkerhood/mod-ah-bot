"""Merge skill-rank-derived recipe prices into the override SQL.

Recipes (item class 9) that ChromieCraft has no market data for get a price from
their required skill rank and the value of the item they teach, fitted against
the recipes that DO carry a market price. See recipe_rank_prices for the model.
"""
import argparse
import csv
import json

import recipe_rank_prices as rrp
import sqlio


def load_items(path):
    """Read the class-9 export (see README) into rows."""
    with open(path, encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        return [{k: int(v) for k, v in row.items()} for row in reader]


def build(rows, spell_to_item, existing):
    """Split the export into (training samples, target samples)."""
    train, targets = [], []
    for r in rows:
        if r["rank"] <= 0:
            continue
        product = existing.get(spell_to_item.get(r["teach_spell"], 0), (0, 0))[0]
        s = rrp.Sample(item=r["entry"], rank=r["rank"], skill=r["skill"],
                       quality=r["quality"], sell_price=r["sell_price"],
                       product_price=product)
        if s.item in existing:
            train.append((s, float(existing[s.item][0])))
        elif r["obtainable"] and not r["free_vendor"]:
            targets.append(s)
    return train, targets


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--items-csv", required=True, help="class-9 export, see README")
    p.add_argument("--spell-to-item-json", required=True)
    p.add_argument("--existing-sql", required=True)
    p.add_argument("--out-sql", default=None, help="defaults to --existing-sql")
    p.add_argument("--report", default="reports/recipe-rank-prices.csv")
    p.add_argument("--min-ratio", type=float, default=0.85,
                   help="minPrice as a fraction of avgPrice")
    p.add_argument("--min-bucket", type=int, default=8,
                   help="observations a rank bucket needs for its own clamp band")
    args = p.parse_args()

    out_sql = args.out_sql or args.existing_sql
    existing = sqlio.parse_override_sql(args.existing_sql)
    with open(args.spell_to_item_json, encoding="utf-8") as fh:
        spell_to_item = {int(k): int(v) for k, v in json.load(fh).items()}

    train, targets = build(load_items(args.items_csv), spell_to_item, existing)
    if len(train) < 100:
        raise SystemExit("only %d priced recipes to fit on; refusing" % len(train))

    model = rrp.fit(train)
    bands = rrp.rank_bands(train, min_bucket=args.min_bucket)
    rows, report = rrp.price_rows(model, bands, targets, existing, args.min_ratio)

    merged = [(item, avg, mn) for item, (avg, mn) in existing.items()]
    merged += rows
    sqlio.write_override_sql(out_sql, merged)

    with open(args.report, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["item", "rank", "skill", "product_price",
                                           "sell_price", "raw", "avg", "min", "clamp"])
        w.writeheader()
        w.writerows(report)

    print("fit on %d priced recipes | holdout median error x%.2f | priced %d of %d "
          "targets | merged %d -> %s | report %s"
          % (len(train), rrp.holdout_error(train), len(rows), len(targets),
             len(merged), out_sql, args.report))


if __name__ == "__main__":
    main()
