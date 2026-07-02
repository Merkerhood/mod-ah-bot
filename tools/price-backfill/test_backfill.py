"""Worker-exception handling: a bad item must be recorded, never dropped."""
import json
import os
import tempfile
import threading
import time
import unittest
from datetime import datetime

import backfill
import sqlio
import transform
import wowauctions


class ProcessIdExceptionTest(unittest.TestCase):
    def setUp(self):
        self._orig_fetch = wowauctions.fetch_item
        fd, self.ckpt = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)
        self.cfg = transform.Config(1.0, 1, 180, 1.5)
        self.state = backfill.BuildIdState("build123")

    def tearDown(self):
        wowauctions.fetch_item = self._orig_fetch
        if os.path.exists(self.ckpt):
            os.remove(self.ckpt)

    def _run(self, item_id):
        lock = threading.Lock()
        with open(self.ckpt, "w", encoding="utf-8") as fh:
            rec = backfill.process_id(
                item_id, self.state, self.cfg, {}, datetime.now(),
                0.0, 25, fh, lock)
        with open(self.ckpt, encoding="utf-8") as fh:
            lines = [json.loads(l) for l in fh if l.strip()]
        return rec, lines

    def test_fetch_error_recorded_not_dropped(self):
        def boom(build_id, item_id):
            raise ValueError("odd item")
        wowauctions.fetch_item = boom

        rec, lines = self._run(42)
        self.assertIsNone(rec["row"])
        self.assertEqual(rec["reason"], "fetch-failed")
        # the record is persisted to the checkpoint, not silently lost
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["item"], 42)
        self.assertEqual(lines[0]["reason"], "fetch-failed")

    def test_transform_error_recorded_as_error(self):
        # fetch succeeds, but transform.build_row blows up on an odd item
        wowauctions.fetch_item = lambda build_id, item_id: {"stats": {}}
        orig_build_row = transform.build_row
        transform.build_row = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bad"))
        try:
            rec, lines = self._run(43)
        finally:
            transform.build_row = orig_build_row
        self.assertIsNone(rec["row"])
        self.assertEqual(rec["reason"], "error")
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["reason"], "error")


class ItemCountCaptureTest(unittest.TestCase):
    def setUp(self):
        self._orig_fetch = wowauctions.fetch_item
        fd, self.ckpt = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)
        self.cfg = transform.Config(1.0, 1, 180, 1.5)
        self.state = backfill.BuildIdState("build123")

    def tearDown(self):
        wowauctions.fetch_item = self._orig_fetch
        if os.path.exists(self.ckpt):
            os.remove(self.ckpt)

    def test_cc_item_count_and_last_seen_captured(self):
        wowauctions.fetch_item = lambda build_id, item_id: {
            "stats": {
                "avg_price": 100,
                "minimum_buyout": 80,
                "item_count": 53,
                "item_last_seen": "2026-07-01 11:45:09",
            },
            "item_info": {"SellPrice": 10, "name": "X"},
        }
        lock = threading.Lock()
        with open(self.ckpt, "w", encoding="utf-8") as fh:
            rec = backfill.process_id(
                123, self.state, self.cfg, {}, datetime.now(),
                0.0, 25, fh, lock)
        self.assertEqual(rec["cc_item_count"], 53)
        self.assertEqual(rec["cc_last_seen"], "2026-07-01 11:45:09")


class SelectRecoveryIdsTest(unittest.TestCase):
    def test_select_recovery_ids(self):
        records = [
            {"item": 1, "reason": "no-data", "gen": 0},
            {"item": 2, "reason": "no-data", "gen": 1},
            {"item": 3, "reason": "thin", "gen": 0},
            {"item": 4, "row": [4, 10, 10], "reason": None, "gen": 0},
        ]
        self.assertEqual(backfill.select_recovery_ids(records, 1), [1])


class SelectFetchFailedTest(unittest.TestCase):
    def test_selects_only_fetch_failed(self):
        records = [
            {"item": 1, "reason": "fetch-failed"},
            {"item": 2, "reason": "no-data"},
            {"item": 3, "row": [3, 1, 1], "reason": None},
            {"item": 4, "reason": "fetch-failed"},
        ]
        self.assertEqual(sorted(backfill.select_fetch_failed(records)), [1, 4])


class NoteResultTest(unittest.TestCase):
    """Redeploy is confirmed via a sentinel item, not raw 404 counting."""

    def setUp(self):
        self._orig_fetch = wowauctions.fetch_item
        self._orig_resolve = wowauctions.resolve_build_id

    def tearDown(self):
        wowauctions.fetch_item = self._orig_fetch
        wowauctions.resolve_build_id = self._orig_resolve

    def test_sentinel_ok_means_no_reresolve(self):
        # A no-data streak: the sentinel still returns data, so despite crossing
        # the threshold we must NOT re-resolve or bump the generation.
        wowauctions.fetch_item = lambda b, i: {"stats": {"avg_price": 5}}
        wowauctions.resolve_build_id = lambda: (_ for _ in ()).throw(
            AssertionError("resolve_build_id must not be called on a false alarm"))
        state = backfill.BuildIdState("OLD", sentinel_id=4389)
        for _ in range(3):
            state.note_result(True, 3)
        self.assertEqual(state.build_id, "OLD")
        self.assertEqual(state.generation, 0)
        self.assertEqual(state.consecutive_404, 0)

    def test_sentinel_404_triggers_reresolve(self):
        # Genuine redeploy: sentinel 404s under the current buildId and the new
        # buildId differs -> re-resolve and bump generation.
        wowauctions.fetch_item = lambda b, i: None
        wowauctions.resolve_build_id = lambda: "NEWBUILD"
        state = backfill.BuildIdState("OLD", sentinel_id=4389)
        for _ in range(3):
            state.note_result(True, 3)
        self.assertEqual(state.build_id, "NEWBUILD")
        self.assertEqual(state.generation, 1)
        self.assertEqual(state.consecutive_404, 0)

    def test_sentinel_404_same_buildid_no_gen_bump(self):
        # Sentinel 404s but re-resolution returns the same buildId (transient /
        # sentinel genuinely gone) -> no generation bump, so no needless recovery.
        wowauctions.fetch_item = lambda b, i: None
        wowauctions.resolve_build_id = lambda: "OLD"
        state = backfill.BuildIdState("OLD", sentinel_id=4389)
        for _ in range(3):
            state.note_result(True, 3)
        self.assertEqual(state.build_id, "OLD")
        self.assertEqual(state.generation, 0)

    def test_non404_resets_counter(self):
        state = backfill.BuildIdState("OLD")
        state.note_result(True, 3)
        state.note_result(True, 3)
        self.assertEqual(state.consecutive_404, 2)
        state.note_result(False, 3)
        self.assertEqual(state.consecutive_404, 0)


class WriteOutputsMergeTest(unittest.TestCase):
    """--out-sql defaults to --existing-sql (in-place regeneration): an item
    already overridden must survive even if it's absent from this run's
    candidates, instead of silently reverting to default AH pricing."""

    def setUp(self):
        fd, self.out_sql = tempfile.mkstemp(suffix=".sql")
        os.close(fd)
        fd, self.skipped = tempfile.mkstemp(suffix=".csv")
        os.close(fd)
        fd, self.deviations = tempfile.mkstemp(suffix=".csv")
        os.close(fd)

    def tearDown(self):
        for path in (self.out_sql, self.skipped, self.deviations):
            if os.path.exists(path):
                os.remove(path)

    def test_existing_item_absent_from_candidates_survives(self):
        existing = {99: (500, 400), 1: (10, 5)}
        records = [{"item": 1, "row": [1, 20, 15], "reason": None}]
        backfill.write_outputs(records, existing, self.out_sql, self.skipped,
                                self.deviations)
        result = sqlio.parse_override_sql(self.out_sql)
        # item 99 was not part of this run's candidates but is a pre-existing
        # override -- it must be carried through unchanged.
        self.assertEqual(result[99], (500, 400))
        # item 1 was re-derived this run -- the new values win.
        self.assertEqual(result[1], (20, 15))


class NoteResultLockScopeTest(unittest.TestCase):
    """The lock must protect shared-state reads/writes only, not the
    sentinel-fetch / resolve_build_id network calls, so a redeploy-sentinel
    check on one thread doesn't collapse concurrency to 1 for everyone else."""

    def setUp(self):
        self._orig_fetch = wowauctions.fetch_item
        self._orig_resolve = wowauctions.resolve_build_id

    def tearDown(self):
        wowauctions.fetch_item = self._orig_fetch
        wowauctions.resolve_build_id = self._orig_resolve

    def test_lock_not_held_during_network_call(self):
        entered = threading.Event()
        release = threading.Event()

        def slow_fetch(build_id, item_id):
            entered.set()
            release.wait(2)
            return None  # sentinel 404s -> proceeds to resolve_build_id

        wowauctions.fetch_item = slow_fetch
        wowauctions.resolve_build_id = lambda: "OLD"  # buildId unchanged

        state = backfill.BuildIdState("OLD", sentinel_id=4389)
        t = threading.Thread(target=lambda: state.note_result(True, 1))
        t.start()
        self.assertTrue(entered.wait(2), "sentinel fetch never started")
        # The sentinel fetch is in flight; the lock must be free so other
        # threads (e.g. via snapshot()) are not blocked for the network RTT.
        acquired = state._lock.acquire(timeout=1)
        self.assertTrue(acquired, "lock is still held during network I/O")
        state._lock.release()
        release.set()
        t.join(2)

    def test_only_one_thread_performs_reresolve(self):
        resolve_calls = []
        call_lock = threading.Lock()

        def counting_resolve():
            with call_lock:
                resolve_calls.append(1)
            time.sleep(0.05)
            return "NEW"

        wowauctions.fetch_item = lambda build_id, item_id: None  # always 404
        wowauctions.resolve_build_id = counting_resolve

        state = backfill.BuildIdState("OLD", sentinel_id=4389)
        threads = [threading.Thread(target=lambda: state.note_result(True, 1))
                   for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(2)

        # Even though every thread crossed the threshold concurrently, only
        # one should have actually re-resolved; the rest proceed on the
        # (old, then updated) buildId per the single-flight design.
        self.assertEqual(len(resolve_calls), 1)
        self.assertEqual(state.build_id, "NEW")
        self.assertEqual(state.generation, 1)


if __name__ == "__main__":
    unittest.main()
