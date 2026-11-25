"""Reminder-shape rules.

A dated reminder must be actionable and must outlive its due moment.
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


class ReminderRuleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "journal.db"
        self.conn = J.connect(self.db)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_wait_mood_with_ask_after_is_rejected(self):
        # The failure this guards: a reminder saved as wait can never surface.
        with self.assertRaises(J.JournalError):
            J.add(self.conn, kind="user_said", mood="wait",
                  text="Remind them to clean up the old backups",
                  ask_after=J.now() + timedelta(hours=10))

    def test_dated_reminder_stays_live_three_days_past_due(self):
        due_at = J.now() + timedelta(hours=10)
        loop = J.add(self.conn, kind="user_said", text="Clean up old backups",
                     ask_after=due_at)
        self.assertGreaterEqual(
            J._parse(loop.expires_at) - due_at, timedelta(days=3) - timedelta(seconds=1))

    def test_until_not_after_ask_after_is_rejected(self):
        due_at = J.now() + timedelta(days=1)
        with self.assertRaises(J.JournalError):
            J.add(self.conn, kind="user_said", text="Clean up old backups",
                  ask_after=due_at, until=due_at - timedelta(hours=1))

    def test_reminder_surfaces_only_once_due(self):
        due_at = J.now() + timedelta(hours=10)
        loop = J.add(self.conn, kind="user_said", text="Clean up old backups",
                     ask_after=due_at)
        self.assertEqual(J.due(self.conn), [])
        later = [x.id for x in J.due(self.conn, when=due_at + timedelta(minutes=1))]
        self.assertEqual(later, [loop.id])
        # still due two days later if nobody answered
        later2 = [x.id for x in J.due(self.conn, when=due_at + timedelta(days=2))]
        self.assertEqual(later2, [loop.id])

    def test_cli_add_accepts_ask_after(self):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = cli.main(["--db", str(self.db), "add", "--kind", "user_said",
                           "--text", "Clean up old backups",
                           "--ask-after", "2099-01-01T09:00:00+00:00"])
        self.assertEqual(rc, 0, out.getvalue())
        loop = J.list_loops(self.conn)[0]
        self.assertTrue(loop.ask_after.startswith("2099-01-01T09:00:00"))
        self.assertTrue(loop.expires_at.startswith("2099-01-04"))

    def test_cli_rejects_wait_with_ask_after(self):
        err = io.StringIO()
        with redirect_stderr(err), redirect_stdout(io.StringIO()):
            rc = cli.main(["--db", str(self.db), "add", "--kind", "user_said",
                           "--mood", "wait", "--text", "Clean up old backups",
                           "--ask-after", "2099-01-01T09:00:00+00:00"])
        self.assertEqual(rc, 1)
        self.assertIn("wait", err.getvalue())


if __name__ == "__main__":
    unittest.main()
