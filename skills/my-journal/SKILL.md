---
name: my-journal
description: Use for open loops and reminders. Remember, ask, close.
version: 1.1.0
author: MyJournal contributors
license: MIT
metadata:
  hermes:
    tags: [journal, open-loops, reminders, companion, memory]
---

# MyJournal — the open-loop notebook

A tiny notebook of what is still *alive* between the agent and its user: promises,
questions the agent wants to ask later, threads left unfinished, things to care about,
and short-lived "right now" facts. It is not a task manager and not long-term memory,
and it never sends a message by itself.

**This skill is meant to be loaded in EVERY session.** Install it under `skills.auto_load` in
`~/.hermes/config.yaml` (see *Setup check* below); a journal the agent only reads sometimes
misses reminders.

Home: `${MYJOURNAL_HOME:-$HERMES_HOME/myjournal}` (default `~/.hermes/myjournal`).
DB: `journal.db` in that folder (mode 600, SQLite WAL + FTS5 full-history search).
Times typed without an offset are read in `MYJOURNAL_TZ` (IANA name) or the system zone; storage is UTC.

## Setup check (first time, and whenever this skill was loaded by hand)

1. Check that `my-journal` is listed under `skills.auto_load` in the active profile's
   `config.yaml` (`hermes config show` or read the file). If it is missing, tell the user in one
   line and **ask before editing**: auto-load means the skill is in every new session's prompt
   (CLI, TUI, gateway, cron and API), which is what makes the journal reliable. On their yes, ADD
   `my-journal` to the existing list, never replace other entries, then read the file back. It
   takes effect from the next session (`/new` or a restart); never claim it is active sooner.
2. Check the nudge plugin: `hermes plugins list` should show `myjournal_nudge` enabled. If not,
   offer `hermes plugins enable myjournal_nudge` (also needs a restart). Optional but recommended.
3. Never create the watchdog cron job unless the user asks for proactive pings.

## Session start (mandatory)

At the start of every new or reset session, and after context compaction, run
`cd ~/.hermes/myjournal && python3 -m myjournal board` and **read** its output before the first
reply. Obey its *Right now* block in every reply, keep *Due now* and *Next 7 days* in mind, and
never print the board to the user unless they ask.

## When to Use

- The user commits to something, asks to be reminded, or leaves something hanging.
- The agent wants to remember to ask something later.
- A thread is unfinished and would be lost when the session ends.
- The user states a temporary status ("WFH this week", "has a cold").
- After the agent actually raises a loop (`ask`) or it is resolved (`close`).

Do not use it for project tasks (use a task manager / Kanban), durable facts about the
user (long-term memory), secrets, or as an excuse to message the user unprompted.

## Kinds and moods

- Kinds: `user_said` (the user committed / asked), `i_wonder` (the agent wants to ask),
  `mid_thread` (left unfinished), `care` (something kind to remember), `now` (temporary status).
- Moods: `warm` (relationship / feeling), `useful` (practical), `wait` (remember only; never due).
- Cap: 15 live `warm`/`useful` loops that are undated or due within 7 days; further-out
  reminders don't take a slot and `wait` never counts. The nudge shows at most 3 per turn.
- Entries up to 4,000 chars plus an optional `--reference-path` to a longer Markdown note.
- Default life: 14 days (`wait`: 120). Sweep marks unused past-expiry loops `stale`;
  closed and stale rows are never deleted and stay searchable.

## Commands

Run from the journal home: `cd ~/.hermes/myjournal` (or set `MYJOURNAL_HOME`).
In a terminal tool, wrap calls as `timeout 20 python3 -m myjournal … </dev/null` so a
stuck call can never hang the turn.

```bash
python3 -m myjournal due              # what may be brought up now (-q = silent if nothing)
python3 -m myjournal board            # right-now shelf, then due / asked / next 7 days / later / parked
python3 -m myjournal add --kind user_said --mood useful --text "..."
python3 -m myjournal add --kind i_wonder --mood warm --text "..." --reference-path ~/notes/x.md
python3 -m myjournal ask <id>         # the agent raised it in conversation
python3 -m myjournal close <id> --reason done     # answered / done / obsolete / "merged into <id>"
python3 -m myjournal edit <id> --text "..." [--ask-after ...] [--mood ...]   # in place, history kept
python3 -m myjournal show <id>        # full loop + edit history
python3 -m myjournal find <words>     # search everything, including closed/stale
python3 -m myjournal history          # closed + stale
python3 -m myjournal sweep
python3 -m myjournal doctor           # exit 0 healthy, 1 needs attention
```

## Dated reminders

```bash
python3 -m myjournal add --kind user_said --mood useful \
  --ask-after 2027-01-05T09:00 --text "Renew the passport"
```

- `--ask-after` = when it becomes due (local time unless an offset is given). `--until` = optional
  expiry, must be after `--ask-after`. Without `--until`, a dated reminder lives 3 days past due.
- "Tomorrow" with no hour = 09:00 local.
- **Any loop whose text names a day or time must carry `--ask-after` with mood useful/warm,
  never `wait`** (the code refuses `wait` + `--ask-after`). Why: a dated `wait` loop never surfaces.
- Save a timed errand or commitment in the same turn the user mentions it, without asking
  "want me to remind you?"; it is an in-conversation follow-up, not permission to message them.
- `--ping` = the optional watchdog sends ONE proactive message at `--ask-after`. Use it only
  when the user wants a reminder even if they are not chatting. Never create one cron job per reminder.

## Right now shelf

```bash
python3 -m myjournal add --kind now --text "WFH Mon-Fri" --until 2027-01-09               # closes itself
python3 -m myjournal add --kind now --confirm --text "Has a cold" --until 2027-01-06      # asks at the end
python3 -m myjournal edit <id> --until 2027-01-12                                         # still true: extend
```

- Save one in the same turn the user states a temporary status; `--until` is required.
- Live lines are quiet background: read them from the board's **Right now** block, let them shape
  replies, never recite them.
- A clear-cut line closes as `ended (auto)`: mention it once, then `ask <id>` so later sessions
  don't repeat it. A `--confirm` line comes back as a due check-in for 3 days: ask once, then close or extend.
- A status that turns out to be permanent belongs in long-term memory; close the line.

## How the agent behaves

1. Run the *Session start* board check above; "silently" means don't print it to the user, never
   that you skip reading it.
2. Due items also arrive each turn through the `myjournal_nudge` plugin, if installed.
3. Bring at most ONE due loop into a reply, in your own voice, and only when it is time to act
   or it relates to what the user just said. Never as a list; never tack plans onto unrelated replies.
4. The current conversation beats any journal item.
5. After raising a loop: `ask <id>`. When it is done: `close <id> --reason ...`.
6. After every `add`/`edit`/`close`, confirm in ONE line, e.g.
   `saved to journal — <id> <text>` / `closed in journal — <id> <text>`. Never save or close silently.
7. Read back with `show <id>` before claiming a change landed.
8. Refine with `edit`, never a second `add`. Merge = `edit` the keeper + `close` the other with
   `--reason "merged into <id>"`. When the user corrects a time, update both the text and `--ask-after`.
9. When work finishes or replaces an older plan, `find <topic>` and close what it obsoletes.
10. When the user says something is done, close it right away. For "what's left?", read the live
    board and phrase likely-done items as candidates until the user confirms.
11. An `asked` loop resurfaces in `due` 24 h after it was last asked, so multi-step plans are not lost.

## Journal vs. casual talk

Write a loop only for: (1) a future hook (date, trip, promise, parked review); (2) a commitment by
either side; (3) something unfinished that dies if the session ends; (4) care that would hurt to forget.

Don't write: feelings or banter that already landed, durable identity facts, scores/news/inbox noise,
vague "we should build something", secrets, or anything about other people's private accounts.
Unsure? Ask once. A missed loop can be added later; a junk loop nags forever.

## Common Pitfalls

1. Treating `list` as `due`, or filing a dated reminder as `wait`.
2. Leaving a raised loop `open`; mark `ask` in the same turn.
3. Dumping the journal into chat; one loop, or none.
4. Storing a durable fact here instead of long-term memory.
5. Building a send path "just to use the journal". Speaking first is a separate, explicit user decision.

## References

- `references/watchdog-and-nudge.md` — the nudge plugin and the optional ping watchdog cron.

## Verification Checklist

- [ ] `python3 -m unittest discover -s tests` passes (in the repo checkout)
- [ ] `python3 -m myjournal doctor` exits 0
- [ ] `journal.db` is mode 600
- [ ] `my-journal` is in `skills.auto_load` (and `myjournal_nudge` is enabled, if wanted)
- [ ] No cron job exists unless the user asked for watchdog pings
