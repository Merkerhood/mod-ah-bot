"""Worker-exception handling: a bad item must be recorded, never dropped."""
import json
import os
import tempfile
import threading
import unittest
from datetime import datetime

import backfill
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


class SelectRecoveryIdsTest(unittest.TestCase):
    def test_select_recovery_ids(self):
        records = [
            {"item": 1, "reason": "no-data", "gen": 0},
            {"item": 2, "reason": "no-data", "gen": 1},
            {"item": 3, "reason": "thin", "gen": 0},
            {"item": 4, "row": [4, 10, 10], "reason": None, "gen": 0},
        ]
        self.assertEqual(backfill.select_recovery_ids(records, 1), [1])


class NoteResultTest(unittest.TestCase):
    def test_note_result_reresolves_at_threshold(self):
        orig = wowauctions.resolve_build_id
        wowauctions.resolve_build_id = lambda: "NEWBUILD"
        try:
            state = backfill.BuildIdState("OLD")
            for _ in range(3):
                state.note_result(True, 3)
            self.assertEqual(state.build_id, "NEWBUILD")
            self.assertEqual(state.generation, 1)
            self.assertEqual(state.consecutive_404, 0)
            state.note_result(False, 3)
            self.assertEqual(state.consecutive_404, 0)
        finally:
            wowauctions.resolve_build_id = orig


if __name__ == "__main__":
    unittest.main()
