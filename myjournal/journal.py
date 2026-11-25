"""Open-loop notebook. Records what is still alive. Does not speak."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path

HERMES_HOME = Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()
HOME = Path(os.environ.get("MYJOURNAL_HOME", HERMES_HOME / "myjournal")).expanduser()
DB_PATH = HOME / "journal.db"


def _local_tz() -> tzinfo:
    """The user's wall clock: MYJOURNAL_TZ (IANA name, e.g. Europe/Berlin), else the system zone.

    Naive times typed on the CLI ("--ask-after 2027-01-05T09:00") are read in this zone.
    Everything is STORED in UTC so stored ISO strings always compare correctly, even across DST.
    """
    name = os.environ.get("MYJOURNAL_TZ", "").strip()
    if name:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    return datetime.now().astimezone().tzinfo or timezone.utc


LOCAL_TZ = _local_tz()

KINDS = ("user_said", "i_wonder", "mid_thread", "care", "now")
# 'Right now' shelf: short-lived status facts ("WFH this week", "has a cold").
# Stored mood=wait so they never nudge while live, they never count toward
# OPEN_CAP, they always carry an end date, and at the end they either close
# themselves or (confirm=1) come back once to be asked.
NOW_KIND = "now"
NOW_CAP = 10
NOW_CHECKIN_DAYS = 3  # a confirm check-in stays askable this long
MOODS = ("warm", "useful", "wait")
STATUSES = ("open", "asked", "closed", "stale")
OPEN_CAP = 15  # live actionable loops; the nudge still shows at most 3 per turn
MAX_TEXT_CHARS = 4000
ACTIONABLE_MOODS = ("warm", "useful")
DEFAULT_TTL_DAYS = 14
WAIT_TTL_DAYS = 120  # life events (travel, family) outlive a fortnight
# A dated reminder stays live this long after it falls due, so a quiet day
# does not silently expire it.
REMINDER_GRACE_DAYS = 3
# A future reminder only occupies a live slot once it is this close to due,
# so a reminder for next month does not block this week's loops.
CAP_HORIZON_DAYS = 7
# An 'asked' loop that is still open comes back after this long, so a
# multi-part plan is not silenced forever by one mention.
ASK_RESURFACE_HOURS = 24
# Watchdog ping: a loop with ping=1 gets ONE proactive message at ask_after
# (sent by the optional cron watchdog). Older than this and it is skipped
# (stale ping); the chat nudge still carries it.
PING_LATE_HOURS = 12

SCHEMA = """
CREATE TABLE IF NOT EXISTS loops (
    id           TEXT PRIMARY KEY,
    kind         TEXT NOT NULL,
    mood         TEXT NOT NULL,
    text         TEXT NOT NULL,
    why          TEXT NOT NULL DEFAULT '',
    source       TEXT NOT NULL DEFAULT 'live',
    reference_path TEXT NOT NULL DEFAULT '',
    dedup        TEXT NOT NULL UNIQUE,
    status       TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    ask_after    TEXT,
    expires_at   TEXT,
    asked_at     TEXT,
    closed_at    TEXT,
    close_reason TEXT,
    ping         INTEGER NOT NULL DEFAULT 0,
    pinged_at    TEXT,
    confirm      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_loops_status ON loops (status, ask_after);
CREATE TABLE IF NOT EXISTS loop_edits (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    loop_id    TEXT NOT NULL,
    field      TEXT NOT NULL,
    old        TEXT,
    new        TEXT,
    edited_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_loop_edits_loop ON loop_edits (loop_id, seq);
"""

FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS loops_fts USING fts5(
    text,
    why,
    source,
    reference_path,
    content='loops',
    content_rowid='rowid'
);
CREATE TRIGGER IF NOT EXISTS loops_fts_ai AFTER INSERT ON loops BEGIN
    INSERT INTO loops_fts(rowid, text, why, source, reference_path)
    VALUES (new.rowid, new.text, new.why, new.source, new.reference_path);
END;
CREATE TRIGGER IF NOT EXISTS loops_fts_ad AFTER DELETE ON loops BEGIN
    INSERT INTO loops_fts(loops_fts, rowid, text, why, source, reference_path)
    VALUES ('delete', old.rowid, old.text, old.why, old.source, old.reference_path);
END;
CREATE TRIGGER IF NOT EXISTS loops_fts_au AFTER UPDATE ON loops BEGIN
    INSERT INTO loops_fts(loops_fts, rowid, text, why, source, reference_path)
    VALUES ('delete', old.rowid, old.text, old.why, old.source, old.reference_path);
    INSERT INTO loops_fts(rowid, text, why, source, reference_path)
    VALUES (new.rowid, new.text, new.why, new.source, new.reference_path);
END;
"""


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None = None) -> str:
    return (dt or now()).isoformat()


def _utc(dt: datetime) -> datetime:
    """Naive -> read as the user's local time; aware -> converted. Always returns UTC,
    so stored ISO strings compare lexically."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=LOCAL_TZ)
    return dt.astimezone(timezone.utc)


def _parse(value: str) -> datetime:
    return _utc(datetime.fromisoformat(value))


def local(value: str | None, fmt: str = "%Y-%m-%d") -> str:
    """Format a stored timestamp in the user's local zone for display."""
    if not value:
        return ""
    return _parse(value).astimezone(LOCAL_TZ).strftime(fmt)


def _norm(text: str) -> str:
    return " ".join((text or "").lower().split())


def dedup_key(kind: str, text: str) -> str:
    raw = f"{kind}:{_norm(text)}".encode()
    return hashlib.sha256(raw).hexdigest()[:16]


@dataclass
class Loop:
    id: str
    kind: str
    mood: str
    text: str
    why: str = ""
    source: str = "live"
    reference_path: str = ""
    status: str = "open"
    created_at: str = ""
    ask_after: str | None = None
    expires_at: str | None = None
    asked_at: str | None = None
    closed_at: str | None = None
    close_reason: str | None = None
    dedup: str = ""
    ping: int = 0
    pinged_at: str | None = None
    confirm: int = 0

    def to_dict(self) -> dict:
        return self.__dict__.copy()


class JournalError(ValueError):
    pass


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the current schema and migrate older journal databases in place."""
    conn.executescript(SCHEMA)
    columns = {
        row[1] for row in conn.execute("PRAGMA table_info(loops)").fetchall()
    }
    if "reference_path" not in columns:
        conn.execute(
            "ALTER TABLE loops ADD COLUMN reference_path TEXT NOT NULL DEFAULT ''"
        )
    if "ping" not in columns:
        conn.execute("ALTER TABLE loops ADD COLUMN ping INTEGER NOT NULL DEFAULT 0")
    if "pinged_at" not in columns:
        conn.execute("ALTER TABLE loops ADD COLUMN pinged_at TEXT")
    if "confirm" not in columns:
        conn.execute("ALTER TABLE loops ADD COLUMN confirm INTEGER NOT NULL DEFAULT 0")

    objects = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'trigger')"
        ).fetchall()
    }
    needs_rebuild = "loops_fts" not in objects or not {
        "loops_fts_ai",
        "loops_fts_ad",
        "loops_fts_au",
    }.issubset(objects)
    try:
        conn.executescript(FTS_SCHEMA)
        loop_count = conn.execute("SELECT COUNT(*) FROM loops").fetchone()[0]
        fts_count = conn.execute("SELECT COUNT(*) FROM loops_fts").fetchone()[0]
        needs_rebuild = needs_rebuild or loop_count != fts_count
        if needs_rebuild:
            conn.execute("INSERT INTO loops_fts(loops_fts) VALUES ('rebuild')")
    except sqlite3.OperationalError as exc:
        raise JournalError(f"SQLite FTS5 is required for journal search: {exc}") from exc


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    # Explicit path only: an omitted path must never land on the wrong file.
    # The CLI passes DB_PATH (or --db); tests pass a temp path.
    if path is None or str(path).strip() == "":
        raise JournalError("connect requires an explicit path")
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _ensure_schema(conn)
    conn.commit()
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return conn


def _row(row) -> Loop:
    return Loop(**{k: row[k] for k in Loop.__dataclass_fields__})


def add(
    conn: sqlite3.Connection,
    *,
    kind: str,
    text: str,
    mood: str = "useful",
    why: str = "",
    source: str = "live",
    reference_path: str = "",
    ttl_days: int | None = None,
    ask_after: datetime | None = None,
    until: datetime | None = None,
    ping: bool = False,
    confirm: bool = False,
) -> Loop:
    if kind not in KINDS:
        raise JournalError(f"bad kind: {kind}")
    if kind == NOW_KIND:
        if until is None:
            raise JournalError("a 'now' status needs --until (every status line has an end date)")
        if ask_after is not None or ping:
            raise JournalError("a 'now' status is quiet background; no --ask-after or --ping")
        mood = "wait"
    elif confirm:
        raise JournalError("--confirm is only for kind 'now'")
    if mood not in MOODS:
        raise JournalError(f"bad mood: {mood}")
    text = " ".join((text or "").split())
    if not text:
        raise JournalError("text is empty")
    if len(text) > MAX_TEXT_CHARS:
        raise JournalError(f"text too long ({MAX_TEXT_CHARS})")
    reference_path = " ".join((reference_path or "").split())
    if ask_after is not None:
        ask_after = _utc(ask_after)
        if mood == "wait":
            raise JournalError(
                "a dated reminder cannot be mood=wait (wait never surfaces); use useful or warm")
    if ping and ask_after is None:
        raise JournalError("--ping needs --ask-after (the watchdog pings at that time)")
    if until is not None:
        until = _utc(until)
        if ask_after is not None and until <= ask_after:
            raise JournalError("--until must be after --ask-after")

    sweep(conn)
    if kind == NOW_KIND and now_count(conn) >= NOW_CAP:
        raise JournalError(f"'now' shelf full ({NOW_CAP}) — close an old status first")
    if kind != NOW_KIND and mood in ACTIONABLE_MOODS and _counts_toward_cap(ask_after, now()):
        if live_count(conn) >= OPEN_CAP:
            raise JournalError(f"open cap {OPEN_CAP} reached — close or sweep first")

    key = dedup_key(kind, text)
    created = now()
    if until is not None:
        expires = until
    else:
        days = ttl_days
        if days is None:
            days = WAIT_TTL_DAYS if mood == "wait" else DEFAULT_TTL_DAYS
        expires = created + timedelta(days=max(int(days), 1))
        if ask_after is not None:
            expires = max(expires, ask_after + timedelta(days=REMINDER_GRACE_DAYS))
    # wait = remember, do not offer. Other moods are due immediately
    # unless the caller set ask_after.
    if ask_after is None and mood != "wait":
        ask_after = created

    existing = conn.execute(
        "SELECT * FROM loops WHERE dedup = ?", (key,)
    ).fetchone()
    if existing:
        loop = _row(existing)
        if loop.status in ("open", "asked"):
            raise JournalError(f"duplicate of {loop.id}")
        conn.execute(
            "UPDATE loops SET kind=?, mood=?, text=?, why=?, source=?,"
            " reference_path=?,"
            " status='open', created_at=?, ask_after=?, expires_at=?,"
            " asked_at=NULL, closed_at=NULL, close_reason=NULL,"
            " ping=?, pinged_at=NULL, confirm=? WHERE id=?",
            (kind, mood, text, (why or "").strip(), source,
             reference_path, iso(created), iso(ask_after) if ask_after else None,
             iso(expires), int(bool(ping)), int(bool(confirm)), loop.id),
        )
        conn.commit()
        return get(conn, loop.id)

    loop = Loop(
        id=uuid.uuid4().hex[:8],
        kind=kind,
        mood=mood,
        text=text,
        why=(why or "").strip(),
        source=source,
        reference_path=reference_path,
        dedup=key,
        status="open",
        created_at=iso(created),
        ask_after=iso(ask_after) if ask_after else None,
        expires_at=iso(expires),
        ping=int(bool(ping)),
        confirm=int(bool(confirm)),
    )
    conn.execute(
        "INSERT INTO loops (id, kind, mood, text, why, source, dedup, status,"
        " reference_path, created_at, ask_after, expires_at, asked_at, closed_at, close_reason,"
        " ping, pinged_at, confirm)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (loop.id, loop.kind, loop.mood, loop.text, loop.why, loop.source,
         loop.dedup, loop.status, loop.reference_path, loop.created_at, loop.ask_after,
         loop.expires_at, None, None, None, loop.ping, None, loop.confirm),
    )
    conn.commit()
    return loop


def _counts_toward_cap(ask_after: datetime | None, when: datetime) -> bool:
    return ask_after is None or ask_after <= when + timedelta(days=CAP_HORIZON_DAYS)


def live_count(conn, when: datetime | None = None, exclude: str | None = None) -> int:
    """Actionable loops occupying a slot now: open/asked, warm/useful, and
    either undated or due within CAP_HORIZON_DAYS."""
    horizon = iso((when or now()) + timedelta(days=CAP_HORIZON_DAYS))
    return conn.execute(
        "SELECT COUNT(*) AS n FROM loops WHERE status IN ('open','asked')"
        " AND mood IN ('warm','useful') AND kind != 'now'"
        " AND (ask_after IS NULL OR ask_after <= ?) AND id != ?",
        (horizon, exclude or ""),
    ).fetchone()["n"]


def now_count(conn) -> int:
    """Live 'now' status lines (their own shelf, separate from OPEN_CAP)."""
    return conn.execute(
        "SELECT COUNT(*) AS n FROM loops WHERE status IN ('open','asked') AND kind = 'now'"
    ).fetchone()["n"]


def now_items(conn) -> list[Loop]:
    """Live, still-quiet status lines, soonest-ending first."""
    rows = conn.execute(
        "SELECT * FROM loops WHERE status IN ('open','asked') AND kind = 'now'"
        " AND mood = 'wait' ORDER BY expires_at ASC"
    ).fetchall()
    return [_row(r) for r in rows]


def recently_ended_now(conn, days: int = 2, when: datetime | None = None) -> list[Loop]:
    """'now' lines that closed themselves recently, so the agent can say so once."""
    since = iso((when or now()) - timedelta(days=days))
    rows = conn.execute(
        "SELECT * FROM loops WHERE kind = 'now' AND status = 'closed'"
        " AND close_reason = 'ended (auto)' AND closed_at >= ? ORDER BY closed_at DESC",
        (since,),
    ).fetchall()
    return [_row(r) for r in rows]


def edit(
    conn: sqlite3.Connection,
    loop_id: str,
    *,
    text: str | None = None,
    why: str | None = None,
    mood: str | None = None,
    kind: str | None = None,
    reference_path: str | None = None,
    ask_after: datetime | None = None,
    until: datetime | None = None,
    ping: bool | None = None,
) -> Loop:
    """Change a live loop in place. Every changed field is logged in loop_edits."""
    loop = get(conn, loop_id)
    if loop.status not in ("open", "asked"):
        raise JournalError(f"{loop_id} is {loop.status}; only live loops can be edited")
    if kind is not None and kind != loop.kind and NOW_KIND in (kind, loop.kind):
        raise JournalError("a 'now' status and a plan are different shelves; close and re-add")
    if loop.kind == NOW_KIND:
        if mood is not None or ask_after is not None or ping:
            raise JournalError("a 'now' status only takes --text, --why or --until")
    new = {}
    if text is not None:
        text = " ".join(text.split())
        if not text:
            raise JournalError("text is empty")
        if len(text) > MAX_TEXT_CHARS:
            raise JournalError(f"text too long ({MAX_TEXT_CHARS})")
        new["text"] = text
    if why is not None:
        new["why"] = why.strip()
    if mood is not None:
        if mood not in MOODS:
            raise JournalError(f"bad mood: {mood}")
        new["mood"] = mood
    if kind is not None:
        if kind not in KINDS:
            raise JournalError(f"bad kind: {kind}")
        new["kind"] = kind
    if reference_path is not None:
        new["reference_path"] = " ".join(reference_path.split())
    if ask_after is not None:
        new["ask_after"] = iso(_utc(ask_after))
    if until is not None:
        new["expires_at"] = iso(_utc(until))
    if ping is not None:
        new["ping"] = int(bool(ping))
    changed = {k: v for k, v in new.items() if getattr(loop, k) != v}
    if not changed:
        raise JournalError("nothing to change")
    if loop.kind == NOW_KIND and "expires_at" in changed:
        if _parse(changed["expires_at"]) <= now():
            raise JournalError("--until must be in the future")
        # Extending a status (incl. a check-in that came back) re-arms it quietly.
        for field, value in (("mood", "wait"), ("ask_after", None),
                             ("status", "open"), ("asked_at", None)):
            if getattr(loop, field) != value:
                changed[field] = value

    merged = {**loop.to_dict(), **changed}
    m_ask = _parse(merged["ask_after"]) if merged["ask_after"] else None
    # Undated loops carry ask_after == created_at; only a real future date
    # makes it a dated reminder, which must stay actionable (never wait).
    if merged["mood"] == "wait" and m_ask is not None and m_ask > _parse(loop.created_at):
        if "mood" in changed or "ask_after" in changed:
            raise JournalError(
                "a dated reminder cannot be mood=wait (wait never surfaces); use useful or warm")
    if m_ask is not None and "ask_after" in changed and "expires_at" not in changed:
        floor = m_ask + timedelta(days=REMINDER_GRACE_DAYS)
        if not merged["expires_at"] or _parse(merged["expires_at"]) < floor:
            changed["expires_at"] = merged["expires_at"] = iso(floor)
    if m_ask is not None and merged["expires_at"] and _parse(merged["expires_at"]) <= m_ask:
        raise JournalError("--until must be after --ask-after")
    # Undated loops carry ask_after == created_at exactly; a ping needs a real time.
    if merged["ping"] and (m_ask is None or merged["ask_after"] == loop.created_at):
        raise JournalError("--ping needs --ask-after (the watchdog pings at that time)")
    # A new time (or turning ping back on) re-arms the one-shot watchdog ping.
    if loop.pinged_at and ("ask_after" in changed or changed.get("ping") == 1):
        changed["pinged_at"] = None

    was_counting = (loop.mood in ACTIONABLE_MOODS and
                    _counts_toward_cap(_parse(loop.ask_after) if loop.ask_after else None, now()))
    will_count = (merged["mood"] in ACTIONABLE_MOODS and _counts_toward_cap(m_ask, now()))
    if will_count and not was_counting and live_count(conn, exclude=loop_id) >= OPEN_CAP:
        raise JournalError(f"open cap {OPEN_CAP} reached — close or sweep first")

    if "text" in changed or "kind" in changed:
        key = dedup_key(merged["kind"], merged["text"])
        clash = conn.execute(
            "SELECT id, status FROM loops WHERE dedup = ? AND id != ?", (key, loop_id)
        ).fetchone()
        if clash and clash["status"] in ("open", "asked"):
            raise JournalError(f"duplicate of {clash['id']}")
        if clash:  # closed/stale twin: free its key so history is kept
            conn.execute("UPDATE loops SET dedup = dedup || ':' || id WHERE id = ?",
                         (clash["id"],))
        changed["dedup"] = key

    stamp = iso()
    sets = ", ".join(f"{k} = ?" for k in changed)
    conn.execute(f"UPDATE loops SET {sets} WHERE id = ?", (*changed.values(), loop_id))
    for field, value in changed.items():
        if field == "dedup":
            continue
        conn.execute(
            "INSERT INTO loop_edits (loop_id, field, old, new, edited_at) VALUES (?,?,?,?,?)",
            (loop_id, field, getattr(loop, field), value, stamp),
        )
    conn.commit()
    return get(conn, loop_id)


def edits(conn, loop_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT field, old, new, edited_at FROM loop_edits WHERE loop_id = ? ORDER BY seq",
        (loop_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def list_loops(conn, statuses=("open", "asked")) -> list[Loop]:
    q = ",".join("?" * len(statuses))
    rows = conn.execute(
        f"SELECT * FROM loops WHERE status IN ({q}) ORDER BY created_at DESC",
        statuses,
    ).fetchall()
    return [_row(r) for r in rows]


def due(conn, when: datetime | None = None) -> list[Loop]:
    """Loops the agent may actually bring up. wait-mood never appears.

    Open loops are due once ask_after passes. An 'asked' loop that is still
    live comes back ASK_RESURFACE_HOURS after it was last raised. Dated
    reminders (ask_after later than creation) sort before undated ones.
    """
    when = when or now()
    stamp = iso(when)
    resurface = iso(when - timedelta(hours=ASK_RESURFACE_HOURS))
    rows = conn.execute(
        "SELECT * FROM loops WHERE mood != 'wait'"
        " AND (expires_at IS NULL OR expires_at > ?)"
        " AND ((status = 'open' AND (ask_after IS NULL OR ask_after <= ?))"
        "   OR (status = 'asked' AND asked_at IS NOT NULL AND asked_at <= ?))"
        " ORDER BY CASE WHEN ask_after IS NOT NULL AND ask_after > created_at"
        "   THEN 0 ELSE 1 END,"
        " CASE mood WHEN 'warm' THEN 0 WHEN 'useful' THEN 1 ELSE 2 END,"
        " created_at ASC",
        (stamp, stamp, resurface),
    ).fetchall()
    return [_row(r) for r in rows]


def ping_due(conn, when: datetime | None = None) -> list[Loop]:
    """Loops the cron watchdog should ping now: ping=1, never pinged, live,
    ask_after passed but not more than PING_LATE_HOURS ago."""
    when = when or now()
    rows = conn.execute(
        "SELECT * FROM loops WHERE ping = 1 AND pinged_at IS NULL"
        " AND status IN ('open','asked') AND mood != 'wait'"
        " AND ask_after IS NOT NULL AND ask_after <= ? AND ask_after > ?"
        " AND (expires_at IS NULL OR expires_at > ?)"
        " ORDER BY ask_after ASC",
        (iso(when), iso(when - timedelta(hours=PING_LATE_HOURS)), iso(when)),
    ).fetchall()
    return [_row(r) for r in rows]


def mark_pinged(conn, loop_id: str) -> Loop:
    """Record the one proactive ping. The loop stays open; only close ends it."""
    loop = get(conn, loop_id)
    if loop.status not in ("open", "asked"):
        raise JournalError(f"{loop_id} is {loop.status}, not live")
    if loop.pinged_at:
        raise JournalError(f"{loop_id} already pinged at {loop.pinged_at}")
    conn.execute("UPDATE loops SET pinged_at = ? WHERE id = ?", (iso(), loop_id))
    conn.commit()
    return get(conn, loop_id)


def get(conn, loop_id: str) -> Loop:
    row = conn.execute("SELECT * FROM loops WHERE id = ?", (loop_id,)).fetchone()
    if not row:
        raise JournalError(f"no loop {loop_id}")
    return _row(row)


def mark_asked(conn, loop_id: str) -> Loop:
    """Record that the loop was raised. Re-asking a resurfaced loop restarts
    its ASK_RESURFACE_HOURS window."""
    loop = get(conn, loop_id)
    if loop.status == "closed" and loop.close_reason == "ended (auto)":
        # An auto-ended status line was told once; drop it from the board.
        conn.execute("UPDATE loops SET close_reason = 'ended (auto, told)' WHERE id = ?", (loop_id,))
        conn.commit()
        return get(conn, loop_id)
    if loop.status not in ("open", "asked"):
        raise JournalError(f"{loop_id} is {loop.status}, not open")
    conn.execute(
        "UPDATE loops SET status = 'asked', asked_at = ? WHERE id = ?",
        (iso(), loop_id),
    )
    conn.commit()
    return get(conn, loop_id)


def close(conn, loop_id: str, reason: str = "answered") -> Loop:
    loop = get(conn, loop_id)
    if loop.status in ("closed", "stale"):
        raise JournalError(f"{loop_id} already {loop.status}")
    conn.execute(
        "UPDATE loops SET status = 'closed', closed_at = ?, close_reason = ? WHERE id = ?",
        (iso(), reason, loop_id),
    )
    conn.commit()
    return get(conn, loop_id)


def sweep(conn, when: datetime | None = None) -> int:
    """Expire unused open/asked loops past expires_at. Returns how many went stale.

    'now' status lines end differently: clear-cut ones close as 'ended (auto)';
    confirm=1 ones turn into a short useful check-in so the agent asks the user once.
    """
    when = when or now()
    stamp = iso(when)
    conn.execute(
        "UPDATE loops SET status = 'closed', closed_at = ?, close_reason = 'ended (auto)'"
        " WHERE kind = 'now' AND confirm = 0 AND status IN ('open','asked')"
        " AND expires_at IS NOT NULL AND expires_at <= ?",
        (stamp, stamp),
    )
    conn.execute(
        "UPDATE loops SET mood = 'useful', status = 'open', ask_after = ?, asked_at = NULL,"
        " expires_at = ? WHERE kind = 'now' AND confirm = 1 AND mood = 'wait'"
        " AND status IN ('open','asked') AND expires_at IS NOT NULL AND expires_at <= ?",
        (stamp, iso(when + timedelta(days=NOW_CHECKIN_DAYS)), stamp),
    )
    cur = conn.execute(
        "UPDATE loops SET status = 'stale', closed_at = ?, close_reason = 'expired'"
        " WHERE status IN ('open','asked') AND expires_at IS NOT NULL AND expires_at <= ?",
        (stamp, stamp),
    )
    conn.commit()
    return cur.rowcount


def history(conn, limit: int = 30) -> list[Loop]:
    """Closed and stale loops, newest first. Never deleted."""
    rows = conn.execute(
        "SELECT * FROM loops WHERE status IN ('closed','stale')"
        " ORDER BY COALESCE(closed_at, created_at) DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [_row(r) for r in rows]


def find(conn: sqlite3.Connection, query: str, limit: int = 20) -> list[Loop]:
    """Recall across every status, including old closed/stale rows."""
    tokens = re.findall(r"[\w]+", query or "", flags=re.UNICODE)
    if not tokens:
        return []
    fts_query = " AND ".join(
        '"' + token.replace('"', '""') + '"*' for token in tokens
    )
    id_query = f"%{_norm(query)}%"
    rows = conn.execute(
        "SELECT * FROM ("
        " SELECT loops.* FROM loops_fts"
        " JOIN loops ON loops.rowid = loops_fts.rowid"
        " WHERE loops_fts MATCH ?"
        " UNION "
        " SELECT loops.* FROM loops WHERE lower(loops.id) LIKE ?"
        ") AS matches ORDER BY matches.created_at DESC LIMIT ?",
        (fts_query, id_query, int(limit)),
    ).fetchall()
    return [_row(r) for r in rows]


def counts(conn) -> dict[str, int]:
    out = {s: 0 for s in STATUSES}
    for row in conn.execute("SELECT status, COUNT(*) AS n FROM loops GROUP BY status"):
        out[row["status"]] = row["n"]
    return out


def dump(loops: list[Loop]) -> str:
    return json.dumps([x.to_dict() for x in loops], indent=2, ensure_ascii=False)
