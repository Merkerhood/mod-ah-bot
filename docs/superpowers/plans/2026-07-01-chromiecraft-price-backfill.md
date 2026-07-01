# ChromieCraft Price Backfill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A standalone Python tool that regenerates `mod_auctionhousebot_priceOverride.sql` from ChromieCraft's live auction data and flags items whose new price deviates ≥1.5× from the shipped override.

**Architecture:** Five stages — parse existing SQL, read candidate item IDs (exported from `acore_world.item_template`), fetch per-item price stats from wowauctions.net's Next.js `_next/data` JSON endpoint, validate/transform into price rows, then emit the regenerated SQL plus a `skipped.csv` and a `deviations.csv` review worklist. Pure stdlib, no third-party packages.

**Tech Stack:** Python 3.12, stdlib only (`urllib`, `json`, `re`, `csv`, `concurrent.futures`, `datetime`, `argparse`, `unittest`).

## Global Constraints

- Zero third-party dependencies — stdlib only (no pip installs on the host).
- All modules live in `tools/price-backfill/` in the `mod-ah-bot` repo; tests sit alongside modules, run via `python3 -m unittest discover` from that directory.
- HTTP requests MUST send the browser User-Agent `Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36` (plain fetches get 403).
- Query wowauctions.net by numeric item ID only (name slug is ignored); endpoint path: `/_next/data/{buildId}/auctionHouse/chromie-craft/chromiecraft/mergedAh/x-{itemId}.json`.
- Field mapping: `stats.avg_price` → avgPrice, `stats.minimum_buyout` → minPrice, floor both at `item_info.SellPrice`, clamp `minPrice <= avgPrice`.
- Defaults: `PRICE_SCALE=1.0`, `MIN_ITEM_COUNT=1`, `MAX_AGE_DAYS=180`, `DEVIATION_FACTOR=1.5`, `CONCURRENCY=6`.
- Regenerated SQL MUST reproduce the shipped file format byte-for-byte (header, `INSERT INTO \`mod_auctionhousebot_priceOverride\` VALUES (item, avg, min);` per row sorted ascending by item, trailing `SET FOREIGN_KEY_CHECKS = 1;`).

---

### Task 1: SQL parse + emit (`sqlio.py`)

**Files:**
- Create: `tools/price-backfill/sqlio.py`
- Test: `tools/price-backfill/test_sqlio.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `parse_override_sql(path: str) -> dict[int, tuple[int, int]]` — maps item → (avgPrice, minPrice).
  - `write_override_sql(path: str, rows: Iterable[tuple[int, int, int]]) -> None` — writes rows `(item, avg, min)` sorted ascending by item, in the shipped format.
  - Module constant `TABLE = "mod_auctionhousebot_priceOverride"`.

- [ ] **Step 1: Write the failing test**

```python
# tools/price-backfill/test_sqlio.py
import os
import tempfile
import unittest

import sqlio


SAMPLE = (
    "SET NAMES utf8mb4;\n"
    "SET FOREIGN_KEY_CHECKS = 0;\n"
    "\n"
    "DROP TABLE IF EXISTS `mod_auctionhousebot_priceOverride`;\n"
    "INSERT INTO `mod_auctionhousebot_priceOverride` VALUES (35, 1667656, 1000594);\n"
    "INSERT INTO `mod_auctionhousebot_priceOverride` VALUES (36, 8621, 6950);\n"
    "\n"
    "SET FOREIGN_KEY_CHECKS = 1;\n"
)


class ParseTest(unittest.TestCase):
    def test_parses_item_avg_min(self):
        with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False) as fh:
            fh.write(SAMPLE)
            path = fh.name
        try:
            result = sqlio.parse_override_sql(path)
        finally:
            os.unlink(path)
        self.assertEqual(result, {35: (1667656, 1000594), 36: (8621, 6950)})


class EmitTest(unittest.TestCase):
    def test_roundtrip_and_sorting(self):
        with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False) as fh:
            path = fh.name
        try:
            sqlio.write_override_sql(path, [(36, 8621, 6950), (35, 1667656, 1000594)])
            text = open(path, encoding="utf-8").read()
            reparsed = sqlio.parse_override_sql(path)
        finally:
            os.unlink(path)
        self.assertEqual(reparsed, {35: (1667656, 1000594), 36: (8621, 6950)})
        # sorted ascending: item 35 line appears before item 36 line
        self.assertLess(text.index("VALUES (35,"), text.index("VALUES (36,"))
        # format fidelity
        self.assertIn(
            "INSERT INTO `mod_auctionhousebot_priceOverride` VALUES (35, 1667656, 1000594);",
            text,
        )
        self.assertTrue(text.startswith("SET NAMES utf8mb4;\n"))
        self.assertTrue(text.rstrip().endswith("SET FOREIGN_KEY_CHECKS = 1;"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd tools/price-backfill && python3 -m unittest test_sqlio -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sqlio'`.

- [ ] **Step 3: Write minimal implementation**

```python
# tools/price-backfill/sqlio.py
"""Parse and emit the mod_auctionhousebot_priceOverride SQL file."""
import re
from typing import Dict, Iterable, Tuple

TABLE = "mod_auctionhousebot_priceOverride"

_INSERT_RE = re.compile(
    r"INSERT INTO `" + TABLE + r"` VALUES \((\d+),\s*(-?\d+),\s*(-?\d+)\);"
)

_HEADER = (
    "SET NAMES utf8mb4;\n"
    "SET FOREIGN_KEY_CHECKS = 0;\n"
    "\n"
    "-- ----------------------------\n"
    "-- Table structure for " + TABLE + "\n"
    "-- ----------------------------\n"
    "DROP TABLE IF EXISTS `" + TABLE + "`;\n"
    "CREATE TABLE `" + TABLE + "`  (\n"
    "  `item` mediumint(8) NOT NULL,\n"
    "  `avgPrice` bigint(20) NOT NULL,\n"
    "  `minPrice` bigint(20) NOT NULL,\n"
    "  PRIMARY KEY (`item`) USING BTREE\n"
    ") ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci ROW_FORMAT = Dynamic;\n"
    "\n"
    "-- ----------------------------\n"
    "-- Records of " + TABLE + "\n"
    "-- ----------------------------\n"
)
_FOOTER = "\nSET FOREIGN_KEY_CHECKS = 1;\n"


def parse_override_sql(path: str) -> Dict[int, Tuple[int, int]]:
    result: Dict[int, Tuple[int, int]] = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            m = _INSERT_RE.search(line)
            if m:
                result[int(m.group(1))] = (int(m.group(2)), int(m.group(3)))
    return result


def write_override_sql(path: str, rows: Iterable[Tuple[int, int, int]]) -> None:
    ordered = sorted(rows, key=lambda r: r[0])
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(_HEADER)
        for item, avg, mn in ordered:
            fh.write(
                "INSERT INTO `" + TABLE + "` VALUES "
                "({}, {}, {});\n".format(item, avg, mn)
            )
        fh.write(_FOOTER)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd tools/price-backfill && python3 -m unittest test_sqlio -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add tools/price-backfill/sqlio.py tools/price-backfill/test_sqlio.py
git commit -m "feat(backfill): SQL parse and emit for price override table"
```

---

### Task 2: Validation, transform, deviation (`transform.py`)

**Files:**
- Create: `tools/price-backfill/transform.py`
- Test: `tools/price-backfill/test_transform.py`

**Interfaces:**
- Consumes: nothing (operates on plain dicts from the fetch stage).
- Produces:
  - `Config = namedtuple("Config", "price_scale min_item_count max_age_days deviation_factor")`.
  - `PriceRow = namedtuple("PriceRow", "item avg_price min_price")`.
  - `Deviation = namedtuple("Deviation", "item item_name existing_avg existing_min cc_avg cc_min avg_ratio min_ratio cc_item_count cc_last_seen")`.
  - `build_row(item_id: int, item: dict, cfg: Config, now: datetime) -> tuple[Optional[PriceRow], Optional[str]]` — returns `(PriceRow, None)` on success or `(None, reason)` where reason ∈ `{"no-data","thin","stale","zero-price"}`. `item` is `pageProps["item"]`.
  - `compute_deviation(row: PriceRow, existing: Optional[tuple[int,int]], item_name: str, stats: dict, factor: float) -> Optional[Deviation]`.

- [ ] **Step 1: Write the failing test**

```python
# tools/price-backfill/test_transform.py
import unittest
from datetime import datetime

import transform
from transform import Config, PriceRow


NOW = datetime(2026, 7, 1, 12, 0, 0)
CFG = Config(price_scale=1.0, min_item_count=1, max_age_days=180, deviation_factor=1.5)


def item_with(stats, sell_price=0):
    return {"item_info": {"SellPrice": sell_price}, "stats": stats}


class BuildRowTest(unittest.TestCase):
    def test_happy_path_maps_avg_and_min(self):
        item = item_with(
            {"avg_price": 4999, "minimum_buyout": 4850, "item_count": 53,
             "item_last_seen": "2026-07-01 11:45:09"},
            sell_price=750,
        )
        row, reason = transform.build_row(4389, item, CFG, NOW)
        self.assertIsNone(reason)
        self.assertEqual(row, PriceRow(4389, 4999, 4850))

    def test_no_stats_is_no_data(self):
        row, reason = transform.build_row(1, {"item_info": {}, "stats": None}, CFG, NOW)
        self.assertIsNone(row)
        self.assertEqual(reason, "no-data")

    def test_thin_below_min_count(self):
        cfg = CFG._replace(min_item_count=3)
        item = item_with({"avg_price": 10, "minimum_buyout": 10, "item_count": 1,
                          "item_last_seen": "2026-07-01 11:45:09"})
        row, reason = transform.build_row(1, item, cfg, NOW)
        self.assertIsNone(row)
        self.assertEqual(reason, "thin")

    def test_stale_beyond_max_age(self):
        item = item_with({"avg_price": 10, "minimum_buyout": 10, "item_count": 5,
                          "item_last_seen": "2025-01-01 00:00:00"})
        row, reason = transform.build_row(1, item, CFG, NOW)
        self.assertIsNone(row)
        self.assertEqual(reason, "stale")

    def test_min_clamped_to_avg(self):
        item = item_with({"avg_price": 100, "minimum_buyout": 250, "item_count": 5,
                          "item_last_seen": "2026-07-01 11:45:09"})
        row, _ = transform.build_row(1, item, CFG, NOW)
        self.assertEqual(row, PriceRow(1, 100, 100))

    def test_vendor_floor_applied(self):
        item = item_with({"avg_price": 5, "minimum_buyout": 5, "item_count": 5,
                          "item_last_seen": "2026-07-01 11:45:09"}, sell_price=90)
        row, _ = transform.build_row(1, item, CFG, NOW)
        self.assertEqual(row, PriceRow(1, 90, 90))

    def test_zero_price_rejected(self):
        item = item_with({"avg_price": 0, "minimum_buyout": 0, "item_count": 5,
                          "item_last_seen": "2026-07-01 11:45:09"}, sell_price=0)
        row, reason = transform.build_row(1, item, CFG, NOW)
        self.assertIsNone(row)
        self.assertEqual(reason, "zero-price")

    def test_price_scale_applied(self):
        cfg = CFG._replace(price_scale=0.5)
        item = item_with({"avg_price": 100, "minimum_buyout": 80, "item_count": 5,
                          "item_last_seen": "2026-07-01 11:45:09"})
        row, _ = transform.build_row(1, item, cfg, NOW)
        self.assertEqual(row, PriceRow(1, 50, 40))


class DeviationTest(unittest.TestCase):
    def test_flags_when_ratio_at_or_above_factor(self):
        row = PriceRow(35, 300, 300)
        dev = transform.compute_deviation(
            row, existing=(100, 100), item_name="Thing",
            stats={"item_count": 5, "item_last_seen": "2026-07-01 11:45:09"}, factor=1.5)
        self.assertIsNotNone(dev)
        self.assertEqual(dev.avg_ratio, 3.0)

    def test_not_flagged_when_below_factor(self):
        row = PriceRow(35, 140, 140)
        dev = transform.compute_deviation(
            row, existing=(100, 100), item_name="Thing",
            stats={"item_count": 5, "item_last_seen": "x"}, factor=1.5)
        self.assertIsNone(dev)

    def test_no_existing_never_flags(self):
        row = PriceRow(35, 999999, 999999)
        dev = transform.compute_deviation(
            row, existing=None, item_name="Thing",
            stats={"item_count": 5, "item_last_seen": "x"}, factor=1.5)
        self.assertIsNone(dev)

    def test_cheaper_direction_also_flags(self):
        row = PriceRow(35, 100, 100)  # existing 500 -> 5x cheaper
        dev = transform.compute_deviation(
            row, existing=(500, 500), item_name="Thing",
            stats={"item_count": 5, "item_last_seen": "x"}, factor=1.5)
        self.assertIsNotNone(dev)
        self.assertEqual(dev.avg_ratio, 5.0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd tools/price-backfill && python3 -m unittest test_transform -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'transform'`.

- [ ] **Step 3: Write minimal implementation**

```python
# tools/price-backfill/transform.py
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd tools/price-backfill && python3 -m unittest test_transform -v`
Expected: PASS (12 tests).

- [ ] **Step 5: Commit**

```bash
git add tools/price-backfill/transform.py tools/price-backfill/test_transform.py
git commit -m "feat(backfill): stats validation, transform, and deviation detection"
```

---

### Task 3: wowauctions.net HTTP client (`wowauctions.py`)

**Files:**
- Create: `tools/price-backfill/wowauctions.py`
- Test: `tools/price-backfill/test_wowauctions.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `resolve_build_id() -> str` — GET homepage, extract `buildId` from `__NEXT_DATA__`.
  - `data_url(build_id: str, item_id: int) -> str` — build the `_next/data` JSON URL.
  - `fetch_item(build_id: str, item_id: int, timeout: int = 20) -> Optional[dict]` — returns `pageProps["item"]` dict, or `None` on HTTP 404 (unknown item).
  - Module constant `UA` (the browser User-Agent).

- [ ] **Step 1: Write the failing test**

```python
# tools/price-backfill/test_wowauctions.py
import io
import json
import unittest
import urllib.error
from unittest import mock

import wowauctions


class UrlTest(unittest.TestCase):
    def test_data_url_uses_id_only(self):
        url = wowauctions.data_url("BUILD123", 4389)
        self.assertEqual(
            url,
            "https://www.wowauctions.net/_next/data/BUILD123/auctionHouse/"
            "chromie-craft/chromiecraft/mergedAh/x-4389.json",
        )


class ResolveBuildIdTest(unittest.TestCase):
    def test_extracts_build_id(self):
        html = '<script>{"props":{},"buildId":"pWPDRVbFX9AWIBQ1u1tR8"}</script>'
        with mock.patch.object(wowauctions, "_get", return_value=html):
            self.assertEqual(wowauctions.resolve_build_id(), "pWPDRVbFX9AWIBQ1u1tR8")

    def test_raises_when_missing(self):
        with mock.patch.object(wowauctions, "_get", return_value="<html></html>"):
            with self.assertRaises(RuntimeError):
                wowauctions.resolve_build_id()


class FetchItemTest(unittest.TestCase):
    def test_returns_item_dict(self):
        payload = json.dumps({"pageProps": {"item": {"stats": {"avg_price": 5}}}})
        with mock.patch.object(wowauctions, "_get", return_value=payload):
            item = wowauctions.fetch_item("BUILD123", 4389)
        self.assertEqual(item, {"stats": {"avg_price": 5}})

    def test_404_returns_none(self):
        err = urllib.error.HTTPError("u", 404, "nf", {}, io.BytesIO(b""))
        with mock.patch.object(wowauctions, "_get", side_effect=err):
            self.assertIsNone(wowauctions.fetch_item("BUILD123", 999999))

    def test_other_http_error_reraised(self):
        err = urllib.error.HTTPError("u", 500, "err", {}, io.BytesIO(b""))
        with mock.patch.object(wowauctions, "_get", side_effect=err):
            with self.assertRaises(urllib.error.HTTPError):
                wowauctions.fetch_item("BUILD123", 4389)


class RetryTest(unittest.TestCase):
    def test_retries_on_500_then_succeeds(self):
        err = urllib.error.HTTPError("u", 500, "err", {}, io.BytesIO(b""))
        calls = [err, err, "ok"]

        def fake_raw(url, timeout):
            v = calls.pop(0)
            if isinstance(v, Exception):
                raise v
            return v

        with mock.patch.object(wowauctions, "_raw_get", side_effect=fake_raw):
            with mock.patch.object(wowauctions.time, "sleep", return_value=None):
                self.assertEqual(wowauctions._get("http://x", retries=3, backoff=0), "ok")

    def test_404_not_retried(self):
        err = urllib.error.HTTPError("u", 404, "nf", {}, io.BytesIO(b""))
        with mock.patch.object(wowauctions, "_raw_get", side_effect=err):
            with self.assertRaises(urllib.error.HTTPError):
                wowauctions._get("http://x", retries=3, backoff=0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd tools/price-backfill && python3 -m unittest test_wowauctions -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'wowauctions'`.

- [ ] **Step 3: Write minimal implementation**

```python
# tools/price-backfill/wowauctions.py
"""HTTP client for the wowauctions.net ChromieCraft Next.js data endpoint."""
import json
import re
import time
import urllib.error
import urllib.request
from typing import Optional

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
BASE = "https://www.wowauctions.net"
_BUILDID_RE = re.compile(r'"buildId":"([^"]+)"')


def _raw_get(url: str, timeout: int) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


def _get(url: str, timeout: int = 20, retries: int = 3, backoff: float = 1.0) -> str:
    """GET with exponential backoff on 429/5xx and network errors. 404 is raised immediately."""
    attempt = 0
    while True:
        try:
            return _raw_get(url, timeout)
        except urllib.error.HTTPError as e:
            if e.code == 404 or e.code < 500 and e.code != 429:
                raise
            last = e
        except urllib.error.URLError as e:
            last = e
        attempt += 1
        if attempt > retries:
            raise last
        time.sleep(backoff * (2 ** (attempt - 1)))


def resolve_build_id() -> str:
    html = _get(BASE + "/")
    m = _BUILDID_RE.search(html)
    if not m:
        raise RuntimeError("buildId not found on wowauctions.net homepage")
    return m.group(1)


def data_url(build_id: str, item_id: int) -> str:
    return ("{}/_next/data/{}/auctionHouse/chromie-craft/chromiecraft/mergedAh/"
            "x-{}.json").format(BASE, build_id, item_id)


def fetch_item(build_id: str, item_id: int, timeout: int = 20) -> Optional[dict]:
    try:
        body = _get(data_url(build_id, item_id), timeout=timeout)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    data = json.loads(body)
    return data.get("pageProps", {}).get("item")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd tools/price-backfill && python3 -m unittest test_wowauctions -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Live smoke check (manual, one call)**

Run:
```bash
cd tools/price-backfill && python3 -c "import wowauctions as w; b=w.resolve_build_id(); print('build', b); print(w.fetch_item(b, 4389)['stats'])"
```
Expected: prints a build id and a `stats` dict with `avg_price`, `minimum_buyout`, `item_count`, `item_last_seen`. (If it fails with a network error, note it — the tool still works offline for unit tests.)

- [ ] **Step 6: Commit**

```bash
git add tools/price-backfill/wowauctions.py tools/price-backfill/test_wowauctions.py
git commit -m "feat(backfill): wowauctions.net data-endpoint client"
```

---

### Task 4: Orchestrator, CLI, outputs, README (`backfill.py`)

**Files:**
- Create: `tools/price-backfill/backfill.py`
- Create: `tools/price-backfill/README.md`

**Interfaces:**
- Consumes: `sqlio.parse_override_sql`, `sqlio.write_override_sql`, `transform.Config/PriceRow/Deviation/build_row/compute_deviation`, `wowauctions.resolve_build_id/fetch_item`.
- Produces: a CLI entrypoint (no importable API required by later tasks).

**Behavior:**
- Reads candidate item IDs from `--ids-csv` (one integer per line, or first column; header row tolerated by skipping non-numeric lines).
- Parses the existing SQL (`--existing-sql`) for deviation comparison; missing file → empty dict.
- Resolves `buildId` once at start. Fetches each ID concurrently (`--concurrency`, default 6) with a per-request `--rate-delay` (default 0.15s) sleep inside the worker.
- Guards against a mid-run site redeploy: a shared consecutive-404 counter; when it crosses `--redeploy-threshold` (default 25) one worker re-resolves `buildId` (guarded by a lock + generation number) and processing continues.
- Checkpoint/resume: append each processed `{item, row|null, reason|null}` as one JSON line to `--checkpoint` (default `checkpoint.jsonl`). On `--resume`, pre-load processed item IDs and skip re-fetching them.
- Classifies each result via `transform.build_row`; kept rows also run `transform.compute_deviation` against the existing override.
- Writes: regenerated SQL (`--out-sql`, default = the existing file path), `skipped.csv` (`item,reason`), `deviations.csv` (`item,item_name,existing_avg,existing_min,cc_avg,cc_min,avg_ratio,min_ratio,cc_item_count,cc_last_seen`, sorted by `max(avg_ratio,min_ratio)` descending).
- `--limit N` processes only the first N candidate IDs (smoke run).

- [ ] **Step 1: Write the orchestrator**

```python
# tools/price-backfill/backfill.py
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

    def __init__(self, build_id):
        self._lock = threading.Lock()
        self.build_id = build_id
        self.generation = 0
        self.consecutive_404 = 0

    def note_result(self, was_404, threshold):
        with self._lock:
            if was_404:
                self.consecutive_404 += 1
                if self.consecutive_404 >= threshold:
                    self.build_id = wowauctions.resolve_build_id()
                    self.generation += 1
                    self.consecutive_404 = 0
            else:
                self.consecutive_404 = 0
            return self.build_id


def process_id(item_id, state, cfg, existing, now, rate_delay, redeploy_threshold, ckpt_fh, ckpt_lock):
    time.sleep(rate_delay)
    try:
        item = wowauctions.fetch_item(state.build_id, item_id)
    except Exception:  # network/HTTP error survived retries; record and move on
        rec = {"item": item_id, "row": None, "reason": "fetch-failed"}
        with ckpt_lock:
            ckpt_fh.write(json.dumps(rec) + "\n")
            ckpt_fh.flush()
        return rec
    state.note_result(item is None, redeploy_threshold)
    if item is None:
        rec = {"item": item_id, "row": None, "reason": "no-data"}
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
        }
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
    p.add_argument("--redeploy-threshold", type=int, default=25)
    p.add_argument("--limit", type=int, default=0)
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

    state = BuildIdState(wowauctions.resolve_build_id())
    ckpt_lock = threading.Lock()
    mode = "a" if args.resume else "w"
    with open(args.checkpoint, mode, encoding="utf-8") as ckpt_fh:
        with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
            futs = [ex.submit(process_id, i, state, cfg, existing, now,
                              args.rate_delay, args.redeploy_threshold,
                              ckpt_fh, ckpt_lock) for i in todo]
            n = 0
            for _ in as_completed(futs):
                n += 1
                if n % 500 == 0:
                    print("processed {}/{}".format(n, len(todo)))

    records = list(load_checkpoint(args.checkpoint).values())
    kept, ndev = write_outputs(records, out_sql, args.skipped_csv, args.deviations_csv)
    print("wrote {} rows to {} | {} deviations | build_gen={}".format(
        kept, out_sql, ndev, state.generation))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify it runs end-to-end on a tiny smoke set (live)**

Run:
```bash
cd tools/price-backfill
printf "35\n36\n4389\n" > /tmp/ids_smoke.csv
python3 backfill.py --ids-csv /tmp/ids_smoke.csv \
  --existing-sql ../../data/sql/db-world/2023_11_16_mod_auctionhousebot_priceOverride.sql \
  --out-sql /tmp/out_smoke.sql --skipped-csv /tmp/skipped_smoke.csv \
  --deviations-csv /tmp/dev_smoke.csv --checkpoint /tmp/ckpt_smoke.jsonl --limit 3
head /tmp/out_smoke.sql /tmp/dev_smoke.csv /tmp/skipped_smoke.csv
```
Expected: prints `candidates=3 ... todo=3`, then `wrote N rows to /tmp/out_smoke.sql`. `/tmp/out_smoke.sql` has the header and up to 3 INSERT lines; `/tmp/dev_smoke.csv` has the header and any flagged rows. (Requires network to wowauctions.net.)

- [ ] **Step 3: Write the README**

```markdown
# ChromieCraft Price Backfill

Regenerates `data/sql/db-world/2023_11_16_mod_auctionhousebot_priceOverride.sql`
from ChromieCraft live auction data (via wowauctions.net).

## 1. Export candidate item IDs from the world DB

Run against the Merkerhood realm's `acore_world` (adjust host/creds):

```bash
mysql -N -B acore_world -e "
  SELECT entry FROM item_template
  WHERE bonding IN (0,2,3) AND Quality <= 5 AND class NOT IN (12,13)
  ORDER BY entry;" > candidates.csv
```

## 2. Run the backfill

```bash
python3 backfill.py \
  --ids-csv candidates.csv \
  --existing-sql ../../data/sql/db-world/2023_11_16_mod_auctionhousebot_priceOverride.sql
```

The out-SQL defaults to the existing file (in-place regen). Use `--out-sql`
to write elsewhere first if you want to diff before committing.

Interruptible: re-run with `--resume` to continue from `checkpoint.jsonl`.
Smoke test first with `--limit 50`.

## Outputs

- regenerated `.sql` (full refresh, ChromieCraft prices)
- `skipped.csv` — items with no/thin/stale/zero data (keep C++ default pricing)
- `deviations.csv` — items where the new price differs >=1.5x from the shipped
  override; the manual-review worklist, worst-first.

## Tuning

`--price-scale` (default 1.0), `--min-item-count` (1), `--max-age-days` (180),
`--deviation-factor` (1.5), `--concurrency` (6), `--rate-delay` (0.15s).

## Tests

```bash
python3 -m unittest discover -v
```
```

- [ ] **Step 4: Run the full unit suite**

Run: `cd tools/price-backfill && python3 -m unittest discover -v`
Expected: PASS (all tests from Tasks 1-3 plus this task's modules import cleanly).

- [ ] **Step 5: Commit**

```bash
git add tools/price-backfill/backfill.py tools/price-backfill/README.md
git commit -m "feat(backfill): orchestrator CLI, CSV outputs, and README"
```

---

## Notes for the implementer

- Run everything from `tools/price-backfill/` so the flat-module imports resolve.
- The full production run against ~15-20k candidate IDs will take a while at
  `rate-delay=0.15` × 6 workers (~roughly 6-9 min per 10k, network permitting).
  Always `--limit 50` first, eyeball `deviations.csv`, then do the full pull.
- Do not push the branch or commit the generated SQL to the remote without the
  maintainer's sign-off — the deviation worklist is meant to be reviewed first.
