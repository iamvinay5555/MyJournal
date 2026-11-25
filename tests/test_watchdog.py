"""The optional watchdog: silent when nothing is due, pings once, then stays quiet."""
from __future__ import annotations

import io
import json
import os
import runpy
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import timedelta
from pathlib import Path

import sys
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from myjournal import journal as J  # noqa: E402

SCRIPT = ROOT / "scripts" / "myjournal_watchdog.py"


class WatchdogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        # The watchdog imports myjournal from MYJOURNAL_HOME, like an installed copy.
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
        with redirect_stdout(buf):
            with self.assertRaises(SystemExit) as cm:
                runpy.run_path(str(SCRIPT), run_name="__main__")
        self.assertEqual(cm.exception.code, 0)
        return json.loads(buf.getvalue())

    def test_silent_when_nothing_to_ping(self):
        J.add(self.conn, kind="user_said", text="no ping on this one")
        self.assertEqual(self.run_watchdog(), {"wakeAgent": False})

    def test_pings_once_then_silent(self):
        loop = J.add(self.conn, kind="user_said", text="Call the plumber",
                     ask_after=J.now() - timedelta(minutes=1), ping=True)
        out = self.run_watchdog()
        self.assertTrue(out["wakeAgent"])
        self.assertEqual([x["id"] for x in out["context"]["loops_to_ping"]], [loop.id])
        self.assertEqual(self.run_watchdog(), {"wakeAgent": False})
        self.assertEqual(J.get(self.conn, loop.id).status, "open")  # a ping never closes


if __name__ == "__main__":
    unittest.main()
