#!/usr/bin/env python3
"""MyJournal watchdog gate — optional Hermes cron pre-run script (e.g. every 5 min).

Nothing due -> prints {"wakeAgent": false}: silent tick, no LLM call, no cost.
Ping due    -> claims ONE due loop as pinged FIRST, inside a single write
               transaction (at-most-once: a crashed, retried or overlapping run
               can never double-message the user), then wakes the cron agent
               with it so it can write ONE short reminder. Others wait a tick.

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
    try:
        # Check and claim in one write transaction, so two overlapping runs can
        # never claim the same loop. One reminder per run; the rest stay armed
        # for the next tick.
        conn.execute("BEGIN IMMEDIATE")
        due = J.ping_due(conn)[:1]
        if due:
            conn.execute("UPDATE loops SET pinged_at = ? WHERE id = ?", (J.iso(), due[0].id))
        conn.commit()
        pinged = [J.get(conn, loop.id) for loop in due]
    finally:
        conn.close()
    if not pinged:
        print(json.dumps({"wakeAgent": False}))
        return 0
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
