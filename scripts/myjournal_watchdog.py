#!/usr/bin/env python3
"""MyJournal watchdog gate — optional Hermes cron pre-run script (e.g. every 5 min).

Nothing due -> prints {"wakeAgent": false}: silent tick, no LLM call, no cost.
Ping due    -> marks those loops pinged FIRST (at-most-once, so a crashed or
               retried run can never double-message the user), then wakes the
               cron agent with the loops so it can write ONE short reminder.

The loops stay open after a ping; the chat nudge plugin keeps them alive until
the agent closes them in conversation. Only loops added with --ping are sent.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")).expanduser()
JOURNAL_HOME = Path(os.environ.get("MYJOURNAL_HOME", HERMES_HOME / "myjournal")).expanduser()
sys.path.insert(0, str(JOURNAL_HOME))
from myjournal import journal as J  # noqa: E402


def main() -> int:
    conn = J.connect(JOURNAL_HOME / "journal.db")
    due = J.ping_due(conn)
    if not due:
        print(json.dumps({"wakeAgent": False}))
        return 0
    pinged = [J.mark_pinged(conn, loop.id) for loop in due]
    conn.close()
    context = {
        "now_local": J.now().astimezone(J.LOCAL_TZ).strftime("%a %d %b %Y %H:%M %Z"),
        "loops_to_ping": [
            {"id": x.id, "text": x.text, "why": x.why,
             "due_at": J.local(x.ask_after, "%a %d %b %H:%M"), "status": x.status}
            for x in pinged
        ],
    }
    print(json.dumps({"wakeAgent": True, "context": context}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
