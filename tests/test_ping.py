"""Watchdog pings.

Reminders get missed when the user is busy and doesn't message. A loop marked
`ping` gets ONE proactive message from the cron watchdog at its
ask_after time. Pinging never closes the loop: it stays open so the chat nudge
keeps it alive until the agent closes it.
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


class PingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "journal.db"
        self.conn = J.connect(self.db)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _add(self, text="Email the landlord at 10pm", hours=-0.1, ping=True, **kw):
        return J.add(self.conn, kind="user_said", mood="useful", text=text,
                     ask_after=J.now() + timedelta(hours=hours), ping=ping, **kw)

    def test_ping_requires_a_time(self):
        with self.assertRaises(J.JournalError):
            J.add(self.conn, kind="user_said", mood="useful", text="undated", ping=True)

    def test_due_ping_listed_once_then_not_after_mark(self):
        loop = self._add()
        self.assertEqual([x.id for x in J.ping_due(self.conn)], [loop.id])
        J.mark_pinged(self.conn, loop.id)
        self.assertEqual(J.ping_due(self.conn), [])

    def test_pinged_loop_stays_open_and_in_chat_due(self):
        loop = self._add()
        pinged = J.mark_pinged(self.conn, loop.id)
        self.assertEqual(pinged.status, "open")
        self.assertIsNotNone(pinged.pinged_at)
        self.assertIn(loop.id, [x.id for x in J.due(self.conn)])

    def test_future_ping_not_due_yet(self):
        self._add(hours=2)
        self.assertEqual(J.ping_due(self.conn), [])

    def test_unflagged_loop_never_pings(self):
        self._add(ping=False)
        self.assertEqual(J.ping_due(self.conn), [])

    def test_stale_ping_beyond_window_is_skipped(self):
        self._add(hours=-(J.PING_LATE_HOURS + 1))
        self.assertEqual(J.ping_due(self.conn), [])

    def test_closed_loop_never_pings(self):
        loop = self._add()
        J.close(self.conn, loop.id, reason="done")
        self.assertEqual(J.ping_due(self.conn), [])

    def test_asked_loop_still_pings(self):
        # Mentioned in chat earlier doesn't cancel the on-time ping.
        loop = self._add()
        J.mark_asked(self.conn, loop.id)
        self.assertEqual([x.id for x in J.ping_due(self.conn)], [loop.id])

    def test_reschedule_rearms_ping(self):
        loop = self._add()
        J.mark_pinged(self.conn, loop.id)
        J.edit(self.conn, loop.id, ask_after=J.now() - timedelta(minutes=1))
        self.assertEqual([x.id for x in J.ping_due(self.conn)], [loop.id])

    def test_edit_can_turn_ping_on_and_off(self):
        loop = self._add(ping=False)
        J.edit(self.conn, loop.id, ping=True)
        self.assertEqual(len(J.ping_due(self.conn)), 1)
        J.edit(self.conn, loop.id, ping=False)
        self.assertEqual(J.ping_due(self.conn), [])

    def test_mark_pinged_twice_refused(self):
        loop = self._add()
        J.mark_pinged(self.conn, loop.id)
        with self.assertRaises(J.JournalError):
            J.mark_pinged(self.conn, loop.id)

    def test_old_database_migrates_with_ping_columns(self):
        import sqlite3
        old = Path(self.tmp.name) / "old.db"
        raw = sqlite3.connect(old)
        raw.executescript(J.SCHEMA.replace(
            "    ping         INTEGER NOT NULL DEFAULT 0,\n    pinged_at    TEXT,\n", ""))
        raw.execute(
            "INSERT INTO loops (id, kind, mood, text, dedup, status, created_at)"
            " VALUES ('a1','user_said','useful','legacy','k','open',?)", (J.iso(),))
        raw.commit()
        raw.close()
        conn = J.connect(old)
        loop = J.get(conn, "a1")
        self.assertEqual(loop.ping, 0)
        self.assertIsNone(loop.pinged_at)
        conn.close()

    def test_cli_add_ping_and_ping_due_json(self):
        when = (J.now() - timedelta(minutes=2)).isoformat()
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            rc = cli.main(["--db", str(self.db), "add", "--kind", "user_said",
                           "--text", "cli ping", "--ask-after", when, "--ping"])
        self.assertEqual(rc, 0)
        out = io.StringIO()
        with redirect_stdout(out):
            cli.main(["--db", str(self.db), "ping-due", "--json"])
        self.assertIn("cli ping", out.getvalue())


if __name__ == "__main__":
    unittest.main()
