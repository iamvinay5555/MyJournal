"""Tests for the myjournal_nudge Hermes plugin (uses the real journal on a temp DB)."""
from __future__ import annotations

import importlib.util
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from myjournal import journal as J  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "myjournal_nudge", ROOT / "plugin" / "myjournal_nudge" / "__init__.py")
N = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(N)


class NudgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "journal.db"
        self.conn = J.connect(self.db)
        for v in N.SCRIPTED_ENV:
            os.environ.pop(v, None)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def nudge(self, **kw):
        kw.setdefault("platform", "telegram")
        kw.setdefault("session_id", "s1")
        kw.setdefault("db", self.db)
        return N.build_nudge(**kw)

    def test_parity_with_journal_due_including_resurfaced_asked(self):
        t = J.now()
        J.add(self.conn, kind="i_wonder", mood="warm", text="undated warm")
        J.add(self.conn, kind="user_said", mood="useful", text="dated reminder",
              ask_after=t + timedelta(minutes=1))
        asked = J.add(self.conn, kind="care", text="mom packing")
        J.mark_asked(self.conn, asked.id)
        J.add(self.conn, kind="mid_thread", mood="wait", text="parked")
        for when in (t + timedelta(minutes=2),
                     t + timedelta(hours=J.ASK_RESURFACE_HOURS + 1)):
            want = [x.id for x in J.due(self.conn, when=when)]
            got = [x["id"] for x in N._due_now(self.db, when)]
            self.assertEqual(got, want)
        later = t + timedelta(hours=J.ASK_RESURFACE_HOURS + 1)
        self.assertIn(asked.id, [x["id"] for x in N._due_now(self.db, later)])
        self.assertEqual(N.RESURFACE_HOURS, J.ASK_RESURFACE_HOURS)

    def test_pinged_loop_carries_bell_marker(self):
        loop = J.add(self.conn, kind="user_said", text="email the landlord",
                     ask_after=J.now() - timedelta(minutes=1), ping=True)
        ctx, _ = self.nudge()
        self.assertNotIn("🔔", ctx)
        J.mark_pinged(self.conn, loop.id)
        ctx, _ = self.nudge(session_id="s2")
        self.assertIn(loop.id, ctx)
        self.assertIn("🔔 watchdog already pinged", ctx)

    def test_dated_reminder_surfaces_later_mid_session(self):
        t0 = J.now()
        loop = J.add(self.conn, kind="user_said", text="Clean up old backups",
                     ask_after=t0 + timedelta(hours=10))
        ctx, state = self.nudge(when=t0 + timedelta(hours=1))
        self.assertIsNone(ctx)  # not yet due
        ctx, state = self.nudge(when=t0 + timedelta(hours=11), state=state)
        self.assertIn(loop.id, ctx)
        self.assertIn("Clean up old backups", ctx)

    def test_wait_items_never_injected(self):
        J.add(self.conn, kind="care", mood="wait", text="Mom visiting until Friday")
        ctx, _ = self.nudge()
        self.assertIsNone(ctx)

    def test_same_session_throttle_then_reshow(self):
        loop = J.add(self.conn, kind="user_said", text="Call the bank")
        t = J.now()
        ctx1, st = self.nudge(when=t)
        self.assertIn(loop.id, ctx1)
        ctx2, st = self.nudge(when=t + timedelta(minutes=5), state=st)
        self.assertIsNone(ctx2)
        ctx3, st = self.nudge(when=t + timedelta(hours=N.RESHOW_HOURS + 1), state=st)
        self.assertIn(loop.id, ctx3)

    def test_new_session_sees_it_again(self):
        J.add(self.conn, kind="user_said", text="Call the bank")
        t = J.now()
        _, st = self.nudge(when=t)
        ctx, _ = self.nudge(when=t + timedelta(minutes=1), session_id="s2", state=st)
        self.assertIsNotNone(ctx)

    def test_ineligible_surfaces_skip(self):
        J.add(self.conn, kind="user_said", text="Call the bank")
        for p in ("cron", "subagent", "discord", ""):
            self.assertIsNone(self.nudge(platform=p)[0], p)
        self.assertIsNone(self.nudge(parent_session_id="parent")[0])
        os.environ["HERMES_KANBAN_TASK"] = "t_1"
        try:
            self.assertIsNone(self.nudge()[0])
        finally:
            os.environ.pop("HERMES_KANBAN_TASK")

    def test_closed_and_expired_not_injected(self):
        a = J.add(self.conn, kind="user_said", text="Done thing")
        J.close(self.conn, a.id, "done")
        J.add(self.conn, kind="user_said", text="Old thing", ttl_days=1)
        ctx, _ = self.nudge(when=J.now() + timedelta(days=2))
        self.assertIsNone(ctx)

    def test_caps_items_and_text(self):
        for i in range(5):
            J.add(self.conn, kind="user_said", text=f"thing {i} " + "x" * 400)
        ctx, _ = self.nudge()
        self.assertEqual(ctx.count("\n- "), N.MAX_ITEMS)
        self.assertLess(len(ctx), 1500)

    def test_read_only_never_mutates_journal(self):
        J.add(self.conn, kind="user_said", text="Call the bank")
        before = self.db.read_bytes()
        self.nudge()
        self.assertEqual(before, self.db.read_bytes())

    def test_missing_db_is_silent(self):
        ctx, _ = self.nudge(db=Path(self.tmp.name) / "nope.db")
        self.assertIsNone(ctx)

    def test_hook_swallows_errors(self):
        orig = N.JOURNAL_DB
        N.JOURNAL_DB = Path(self.tmp.name)  # a directory → sqlite error
        try:
            self.assertIsNone(N._on_pre_llm_call(platform="telegram", session_id="s"))
        finally:
            N.JOURNAL_DB = orig


if __name__ == "__main__":
    unittest.main()
