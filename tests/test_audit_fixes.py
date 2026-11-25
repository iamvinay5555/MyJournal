"""Regressions from an independent audit (v1.1.0).

- An asked loop honours its own due date; rescheduling re-arms it.
- Asked loops resurface after 72 h, not daily.
- Re-adding a closed loop keeps its earlier episode in the edit history.
- The watchdog claims one reminder per run, atomically.
- `find` treats the query literally (no LIKE wildcards).
- Sidecar files (-wal/-shm) are private like the DB.
- The nudge shows one item per turn, with a cooldown that survives new sessions,
  and can be limited to the owner's own chat route.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import runpy
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from myjournal import journal as J  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "myjournal_nudge_audit", ROOT / "plugin" / "myjournal_nudge" / "__init__.py")
N = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(N)


class JournalAuditFixes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "journal.db"
        self.conn = J.connect(self.db)
        self.t = J.now()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def due_ids(self, when):
        return [x.id for x in J.due(self.conn, when=when)]

    def test_asked_loop_rescheduled_far_ahead_stays_quiet(self):
        loop = J.add(self.conn, kind="user_said", text="channel review in january")
        J.mark_asked(self.conn, loop.id)
        J.edit(self.conn, loop.id, ask_after=self.t + timedelta(days=40))
        self.assertNotIn(loop.id, self.due_ids(self.t + timedelta(days=5)))
        self.assertIn(loop.id, self.due_ids(self.t + timedelta(days=40, minutes=1)))

    def test_reschedule_soon_after_asking_surfaces_on_time(self):
        loop = J.add(self.conn, kind="user_said", text="check the parcel")
        J.mark_asked(self.conn, loop.id)
        at = self.t + timedelta(hours=2)
        J.edit(self.conn, loop.id, ask_after=at)
        self.assertIn(loop.id, self.due_ids(at + timedelta(minutes=1)))

    def test_asked_loop_resurfaces_after_72h_not_daily(self):
        self.assertEqual(J.ASK_RESURFACE_HOURS, 72)
        loop = J.add(self.conn, kind="user_said", text="finish the trip budget")
        J.mark_asked(self.conn, loop.id)
        self.assertNotIn(loop.id, self.due_ids(self.t + timedelta(hours=48)))
        self.assertIn(loop.id, self.due_ids(self.t + timedelta(hours=73)))

    def test_readd_after_close_keeps_prior_episode(self):
        first = J.add(self.conn, kind="user_said", text="call the bank")
        closed = J.close(self.conn, first.id, "paid on 3 Dec")
        again = J.add(self.conn, kind="user_said", text="call the bank")
        self.assertEqual(again.id, first.id)
        prior = [e for e in J.edits(self.conn, again.id) if e["field"] == "prior_episode"]
        self.assertEqual(len(prior), 1)
        self.assertEqual(json.loads(prior[0]["old"])["close_reason"], closed.close_reason)

    def test_find_is_literal(self):
        J.add(self.conn, kind="user_said", text="buy milk")
        self.assertEqual(J.find(self.conn, "_"), [])

    def test_sidecars_are_private(self):
        J.add(self.conn, kind="user_said", text="something private")
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(self.db) + suffix)
            if p.exists():
                self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600, p.name)

    def test_iso_is_normalised(self):
        from datetime import datetime, timezone
        aware = datetime(2026, 1, 1, 12, 0, tzinfo=timezone(timedelta(hours=8)))
        self.assertTrue(J.iso(aware).endswith("+00:00"))


class WatchdogAuditFixes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        (self.home / "myjournal").symlink_to(ROOT / "myjournal")
        self.conn = J.connect(self.home / "journal.db")
        self._env = os.environ.get("MYJOURNAL_HOME")
        os.environ["MYJOURNAL_HOME"] = str(self.home)

    def tearDown(self):
        self.conn.close()
        if self._env is None:
            os.environ.pop("MYJOURNAL_HOME", None)
        else:
            os.environ["MYJOURNAL_HOME"] = self._env
        self.tmp.cleanup()

    def run_watchdog(self) -> dict:
        buf = io.StringIO()
        with redirect_stdout(buf), self.assertRaises(SystemExit):
            runpy.run_path(str(ROOT / "scripts" / "myjournal_watchdog.py"), run_name="__main__")
        return json.loads(buf.getvalue())

    def test_one_reminder_per_run_rest_stay_armed(self):
        t = J.now()
        for text in ("first ping", "second ping"):
            J.add(self.conn, kind="user_said", text=text,
                  ask_after=t - timedelta(minutes=1), ping=True)
        out1 = self.run_watchdog()
        self.assertTrue(out1["wakeAgent"])
        self.assertEqual(len(out1["context"]["loops_to_ping"]), 1)
        out2 = self.run_watchdog()
        self.assertEqual(len(out2["context"]["loops_to_ping"]), 1)
        self.assertNotEqual(out1["context"]["loops_to_ping"][0]["id"],
                            out2["context"]["loops_to_ping"][0]["id"])
        self.assertFalse(self.run_watchdog()["wakeAgent"])


class NudgeAuditFixes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "journal.db"
        self.conn = J.connect(self.db)
        for v in N.SCRIPTED_ENV:
            os.environ.pop(v, None)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_one_item_per_turn(self):
        for i in range(3):
            J.add(self.conn, kind="user_said", text=f"thing {i}")
        ctx, _ = N.build_nudge(platform="telegram", session_id="s1", db=self.db)
        self.assertEqual(ctx.count("\n- "), 1)

    def test_cooldown_survives_a_new_session(self):
        J.add(self.conn, kind="user_said", text="only thing")
        ctx, st = N.build_nudge(platform="telegram", session_id="s1", db=self.db)
        self.assertIsNotNone(ctx)
        ctx2, _ = N.build_nudge(platform="telegram", session_id="s2", db=self.db, state=st)
        self.assertIsNone(ctx2)

    def test_owner_route_gate(self):
        J.add(self.conn, kind="user_said", text="private reminder")
        with mock.patch.object(N, "OWNER_SESSION_KEY", "agent:main:telegram:dm:42"), \
             mock.patch.object(N, "_current_route", return_value="agent:main:telegram:group:7"):
            self.assertIsNone(N._on_pre_llm_call(platform="telegram", session_id="g"))
        with mock.patch.object(N, "OWNER_SESSION_KEY", "agent:main:telegram:dm:42"), \
             mock.patch.object(N, "_current_route", return_value="agent:main:telegram:dm:42"), \
             mock.patch.object(N, "STATE_PATH", Path(self.tmp.name) / "state.json"), \
             mock.patch.object(N, "JOURNAL_DB", self.db):
            self.assertIsNotNone(N._on_pre_llm_call(platform="telegram", session_id="d"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
