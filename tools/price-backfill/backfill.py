"""Regenerate the AH-bot price override SQL from ChromieCraft auction data."""
import argparse
import csv
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import sqlio
import transform
import wowauctions


def read_candidate_ids(path):
    ids = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            tok = line.strip().split(",")[0].strip()
            if tok.isdigit():
                ids.append(int(tok))
    return ids


def load_checkpoint(path):
    done = {}
    if not os.path.exists(path):
        return done
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            done[rec["item"]] = rec
    return done


class BuildIdState:
    """Thread-safe buildId holder with redeploy re-resolution."""

    def __init__(self, build_id, sentinel_id=4389):
        self._lock = threading.Lock()
        self.build_id = build_id
        self.generation = 0
        self.consecutive_404 = 0
        self.sentinel_id = sentinel_id  # known-good item; confirms a real redeploy

    def snapshot(self):
        with self._lock:
            return self.build_id, self.generation

    def note_result(self, was_404, threshold):
        with self._lock:
            if not was_404:
                self.consecutive_404 = 0
                return self.build_id
            self.consecutive_404 += 1
            if self.consecutive_404 < threshold:
                return self.build_id
            # A run of 404s usually just means a stretch of items ChromieCraft has
            # no data for -- NOT a site redeploy. Confirm with a known-good sentinel
            # item: only a genuine redeploy makes the current buildId 404 for an
            # item that always has data. This stops no-data streaks from triggering
            # a needless buildId re-resolve storm and recovery re-fetch.
            self.consecutive_404 = 0
            try:
                sentinel = wowauctions.fetch_item(self.build_id, self.sentinel_id)
            except Exception:
                return self.build_id  # transient error; do not assume redeploy
            if sentinel is not None:
                return self.build_id  # sentinel still resolves -> buildId is fine
            # Sentinel 404s under the current buildId -> real redeploy. Re-resolve,
            # and bump the generation only if the buildId actually changed, so the
            # recovery pass stays scoped to items fetched under the stale buildId.
            new_build_id = wowauctions.resolve_build_id()
            if new_build_id != self.build_id:
                self.build_id = new_build_id
                self.generation += 1
            return self.build_id


def process_id(item_id, state, cfg, existing, now, rate_delay, redeploy_threshold, ckpt_fh, ckpt_lock):
    time.sleep(rate_delay)
    build_id, gen = state.snapshot()
    try:
        try:
            item = wowauctions.fetch_item(build_id, item_id)
        except Exception:  # network/HTTP error survived retries; record and move on
            rec = {"item": item_id, "row": None, "reason": "fetch-failed", "gen": gen}
        else:
            state.note_result(item is None, redeploy_threshold)
            if item is None:
                rec = {"item": item_id, "row": None, "reason": "no-data", "gen": gen}
            else:
                row, reason = transform.build_row(item_id, item, cfg, now)
                dev = None
                if row is not None:
                    name = (item.get("item_info") or {}).get("name", "")
                    dev = transform.compute_deviation(
                        row, existing.get(item_id), name, item.get("stats") or {},
                        cfg.deviation_factor)
                rec = {
                    "item": item_id,
                    "row": list(row) if row else None,
                    "reason": reason,
                    "deviation": list(dev) if dev else None,
                    "gen": gen,
                }
    except Exception:  # transform/deviation/other failure; record, never drop
        rec = {"item": item_id, "row": None, "reason": "error", "gen": gen}
    with ckpt_lock:
        ckpt_fh.write(json.dumps(rec) + "\n")
        ckpt_fh.flush()
    return rec


def write_outputs(records, out_sql, skipped_csv, deviations_csv):
    rows = [tuple(r["row"]) for r in records if r.get("row")]
    sqlio.write_override_sql(out_sql, rows)

    with open(skipped_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["item", "reason"])
        for r in records:
            if r.get("reason"):
                w.writerow([r["item"], r["reason"]])

    devs = [r["deviation"] for r in records if r.get("deviation")]
    devs.sort(key=lambda d: max(d[6], d[7]), reverse=True)  # avg_ratio, min_ratio
    with open(deviations_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["item", "item_name", "existing_avg", "existing_min", "cc_avg",
                    "cc_min", "avg_ratio", "min_ratio", "cc_item_count", "cc_last_seen"])
        w.writerows(devs)
    return len(rows), len(devs)


def select_recovery_ids(records, final_gen):
    """Item IDs recorded as no-data under a build generation older than the
    current one — these 404'd against a stale buildId and deserve a re-fetch."""
    return [r["item"] for r in records
            if r.get("reason") == "no-data" and r.get("gen", 0) < final_gen]


def select_fetch_failed(records):
    """Items whose fetch errored out (transient HTTP/network) — worth another try."""
    return [r["item"] for r in records if r.get("reason") == "fetch-failed"]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ids-csv", required=True)
    p.add_argument("--existing-sql", required=True)
    p.add_argument("--out-sql", default=None, help="defaults to --existing-sql")
    p.add_argument("--skipped-csv", default="skipped.csv")
    p.add_argument("--deviations-csv", default="deviations.csv")
    p.add_argument("--checkpoint", default="checkpoint.jsonl")
    p.add_argument("--resume", action="store_true")
    p.add_argument("--price-scale", type=float, default=1.0)
    p.add_argument("--min-item-count", type=int, default=1)
    p.add_argument("--max-age-days", type=int, default=180)
    p.add_argument("--deviation-factor", type=float, default=1.5)
    p.add_argument("--concurrency", type=int, default=6)
    p.add_argument("--rate-delay", type=float, default=0.15)
    p.add_argument("--redeploy-threshold", type=int, default=25,
                   help="consecutive 404s before a sentinel check for a real redeploy")
    p.add_argument("--sentinel-id", type=int, default=4389,
                   help="known-good item id used to confirm a real site redeploy")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--resweep-rounds", type=int, default=2,
                   help="extra passes to retry transient fetch-failed items")
    args = p.parse_args()

    out_sql = args.out_sql or args.existing_sql
    cfg = transform.Config(args.price_scale, args.min_item_count,
                           args.max_age_days, args.deviation_factor)
    now = datetime.now()

    existing = (sqlio.parse_override_sql(args.existing_sql)
                if os.path.exists(args.existing_sql) else {})
    ids = read_candidate_ids(args.ids_csv)
    if args.limit:
        ids = ids[:args.limit]

    done = load_checkpoint(args.checkpoint) if args.resume else {}
    todo = [i for i in ids if i not in done]
    print("candidates={} already_done={} todo={}".format(len(ids), len(done), len(todo)))

    state = BuildIdState(wowauctions.resolve_build_id(), sentinel_id=args.sentinel_id)
    ckpt_lock = threading.Lock()
    mode = "a" if args.resume else "w"
    with open(args.checkpoint, mode, encoding="utf-8") as ckpt_fh:
        def run_pass(id_list):
            with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
                futs = [ex.submit(process_id, i, state, cfg, existing, now,
                                  args.rate_delay, args.redeploy_threshold,
                                  ckpt_fh, ckpt_lock) for i in id_list]
                done_n = 0
                for fut in as_completed(futs):
                    fut.result()
                    done_n += 1
                    if done_n % 500 == 0:
                        print("processed {}/{}".format(done_n, len(id_list)))

        run_pass(todo)

        # Recovery: a mid-run redeploy (generation bumped) falsely 404'd items
        # fetched under the stale buildId. Re-fetch just those with the new one.
        final_gen = state.generation
        if final_gen > 0:
            recovery = select_recovery_ids(
                list(load_checkpoint(args.checkpoint).values()), final_gen)
            if recovery:
                print("redeploy detected (gen={}); recovery re-fetch of {} items".format(
                    final_gen, len(recovery)))
                run_pass(recovery)

        # Re-sweep transient fetch failures: HTTP/network errors that survived
        # per-request retries are usually transient over a long run, so re-fetch
        # them for a few bounded rounds until none remain.
        for _ in range(args.resweep_rounds):
            failed = select_fetch_failed(list(load_checkpoint(args.checkpoint).values()))
            if not failed:
                break
            print("re-sweeping {} fetch-failed items".format(len(failed)))
            run_pass(failed)

    records = list(load_checkpoint(args.checkpoint).values())
    kept, ndev = write_outputs(records, out_sql, args.skipped_csv, args.deviations_csv)
    print("wrote {} rows to {} | {} deviations | build_gen={}".format(
        kept, out_sql, ndev, state.generation))


if __name__ == "__main__":
    main()
