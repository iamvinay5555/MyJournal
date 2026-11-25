"""MyJournal tests. Run: python3 -m unittest discover -s tests -v"""
from __future__ import annotations

import io
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import timedelta
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from myjournal import journal as J
from myjournal import cli


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = J.connect(Path(self.tmp.name) / "journal.db")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_add_and_due_skips_wait(self):
        warm = J.add(self.conn, kind="i_wonder", mood="warm",
                     text="Ask how the commute felt today")
        J.add(self.conn, kind="mid_thread", mood="wait",
              text="Keep the old draft for reference")
        due = J.due(self.conn)
        self.assertEqual([x.id for x in due], [warm.id])

    def test_duplicate_open_is_rejected(self):
        J.add(self.conn, kind="user_said", text="Call Mom this weekend")
        with self.assertRaises(J.JournalError):
            J.add(self.conn, kind="user_said", text="Call Mom this weekend")

    def test_closed_loop_can_be_revived(self):
        loop = J.add(self.conn, kind="care", text="Pick a movie for Friday")
        J.close(self.conn, loop.id)
        again = J.add(self.conn, kind="care", text="Pick a movie for Friday")
        self.assertEqual(again.id, loop.id)
        self.assertEqual(again.status, "open")

    def test_ask_then_not_due(self):
        loop = J.add(self.conn, kind="user_said", text="Finish the Japan packing list")
        J.mark_asked(self.conn, loop.id)
        self.assertEqual(J.due(self.conn), [])
        self.assertEqual(J.get(self.conn, loop.id).status, "asked")

    def test_sweep_expires(self):
        loop = J.add(self.conn, kind="i_wonder", text="Old curiosity", ttl_days=1)
        past = J.now() + timedelta(days=2)
        n = J.sweep(self.conn, when=past)
        self.assertEqual(n, 1)
        self.assertEqual(J.get(self.conn, loop.id).status, "stale")
        self.assertEqual(J.due(self.conn, when=past), [])

    def test_open_cap(self):
        for i in range(J.OPEN_CAP):
            J.add(self.conn, kind="i_wonder", text=f"item {i}")
        with self.assertRaises(J.JournalError):
            J.add(self.conn, kind="i_wonder", text="one too many")

    def test_text_limit_is_4000_and_boundary_is_enforced(self):
        exact = J.add(self.conn, kind="mid_thread", mood="wait", text="x" * 4000)
        self.assertEqual(len(exact.text), 4000)
        with self.assertRaises(J.JournalError):
            J.add(self.conn, kind="mid_thread", mood="wait", text="y" * 4001)

    def test_wait_entries_do_not_consume_actionable_live_cap(self):
        for i in range(J.OPEN_CAP):
            J.add(self.conn, kind="i_wonder", mood="useful", text=f"actionable {i}")
        parked = J.add(
            self.conn,
            kind="mid_thread",
            mood="wait",
            text="parked long-term reference",
        )
        self.assertEqual(parked.status, "open")

    def test_long_entry_has_reference_and_is_full_text_searchable(self):
        long_text = "architecture " * 250
        loop = J.add(
            self.conn,
            kind="mid_thread",
            mood="wait",
            text=long_text,
            reference_path="/home/user/notes/example.md",
        )
        found = J.find(self.conn, "architecture")
        self.assertEqual(found[0].id, loop.id)
        self.assertEqual(found[0].reference_path, "/home/user/notes/example.md")
        self.assertEqual(found[0].text, " ".join(long_text.split()))

    def test_due_output_previews_long_text_and_keeps_reference(self):
        loop = J.add(
            self.conn,
            kind="i_wonder",
            mood="warm",
            text="important " * 300,
            reference_path="/home/user/notes/example.md",
        )
        output = io.StringIO()
        with redirect_stdout(output):
            rc = cli.main(["--db", str(Path(self.tmp.name) / "journal.db"), "due"])
        self.assertEqual(rc, 0)
        rendered = output.getvalue()
        self.assertIn(loop.id, rendered)
        self.assertIn("…", rendered)
        self.assertIn(loop.reference_path, rendered)
        self.assertLess(len(rendered), 700)

    def test_legacy_database_migrates_and_reindexes_existing_rows(self):
        legacy_path = Path(self.tmp.name) / "legacy.db"
        legacy = sqlite3.connect(legacy_path)
        legacy.executescript(
            """
            CREATE TABLE loops (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                mood TEXT NOT NULL,
                text TEXT NOT NULL,
                why TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT 'live',
                dedup TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                ask_after TEXT,
                expires_at TEXT,
                asked_at TEXT,
                closed_at TEXT,
                close_reason TEXT
            );
            """
        )
        legacy.execute(
            "INSERT INTO loops VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "legacy01",
                "mid_thread",
                "wait",
                "legacy searchable architecture note",
                "migration",
                "legacy-test",
                "legacy-dedup",
                "open",
                "2026-01-01T00:00:00+00:00",
                None,
                "2027-01-01T00:00:00+00:00",
                None,
                None,
                None,
            ),
        )
        legacy.commit()
        legacy.close()

        migrated = J.connect(legacy_path)
        found = J.find(migrated, "searchable architecture")
        self.assertEqual([x.id for x in found], ["legacy01"])
        self.assertEqual(found[0].reference_path, "")
        self.assertTrue(
            migrated.execute(
                "SELECT 1 FROM sqlite_master WHERE name='loops_fts'"
            ).fetchone()
        )
        migrated.close()

    def test_reviving_closed_loop_updates_fts_reference(self):
        loop = J.add(
            self.conn,
            kind="care",
            mood="wait",
            text="revivable reference",
            reference_path="/old/path.md",
        )
        J.close(self.conn, loop.id)
        revived = J.add(
            self.conn,
            kind="care",
            mood="wait",
            text="revivable reference",
            reference_path="/new/path.md",
        )
        found = J.find(self.conn, "new path")
        self.assertEqual(found[0].id, revived.id)
        self.assertEqual(found[0].reference_path, "/new/path.md")
        self.assertEqual(J.find(self.conn, "old path"), [])

    def test_empty_and_bad_kind_rejected(self):
        with self.assertRaises(J.JournalError):
            J.add(self.conn, kind="nope", text="x")
        with self.assertRaises(J.JournalError):
            J.add(self.conn, kind="care", text="   ")

    def test_wait_lives_longer_and_until_is_honoured(self):
        from datetime import datetime as dt
        wait = J.add(self.conn, kind="care", mood="wait", text="Mom visiting")
        exp = dt.fromisoformat(wait.expires_at)
        self.assertGreater((exp - J.now()).days, 60)
        until = J.now() + timedelta(days=40)
        loop = J.add(self.conn, kind="care", mood="wait",
                     text="Lisbon family holiday", until=until)
        self.assertLess(
            abs((dt.fromisoformat(loop.expires_at) - until).total_seconds()), 2
        )

    def test_history_and_find_recall_closed(self):
        loop = J.add(self.conn, kind="user_said", text="Book the Lisbon car rental")
        J.close(self.conn, loop.id, reason="done")
        hist = J.history(self.conn)
        self.assertEqual(hist[0].id, loop.id)
        found = J.find(self.conn, "lisbon car")
        self.assertEqual(found[0].status, "closed")


class ConnectPathTests(unittest.TestCase):
    def test_connect_requires_explicit_path(self):
        with self.assertRaises(J.JournalError):
            J.connect()
        with self.assertRaises(J.JournalError):
            J.connect("")

    def test_module_db_path_cannot_hijack_omitted_connect(self):
        poisoned = Path(tempfile.mkdtemp()) / "other.db"
        old = J.DB_PATH
        J.DB_PATH = poisoned
        try:
            with self.assertRaises(J.JournalError):
                J.connect()
            self.assertFalse(poisoned.exists())
        finally:
            J.DB_PATH = old


if __name__ == "__main__":
    unittest.main(verbosity=2)
