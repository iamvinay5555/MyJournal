"""myjournal — inspect and write the open-loop notebook. Nothing here sends messages."""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

from . import journal as J

DUE_PREVIEW_CHARS = 280


def _conn(args):
    return J.connect(Path(args.db).expanduser() if args.db else J.DB_PATH)


def _when(value: str, flag: str):
    """Parse an ISO date/datetime flag. Returns (datetime | None, error | None)."""
    if not value:
        return None, None
    try:
        return datetime.fromisoformat(value), None
    except ValueError:
        return None, f"{flag} must be YYYY-MM-DD or an ISO datetime"


def _preview(text: str) -> str:
    return text if len(text) <= DUE_PREVIEW_CHARS else text[:DUE_PREVIEW_CHARS].rstrip() + "…"


def _print_loops(loops, quiet=False, empty="none"):
    if not loops:
        if not quiet:
            print(empty)
        return
    for loop in loops:
        extra = f"  — {loop.why}" if loop.why else ""
        print(f"{loop.id}  {loop.status:<6} [{loop.kind}/{loop.mood}] {loop.text}{extra}")


def cmd_add(args) -> int:
    until, err = _when(args.until, "--until")
    ask_after, err2 = _when(args.ask_after, "--ask-after")
    if err or err2:
        print(f"add failed: {err or err2}", file=sys.stderr)
        return 1
    try:
        loop = J.add(
            _conn(args), kind=args.kind, text=args.text, mood=args.mood,
            why=args.why or "", source=args.source,
            reference_path=args.reference_path or "", ttl_days=args.ttl_days,
            ask_after=ask_after, until=until, ping=args.ping, confirm=args.confirm,
        )
    except J.JournalError as exc:
        print(f"add failed: {exc}", file=sys.stderr)
        return 1
    bell = " 🔔" if loop.ping else ""
    print(f"+ {loop.id}{bell}  [{loop.kind}/{loop.mood}] {loop.text}")
    return 0


def cmd_list(args) -> int:
    conn = _conn(args)
    J.sweep(conn)
    statuses = tuple(args.status.split(",")) if args.status else ("open", "asked")
    loops = J.list_loops(conn, statuses=statuses)
    if args.json:
        print(J.dump(loops))
        return 0
    _print_loops(loops, quiet=args.quiet, empty="no open loops")
    return 0


def cmd_due(args) -> int:
    conn = _conn(args)
    J.sweep(conn)
    loops = J.due(conn)
    if args.json:
        print(J.dump(loops))
        return 0
    if not loops:
        if not args.quiet:
            print("nothing due")
        return 0
    print(f"due now ({len(loops)}):\n")
    for loop in loops:
        extra = f"  — {loop.why}" if loop.why else ""
        reference = f"  [ref: {loop.reference_path}]" if loop.reference_path else ""
        print(f"  {loop.id}  [{loop.kind}/{loop.mood}] {_preview(loop.text)}{reference}{extra}")
    return 0


def cmd_ask(args) -> int:
    try:
        loop = J.mark_asked(_conn(args), args.id)
    except J.JournalError as exc:
        print(f"ask failed: {exc}", file=sys.stderr)
        return 1
    print(f"asked {loop.id}  [{loop.kind}] {loop.text}")
    return 0


def cmd_close(args) -> int:
    try:
        loop = J.close(_conn(args), args.id, reason=args.reason)
    except J.JournalError as exc:
        print(f"close failed: {exc}", file=sys.stderr)
        return 1
    print(f"closed {loop.id}  ({loop.close_reason})")
    return 0


def cmd_sweep(args) -> int:
    n = J.sweep(_conn(args))
    if not (args.quiet and not n):
        print(f"swept {n} stale")
    return 0


def cmd_history(args) -> int:
    loops = J.history(_conn(args), limit=args.limit)
    if args.json:
        print(J.dump(loops))
        return 0
    _print_loops(loops, quiet=args.quiet)
    return 0


def cmd_find(args) -> int:
    loops = J.find(_conn(args), args.query)
    if args.json:
        print(J.dump(loops))
        return 0
    _print_loops(loops, quiet=args.quiet)
    return 0


def cmd_doctor(args) -> int:
    try:
        conn = _conn(args)
        J.sweep(conn)
        n = J.counts(conn)
    except Exception as exc:
        print(f"myjournal doctor — {exc}")
        return 1
    live = J.live_count(conn)
    if live > J.OPEN_CAP:
        print("myjournal doctor — issues found:")
        print(f"  x live loops {live} exceed cap {J.OPEN_CAP}")
        return 1
    if not args.quiet:
        print(f"myjournal doctor — all good  open={n['open']} asked={n['asked']}"
              f" closed={n['closed']} stale={n['stale']}")
    return 0


def cmd_edit(args) -> int:
    kw = {f: getattr(args, f) for f in ("text", "why", "mood", "kind", "reference_path")
          if getattr(args, f) is not None}
    if args.ping is not None:
        kw["ping"] = args.ping
    for field, flag in (("ask_after", "--ask-after"), ("until", "--until")):
        when, err = _when(getattr(args, field), flag)
        if err:
            print(f"edit failed: {err}", file=sys.stderr)
            return 1
        if when is not None:
            kw[field] = when
    try:
        loop = J.edit(_conn(args), args.id, **kw)
    except J.JournalError as exc:
        print(f"edit failed: {exc}", file=sys.stderr)
        return 1
    bell = " 🔔" if loop.ping else ""
    print(f"~ {loop.id}{bell}  [{loop.kind}/{loop.mood}] {loop.text}")
    return 0


def cmd_show(args) -> int:
    conn = _conn(args)
    try:
        loop = J.get(conn, args.id)
    except J.JournalError as exc:
        print(f"show failed: {exc}", file=sys.stderr)
        return 1
    fmt = "%Y-%m-%d %H:%M %Z"
    print(f"{loop.id}  {loop.status}  [{loop.kind}/{loop.mood}]")
    print(f"  text:    {loop.text}")
    if loop.why:
        print(f"  why:     {loop.why}")
    if loop.reference_path:
        print(f"  ref:     {loop.reference_path}")
    print(f"  created: {J.local(loop.created_at, fmt)}")
    if loop.ask_after and loop.ask_after > loop.created_at:
        print(f"  due:     {J.local(loop.ask_after, fmt)}")
    print(f"  expires: {J.local(loop.expires_at, fmt)}")
    if loop.ping:
        print(f"  ping:    🔔 {'pinged ' + J.local(loop.pinged_at, fmt) if loop.pinged_at else 'armed'}")
    if loop.asked_at:
        print(f"  asked:   {J.local(loop.asked_at, fmt)}")
    if loop.closed_at:
        print(f"  closed:  {J.local(loop.closed_at, fmt)} ({loop.close_reason})")
    hist = J.edits(conn, loop.id)
    if hist:
        print("  edits:")
        for h in hist:
            print(f"    {J.local(h['edited_at'], '%Y-%m-%d %H:%M')}  {h['field']}: {h['old']!s} -> {h['new']!s}")
    return 0


def cmd_board(args) -> int:
    """The whole journal at a glance, grouped by when it matters."""
    conn = _conn(args)
    J.sweep(conn)
    when = J.now()
    week = J.iso(when + timedelta(days=J.CAP_HORIZON_DAYS))
    stamp = J.iso(when)
    groups = {k: [] for k in ("Due now", "Asked, waiting", "Next 7 days", "Later", "Parked")}
    for loop in reversed(J.list_loops(conn)):
        if loop.kind == J.NOW_KIND and loop.mood == "wait":
            continue  # shown on the Right now shelf above
        dated = bool(loop.ask_after and loop.ask_after > loop.created_at)
        if loop.mood == "wait":
            key = "Parked"
        elif loop.status == "asked":
            key = "Asked, waiting"
        elif not loop.ask_after or loop.ask_after <= stamp:
            key = "Due now"
        elif loop.ask_after <= week:
            key = "Next 7 days"
        else:
            key = "Later"
        groups[key].append((loop, dated))
    print(f"myjournal board — live {J.live_count(conn)}/{J.OPEN_CAP}"
          f"  now {J.now_count(conn)}/{J.NOW_CAP}")
    shelf = J.now_items(conn)
    if shelf:
        print(f"\nRight now — background, never recite ({len(shelf)})")
        for loop in shelf:
            ask = " ask-at-end" if loop.confirm else ""
            print(f"  {loop.id} [until {J.local(loop.expires_at)}{ask}] {loop.text}")
    ended = J.recently_ended_now(conn, when=when)
    if ended:
        print(f"\nEnded on its own — mention once in one line ({len(ended)})")
        for loop in ended:
            print(f"  {loop.id} {loop.text}")
    for title, items in groups.items():
        if not items:
            continue
        print(f"\n{title} ({len(items)})")
        for loop, dated in items:
            when_s = f" [{J.local(loop.ask_after)}]" if dated else ""
            if loop.ping:
                when_s += " 🔔" if not loop.pinged_at else " 🔔✓"
            print(f"  {loop.id}{when_s} {_preview(loop.text)}")
    return 0


def cmd_ping_due(args) -> int:
    loops = J.ping_due(_conn(args))
    if args.json:
        print(J.dump(loops))
        return 0
    _print_loops(loops, quiet=args.quiet)
    return 0


def cmd_pinged(args) -> int:
    try:
        loop = J.mark_pinged(_conn(args), args.id)
    except J.JournalError as exc:
        print(f"pinged failed: {exc}", file=sys.stderr)
        return 1
    print(f"pinged {loop.id}  {J.local(loop.pinged_at, '%Y-%m-%d %H:%M')}  [{loop.kind}] {loop.text}")
    return 0


def _output_flags(sp):
    sp.add_argument("--json", action="store_true")
    sp.add_argument("-q", "--quiet", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="myjournal", description=__doc__.splitlines()[0])
    p.add_argument("--db", default=None, help="journal file (default: $MYJOURNAL_HOME/journal.db)")
    p.add_argument("-q", "--quiet", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="remember a loop")
    a.add_argument("--kind", required=True, choices=J.KINDS)
    a.add_argument("--mood", default="useful", choices=J.MOODS)
    a.add_argument("--text", required=True)
    a.add_argument("--why", default="")
    a.add_argument("--source", default="live")
    a.add_argument("--reference-path", default="")
    a.add_argument("--ttl-days", type=int, default=None)
    a.add_argument("--until", default="", help="ISO date this loop stays live until")
    a.add_argument("--ask-after", default="", dest="ask_after",
                   help="ISO date/time it becomes due (local time; stays live 3 days after)")
    a.add_argument("--ping", action="store_true",
                   help="the optional watchdog sends ONE proactive message at --ask-after")
    a.add_argument("--confirm", action="store_true",
                   help="kind 'now' only: at --until, come back once to ask instead of auto-closing")
    a.set_defaults(fn=cmd_add)

    ls = sub.add_parser("list", help="show live loops")
    ls.add_argument("--status", default="")
    _output_flags(ls)
    ls.set_defaults(fn=cmd_list)

    d = sub.add_parser("due", help="what the agent may bring up now")
    _output_flags(d)
    d.set_defaults(fn=cmd_due)

    ak = sub.add_parser("ask", help="mark a loop as raised in conversation")
    ak.add_argument("id")
    ak.set_defaults(fn=cmd_ask)

    cl = sub.add_parser("close", help="resolve a loop")
    cl.add_argument("id")
    cl.add_argument("--reason", default="answered",
                    help="e.g. answered, done, obsolete, merged into <id>")
    cl.set_defaults(fn=cmd_close)

    ed = sub.add_parser("edit", help="change a live loop in place (history kept)")
    ed.add_argument("id")
    ed.add_argument("--text", default=None)
    ed.add_argument("--why", default=None)
    ed.add_argument("--mood", default=None, choices=J.MOODS)
    ed.add_argument("--kind", default=None, choices=J.KINDS)
    ed.add_argument("--reference-path", default=None, dest="reference_path")
    ed.add_argument("--ask-after", default="", dest="ask_after")
    ed.add_argument("--until", default="")
    pg = ed.add_mutually_exclusive_group()
    pg.add_argument("--ping", dest="ping", action="store_true", default=None,
                    help="arm the one-shot watchdog ping")
    pg.add_argument("--no-ping", dest="ping", action="store_false",
                    help="disarm the watchdog ping")
    ed.set_defaults(fn=cmd_edit)

    sh = sub.add_parser("show", help="one loop in full, with its edit history")
    sh.add_argument("id")
    sh.set_defaults(fn=cmd_show)

    sub.add_parser("board", help="whole journal: right-now shelf, then due / asked / week / later / parked"
                   ).set_defaults(fn=cmd_board)
    sub.add_parser("sweep", help="expire old unused loops").set_defaults(fn=cmd_sweep)
    sub.add_parser("doctor", help="verify the notebook").set_defaults(fn=cmd_doctor)

    hs = sub.add_parser("history", help="closed and stale loops (never deleted)")
    hs.add_argument("--limit", type=int, default=30)
    _output_flags(hs)
    hs.set_defaults(fn=cmd_history)

    fd = sub.add_parser("find", help="recall a loop by text, including old ones")
    fd.add_argument("query")
    _output_flags(fd)
    fd.set_defaults(fn=cmd_find)

    pd = sub.add_parser("ping-due", help="loops the watchdog should ping now (read-only)")
    _output_flags(pd)
    pd.set_defaults(fn=cmd_ping_due)

    pn = sub.add_parser("pinged", help="record the one watchdog ping (loop stays open)")
    pn.add_argument("id")
    pn.set_defaults(fn=cmd_pinged)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
