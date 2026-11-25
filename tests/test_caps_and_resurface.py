"""Caps, resurfacing, ordering, edits and the board.

1. Future reminders only count toward the live cap within CAP_HORIZON_DAYS.
2. An 'asked' loop that is still open resurfaces after ASK_RESURFACE_HOURS.
3. Dated reminders are offered before undated ones.
4. Loops can be edited in place, with an edit history.
5. `board` shows the whole journal grouped by when it matters.
Run: python3 -m unittest discover -s tests -v
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


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "journal.db"
        self.conn = J.connect(self.db)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli.main(["--db", str(self.db), *argv])
        return rc, out.getvalue(), err.getvalue()


class CapHorizonTests(Base):
    def test_far_future_reminders_do_not_consume_cap(self):
        far = J.now() + timedelta(days=J.CAP_HORIZON_DAYS + 20)
        for i in range(J.OPEN_CAP):
            J.add(self.conn, kind="user_said", text=f"december thing {i}", ask_after=far)
        # an immediate loop still fits
        J.add(self.conn, kind="user_said", text="call the bank today")
        self.assertEqual(J.live_count(self.conn), 1)

    def test_near_reminders_do_consume_cap(self):
        soon = J.now() + timedelta(days=2)
        for i in range(J.OPEN_CAP):
            J.add(self.conn, kind="user_said", text=f"this week {i}", ask_after=soon)
        with self.assertRaises(J.JournalError):
            J.add(self.conn, kind="user_said", text="one too many")

    def test_far_reminder_starts_counting_inside_horizon(self):
        far = J.now() + timedelta(days=30)
        J.add(self.conn, kind="user_said", text="photo album", ask_after=far)
        self.assertEqual(J.live_count(self.conn), 0)
        self.assertEqual(J.live_count(self.conn, when=far - timedelta(days=3)), 1)

    def test_doctor_uses_horizon_count(self):
        far = J.now() + timedelta(days=40)
        for i in range(J.OPEN_CAP):
            J.add(self.conn, kind="user_said", text=f"later {i}", ask_after=far)
        J.add(self.conn, kind="user_said", text="now thing")
        rc, out, _ = self.run_cli("doctor")
        self.assertEqual(rc, 0, out)


class AskedResurfaceTests(Base):
    def test_asked_loop_resurfaces_after_window(self):
        loop = J.add(self.conn, kind="care", text="Mom packing and flight snacks")
        J.mark_asked(self.conn, loop.id)
        self.assertEqual(J.due(self.conn), [])
        later = J.now() + timedelta(hours=J.ASK_RESURFACE_HOURS + 1)
        self.assertEqual([x.id for x in J.due(self.conn, when=later)], [loop.id])

    def test_asking_again_restarts_window(self):
        loop = J.add(self.conn, kind="care", text="Mom packing")
        J.mark_asked(self.conn, loop.id)
        first = J.get(self.conn, loop.id).asked_at
        again = J.mark_asked(self.conn, loop.id)
        self.assertEqual(again.status, "asked")
        self.assertGreaterEqual(again.asked_at, first)

    def test_closed_asked_loop_never_resurfaces(self):
        loop = J.add(self.conn, kind="care", text="Mom packing")
        J.mark_asked(self.conn, loop.id)
        J.close(self.conn, loop.id, "done")
        later = J.now() + timedelta(days=2)
        self.assertEqual(J.due(self.conn, when=later), [])


class OrderingTests(Base):
    def test_dated_reminders_come_first(self):
        undated = J.add(self.conn, kind="i_wonder", mood="warm", text="ask about lunch")
        t = J.now()
        dated = J.add(self.conn, kind="user_said", mood="useful", text="Mom check-in",
                      ask_after=t + timedelta(minutes=1))
        ids = [x.id for x in J.due(self.conn, when=t + timedelta(minutes=2))]
        self.assertEqual(ids, [dated.id, undated.id])


class EditTests(Base):
    def test_edit_text_in_place_keeps_history(self):
        loop = J.add(self.conn, kind="user_said", text="Saturday garage cleanup")
        edited = J.edit(self.conn, loop.id, text="Saturday garage cleanup + donation box")
        self.assertEqual(edited.id, loop.id)
        self.assertEqual(edited.text, "Saturday garage cleanup + donation box")
        hist = J.edits(self.conn, loop.id)
        self.assertEqual(len(hist), 1)
        self.assertEqual(hist[0]["field"], "text")
        self.assertEqual(hist[0]["old"], "Saturday garage cleanup")
        self.assertEqual([x.id for x in J.find(self.conn, "donation box")], [loop.id])

    def test_edit_updates_dedup(self):
        loop = J.add(self.conn, kind="user_said", text="old words")
        J.edit(self.conn, loop.id, text="new words")
        # old wording is free again, new wording is taken
        J.add(self.conn, kind="user_said", text="old words")
        with self.assertRaises(J.JournalError):
            J.add(self.conn, kind="user_said", text="new words")

    def test_edit_into_duplicate_of_other_open_loop_rejected(self):
        J.add(self.conn, kind="user_said", text="call the bank")
        other = J.add(self.conn, kind="user_said", text="call the dentist")
        with self.assertRaises(J.JournalError):
            J.edit(self.conn, other.id, text="Call the  bank")

    def test_edit_closed_loop_rejected(self):
        loop = J.add(self.conn, kind="user_said", text="done thing")
        J.close(self.conn, loop.id, "done")
        with self.assertRaises(J.JournalError):
            J.edit(self.conn, loop.id, text="changed")

    def test_edit_ask_after_extends_expiry(self):
        loop = J.add(self.conn, kind="user_said", text="photo album")
        due_at = J.now() + timedelta(days=60)
        edited = J.edit(self.conn, loop.id, ask_after=due_at)
        self.assertTrue(edited.ask_after.startswith(J.iso(due_at)[:16]))
        self.assertGreaterEqual(J._parse(edited.expires_at),
                                due_at + timedelta(days=J.REMINDER_GRACE_DAYS) - timedelta(seconds=1))

    def test_edit_wait_with_date_rejected(self):
        loop = J.add(self.conn, kind="user_said", text="dated thing",
                     ask_after=J.now() + timedelta(days=1))
        with self.assertRaises(J.JournalError):
            J.edit(self.conn, loop.id, mood="wait")

    def test_edit_to_wait_parks_undated_loop(self):
        loop = J.add(self.conn, kind="mid_thread", text="someday idea")
        edited = J.edit(self.conn, loop.id, mood="wait")
        self.assertEqual(edited.mood, "wait")
        self.assertEqual(J.due(self.conn), [])

    def test_edit_respects_cap_when_it_starts_counting(self):
        for i in range(J.OPEN_CAP):
            J.add(self.conn, kind="user_said", text=f"now {i}")
        far = J.add(self.conn, kind="user_said", text="far thing",
                    ask_after=J.now() + timedelta(days=60))
        with self.assertRaises(J.JournalError):
            J.edit(self.conn, far.id, ask_after=J.now() + timedelta(days=1))

    def test_noop_edit_rejected(self):
        loop = J.add(self.conn, kind="user_said", text="same")
        with self.assertRaises(J.JournalError):
            J.edit(self.conn, loop.id)

    def test_cli_edit_and_show(self):
        loop = J.add(self.conn, kind="user_said", text="first wording")
        rc, out, err = self.run_cli("edit", loop.id, "--text", "second wording",
                                    "--why", "merged")
        self.assertEqual(rc, 0, err)
        self.assertIn("second wording", out)
        rc, out, err = self.run_cli("show", loop.id)
        self.assertEqual(rc, 0, err)
        self.assertIn("second wording", out)
        self.assertIn("first wording", out)  # history visible


class BoardTests(Base):
    def test_board_groups(self):
        J.add(self.conn, kind="user_said", text="check lottery tonight")
        J.add(self.conn, kind="user_said", text="saturday project",
              ask_after=J.now() + timedelta(days=3))
        J.add(self.conn, kind="user_said", text="december images",
              ask_after=J.now() + timedelta(days=60))
        asked = J.add(self.conn, kind="care", text="mom packing")
        J.mark_asked(self.conn, asked.id)
        J.add(self.conn, kind="mid_thread", mood="wait", text="garden dream")
        rc, out, err = self.run_cli("board")
        self.assertEqual(rc, 0, err)
        order = [out.index(h) for h in
                 ("Due now", "Asked, waiting", "Next 7 days", "Later", "Parked")]
        self.assertEqual(order, sorted(order))
        for text in ("check lottery tonight", "saturday project", "december images",
                     "mom packing", "garden dream"):
            self.assertIn(text, out)
        self.assertIn("live 3/", out)  # lottery + saturday + asked count; december does not



if __name__ == "__main__":
    unittest.main()
