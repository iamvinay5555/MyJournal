"""'Right now' shelf: short-lived status facts like
"WFH this week" or "has a cold" that the agent should simply know.

They never take one of the 15 plan slots, never surface while live, always
carry an end date, and at the end either close on their own (clear-cut) or
come back once so the agent can ask the user (--confirm).
"""
from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import timedelta
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from myjournal import journal as J
from myjournal import cli


class NowShelfTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "journal.db"
        self.conn = J.connect(self.db)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _now(self, text="WFH this week", days=3, confirm=False):
        return J.add(self.conn, kind="now", text=text,
                     until=J.now() + timedelta(days=days), confirm=confirm)

    def _fill_plans(self, n=None):
        for i in range(J.OPEN_CAP if n is None else n):
            J.add(self.conn, kind="user_said", mood="useful", text=f"plan {i}")

    def test_now_requires_until(self):
        with self.assertRaises(J.JournalError):
            J.add(self.conn, kind="now", text="WFH this week")

    def test_now_rejects_ask_after(self):
        with self.assertRaises(J.JournalError):
            J.add(self.conn, kind="now", text="x", until=J.now() + timedelta(days=2),
                  ask_after=J.now() + timedelta(days=1))

    def test_now_is_stored_quiet(self):
        loop = self._now()
        self.assertEqual(loop.mood, "wait")
        self.assertEqual(J.due(self.conn), [])

    def test_now_does_not_take_plan_slots(self):
        self._fill_plans()
        loop = self._now()
        self.assertEqual(loop.kind, "now")
        self.assertEqual(J.live_count(self.conn), J.OPEN_CAP)

    def test_plans_still_fit_when_now_shelf_is_full(self):
        for i in range(J.NOW_CAP):
            self._now(text=f"status {i}")
        self._fill_plans()
        self.assertEqual(J.live_count(self.conn), J.OPEN_CAP)

    def test_now_shelf_has_own_cap(self):
        for i in range(J.NOW_CAP):
            self._now(text=f"status {i}")
        with self.assertRaises(J.JournalError):
            self._now(text="one too many")

    def test_clear_cut_status_closes_itself(self):
        loop = self._now(days=1)
        J.sweep(self.conn, when=J.now() + timedelta(days=2))
        got = J.get(self.conn, loop.id)
        self.assertEqual(got.status, "closed")
        self.assertEqual(got.close_reason, "ended (auto)")

    def test_ended_status_is_told_only_once(self):
        loop = self._now(days=1)
        later = J.now() + timedelta(days=2)
        J.sweep(self.conn, when=later)
        self.assertIn(loop.id, [x.id for x in J.recently_ended_now(self.conn, when=later)])
        J.mark_asked(self.conn, loop.id)  # the agent told the user
        self.assertNotIn(loop.id, [x.id for x in J.recently_ended_now(self.conn, when=later)])
        self.assertEqual(J.get(self.conn, loop.id).status, "closed")

    def test_confirm_status_comes_back_once_to_ask(self):
        loop = self._now(text="has a cold", days=1, confirm=True)
        later = J.now() + timedelta(days=2)
        J.sweep(self.conn, when=later)
        got = J.get(self.conn, loop.id)
        self.assertEqual(got.status, "open")
        self.assertIn(got.mood, J.ACTIONABLE_MOODS)
        self.assertIn(loop.id, [x.id for x in J.due(self.conn, when=later)])
        J.sweep(self.conn, when=later)
        self.assertEqual(J.get(self.conn, loop.id).status, "open")

    def test_confirm_checkin_does_not_take_plan_slot(self):
        self._fill_plans()
        self._now(text="has a cold", days=1, confirm=True)
        J.sweep(self.conn, when=J.now() + timedelta(days=2))
        self.assertEqual(J.live_count(self.conn), J.OPEN_CAP)

    def test_extend_rearms_quietly(self):
        loop = self._now(text="has a cold", days=1, confirm=True)
        J.sweep(self.conn, when=J.now() + timedelta(days=2))
        J.edit(self.conn, loop.id, until=J.now() + timedelta(days=5))
        got = J.get(self.conn, loop.id)
        self.assertEqual((got.status, got.mood), ("open", "wait"))
        self.assertEqual(J.due(self.conn), [])

    def test_edit_cannot_move_into_or_out_of_now(self):
        loop = self._now()
        with self.assertRaises(J.JournalError):
            J.edit(self.conn, loop.id, kind="user_said")
        plan = J.add(self.conn, kind="user_said", mood="useful", text="a plan")
        with self.assertRaises(J.JournalError):
            J.edit(self.conn, plan.id, kind="now")

    def test_now_items_lists_only_live(self):
        live = self._now(text="WFH this week", days=5)
        ended = self._now(text="Mom visiting", days=1)
        J.sweep(self.conn, when=J.now() + timedelta(days=2))
        ids = [x.id for x in J.now_items(self.conn)]
        self.assertIn(live.id, ids)
        self.assertNotIn(ended.id, ids)

    def _cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli.main(["--db", str(self.db), *argv])
        return rc, out.getvalue(), err.getvalue()

    def test_cli_add_now_and_board_shows_right_now(self):
        rc, out, err = self._cli("add", "--kind", "now", "--text", "WFH 19-23 Oct",
                                 "--until", (J.now() + timedelta(days=3)).date().isoformat())
        self.assertEqual(rc, 0, err)
        rc, out, _ = self._cli("board")
        self.assertEqual(rc, 0)
        self.assertIn("Right now", out)
        self.assertIn("WFH 19-23 Oct", out)
        self.assertNotIn("Parked", out)

    def test_cli_confirm_flag(self):
        rc, out, err = self._cli("add", "--kind", "now", "--confirm", "--text", "has a cold",
                                 "--until", (J.now() + timedelta(days=3)).date().isoformat())
        self.assertEqual(rc, 0, err)
        self.assertEqual(J.list_loops(self.conn)[0].confirm, 1)

    def test_doctor_clean_with_full_shelves(self):
        for i in range(J.NOW_CAP):
            self._now(text=f"status {i}")
        self._fill_plans()
        rc, out, _ = self._cli("doctor")
        self.assertEqual(rc, 0, out)


if __name__ == "__main__":
    unittest.main()
