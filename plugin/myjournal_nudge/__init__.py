"""MyJournal nudge — deterministic due-reminder injection for Hermes Agent.

Why: if surfacing reminders depends on the model remembering to run
``myjournal due``, reminders get missed, and chat sessions can run for days.
This hook asks the journal on every interactive turn instead.

Contract:
  * pre_llm_call only; returns a short context string or None.
  * Opens the journal read-only (mode=ro). Never writes it: status changes stay
    with the agent (``ask`` / ``close``).
  * Only interactive surfaces (MYJOURNAL_NUDGE_PLATFORMS, default telegram,cli).
    Skips subagents, kanban workers and single-query runs.
  * Throttled: a given loop is shown at most once per session, and again only
    after RESHOW_HOURS in a long-lived session. The injected text stays in that
    turn's history, so the agent keeps seeing it afterwards.
  * Fail-closed to silence: any error -> None, never blocks a reply.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

HERMES_HOME = Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()
JOURNAL_HOME = Path(os.environ.get("MYJOURNAL_HOME", HERMES_HOME / "myjournal")).expanduser()
JOURNAL_DB = JOURNAL_HOME / "journal.db"
STATE_PATH = JOURNAL_HOME / "nudge_state.json"

ALLOWED_PLATFORMS = {
    p.strip().lower()
    for p in os.environ.get("MYJOURNAL_NUDGE_PLATFORMS", "telegram,cli").split(",")
    if p.strip()
}
USER_NAME = os.environ.get("MYJOURNAL_USER_NAME", "").strip() or "the user"
SCRIPTED_ENV = ("HERMES_KANBAN_TASK", "HERMES_SINGLE_QUERY_SESSION")
MAX_ITEMS = 3
MAX_TEXT = 220
RESHOW_HOURS = 12
STATE_KEEP_DAYS = 14
RESURFACE_HOURS = 24  # keep in step with myjournal.journal.ASK_RESURFACE_HOURS (parity-tested)


def _eligible(platform: str, parent_session_id: str) -> bool:
    if (platform or "").lower() not in ALLOWED_PLATFORMS:
        return False
    if parent_session_id:
        return False
    return not any(os.environ.get(v) for v in SCRIPTED_ENV)


def _iso(when: datetime) -> str:
    return when.astimezone(timezone.utc).isoformat()


def _due_now(db: Path, when: datetime) -> list[dict]:
    if not db.exists():
        return []
    stamp = _iso(when)
    resurface = _iso(when - timedelta(hours=RESURFACE_HOURS))
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
    conn.row_factory = sqlite3.Row
    try:
        # Same rule as myjournal.journal.due(): open loops once ask_after
        # passes; still-live 'asked' loops again after RESURFACE_HOURS;
        # dated reminders first.
        rows = conn.execute(
            "SELECT id, kind, mood, text, ask_after, pinged_at FROM loops"
            " WHERE mood != 'wait'"
            " AND (expires_at IS NULL OR expires_at > ?)"
            " AND ((status = 'open' AND (ask_after IS NULL OR ask_after <= ?))"
            "   OR (status = 'asked' AND asked_at IS NOT NULL AND asked_at <= ?))"
            " ORDER BY CASE WHEN ask_after IS NOT NULL AND ask_after > created_at"
            "   THEN 0 ELSE 1 END,"
            " CASE mood WHEN 'warm' THEN 0 WHEN 'useful' THEN 1 ELSE 2 END,"
            " created_at ASC",
            (stamp, stamp, resurface),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def _load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(STATE_PATH.parent), prefix=".nudge_state.")
    with os.fdopen(fd, "w") as fh:
        json.dump(state, fh)
    os.chmod(tmp, 0o600)
    os.replace(tmp, STATE_PATH)


def _select(items: list[dict], state: dict, session_id: str, now_ts: float) -> list[dict]:
    picked = []
    for item in items:
        seen = state.get(item["id"]) or {}
        same_session = seen.get("session") == session_id
        fresh = now_ts - float(seen.get("at", 0)) < RESHOW_HOURS * 3600
        if same_session and fresh:
            continue
        picked.append(item)
        if len(picked) >= MAX_ITEMS:
            break
    return picked


def _render(items: list[dict]) -> str:
    lines = [
        f"[MyJournal — due reminders for {USER_NAME}, from the journal (not their words this turn).",
        " Background only. Mention one ONLY when it is necessary now (it's time to act on it)"
        " or directly about what they just said; otherwise stay silent about it. Never tack plans"
        " onto unrelated replies, and don't repeat one already mentioned or acknowledged in this"
        f" chat. Never recite this list. In {JOURNAL_HOME}: after you bring one up,"
        " `python3 -m myjournal ask <id>`; when it is done, `python3 -m myjournal close <id> --reason \"...\"`.]",
    ]
    for it in items:
        text = " ".join(str(it["text"]).split())
        if len(text) > MAX_TEXT:
            text = text[: MAX_TEXT - 1] + "…"
        bell = ""
        if it.get("pinged_at"):
            try:
                at = datetime.fromisoformat(it["pinged_at"]).astimezone()
                bell = f" [🔔 watchdog already pinged them {at:%a %H:%M}; follow up, don't re-announce]"
            except ValueError:
                bell = " [🔔 watchdog already pinged them]"
        lines.append(f"- {it['id']} ({it['kind']}/{it['mood']}){bell}: {text}")
    return "\n".join(lines)


def build_nudge(*, platform: str, session_id: str, parent_session_id: str = "",
                when: datetime | None = None, db: Path | None = None,
                state: dict | None = None) -> tuple[str | None, dict]:
    """Pure core (testable): returns (context or None, updated state)."""
    state = dict(state if state is not None else {})
    if not _eligible(platform, parent_session_id):
        return None, state
    when = when or datetime.now(timezone.utc)
    items = _due_now(db or JOURNAL_DB, when)
    now_ts = when.timestamp()
    picked = _select(items, state, session_id or "", now_ts)
    cutoff = now_ts - STATE_KEEP_DAYS * 86400
    state = {k: v for k, v in state.items() if float((v or {}).get("at", 0)) >= cutoff}
    if not picked:
        return None, state
    for it in picked:
        state[it["id"]] = {"session": session_id or "", "at": now_ts}
    return _render(picked), state


def _on_pre_llm_call(**kwargs):
    try:
        t0 = time.monotonic()
        context, new_state = build_nudge(
            platform=str(kwargs.get("platform") or ""),
            session_id=str(kwargs.get("session_id") or ""),
            parent_session_id=str(kwargs.get("parent_session_id") or ""),
            state=_load_state(),
        )
        if context is None:
            return None
        _save_state(new_state)
        logger.info("myjournal_nudge: injected %d due item(s) in %.0fms",
                    context.count("\n- "), (time.monotonic() - t0) * 1000)
        return {"context": context}
    except Exception as exc:  # never block a reply
        logger.warning("myjournal_nudge skipped: %s", exc)
        return None


def register(ctx) -> None:
    ctx.register_hook("pre_llm_call", _on_pre_llm_call)
