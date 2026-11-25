# MyJournal 📓

**An open-loop notebook for personal AI agents** — a small, private place where your agent keeps
track of what is still *alive* between the two of you: promises, reminders, questions it wants to
ask later, threads you left half-finished, and short-lived "right now" facts like *working from home
this week*.

Built for [Hermes Agent](https://github.com/NousResearch/hermes-agent), but the core is a plain
Python CLI with a SQLite file, so any agent that can run a shell command can use it.

> It remembers. It doesn't nag. It never sends a message on its own.

---

## Why

Long-term memory is good at *facts* ("my sister lives in Toronto"). Task managers are good at
*projects*. Neither is good at the soft, living things in a real conversation:

- "Remind me to call the bank on Friday."
- "Let's finish that trip plan this weekend."
- The question your agent meant to ask about your interview, before the session reset.
- "I've got a cold this week" — which should quietly shape every reply until it's over.

Those usually vanish when the session ends. MyJournal gives them a home, and gives the agent clear
rules about **when** to bring one up (only when it matters now), **how many** (at most one), and
**what to do after** (mark it asked, or close it).

## What you get

| Piece | Path | What it does |
|---|---|---|
| Journal CLI | `myjournal/` | `add`, `due`, `board`, `ask`, `close`, `edit`, `find`… on a local SQLite file |
| Agent skill | `skills/my-journal/SKILL.md` | Teaches the agent when to write, surface, ask and close loops |
| Nudge plugin | `plugin/myjournal_nudge/` | Hermes `pre_llm_call` hook: shows the agent what's due each turn (read-only) |
| Watchdog *(optional)* | `scripts/myjournal_watchdog.py` | Cron pre-run script for "ping me at 3 pm even if we're not chatting" |
| Tests | `tests/` | 85 unit tests (journal, CLI, nudge parity, now-shelf, watchdog) |

No dependencies beyond the Python 3.9+ standard library (SQLite with FTS5, which every normal
Python build has).

## Concepts

**Loop** — one living thing. Each loop has a *kind*, a *mood*, text (up to 4,000 chars), an optional
reason, an optional `--reference-path` to a longer note, and an expiry.

**Kinds**

| Kind | Meaning | Example |
|---|---|---|
| `user_said` | The user committed to something or asked for a reminder | "Book the dentist next week" |
| `i_wonder` | Something the agent wants to ask later | "Ask how the interview went" |
| `mid_thread` | A conversation left unfinished | "We stopped halfway through the trip budget" |
| `care` | Something kind to remember | "Mom's surgery is Thursday — check in" |
| `now` | A temporary status, kept on its own shelf | "WFH until Friday" |

**Moods**

- `warm` — about the relationship or feelings; surfaces first.
- `useful` — practical.
- `wait` — remember only. Never shows up as due (good for parked ideas).

**Guard rails built into the code**

- **Live cap of 15** actionable loops (`warm`/`useful`, undated or due within 7 days). Reminders
  further out don't take a slot. This keeps the agent from becoming a nag.
- **Dated reminders can't be `wait`** — a reminder that can never surface is a bug, so it's refused.
- **Duplicates are refused** while the original is still live.
- **Asked loops resurface after 24 h** if still open, so multi-step plans don't die after one mention.
- **Dated reminders live 3 days past due**, so one quiet day doesn't silently lose them.
- **Nothing is ever deleted.** Closed and expired (stale) loops stay searchable with full-text search.
- **Every edit is logged** (`show <id>` prints the history).
- The database is created with file mode `600`.

**Right now shelf** — `--kind now --until DATE` stores a quiet status line (own cap of 10). It never
shows up as "due"; the agent just *knows* it. At `--until` it closes itself, or with `--confirm` it
comes back once as a check-in ("is the cold gone?").

## Install (Hermes Agent)

```bash
git clone https://github.com/iamvinay5555/MyJournal.git
cd MyJournal
./install.sh                 # runs the tests, then copies files; enables nothing
hermes plugins enable myjournal_nudge   # recommended
# restart your gateway / CLI session
```

This installs:

- the journal to `~/.hermes/myjournal/` (database `journal.db` is created on first use)
- the skill to `~/.hermes/skills/productivity/my-journal/`
- the plugin to `~/.hermes/plugins/myjournal_nudge/` (disabled until you enable it)

`./install.sh --watchdog` also copies the optional ping script to `~/.hermes/scripts/` — it does
**not** create a cron job (see below).

Then add one line to your agent's instructions (system prompt, SOUL.md or AGENTS.md):

> At the start of every session and after context compaction, run
> `cd ~/.hermes/myjournal && python3 -m myjournal board` and read its output before your first
> reply. Follow the `my-journal` skill for when to add, surface, ask and close loops.

### Configuration (environment variables, all optional)

| Variable | Default | Used by |
|---|---|---|
| `MYJOURNAL_HOME` | `$HERMES_HOME/myjournal` (`~/.hermes/myjournal`) | everything — where the code and `journal.db` live |
| `MYJOURNAL_TZ` | system time zone | CLI — IANA zone (e.g. `Europe/Berlin`) for times typed without an offset |
| `MYJOURNAL_NUDGE_PLATFORMS` | `telegram,cli` | plugin — comma list of surfaces where due loops are shown |
| `MYJOURNAL_USER_NAME` | `the user` | plugin — name used in the injected context line |

All timestamps are stored in UTC and shown in your local zone, so DST changes and travel are safe.

### Using it without Hermes

The CLI works anywhere:

```bash
cd MyJournal                                   # run from the repo checkout
python3 -m myjournal --db ~/myjournal/journal.db board
```

Give your agent the rules from `skills/my-journal/SKILL.md` and let it call the CLI from its shell tool.

## CLI reference

```bash
python3 -m myjournal board                      # Right now / Due now / Asked / Next 7 days / Later / Parked
python3 -m myjournal due [-q] [--json]          # what may be brought up now (-q: silent when nothing)
python3 -m myjournal list [--status open,asked] [--json]
python3 -m myjournal add --kind KIND [--mood warm|useful|wait] --text "..." \
        [--why "..."] [--reference-path FILE] [--ask-after WHEN] [--until DATE] \
        [--ttl-days N] [--ping] [--confirm]
python3 -m myjournal ask ID                     # mark as raised in conversation
python3 -m myjournal close ID [--reason done]   # answered / done / obsolete / "merged into ID"
python3 -m myjournal edit ID [--text ...] [--why ...] [--mood ...] [--kind ...] \
        [--ask-after ...] [--until ...] [--ping | --no-ping]
python3 -m myjournal show ID                    # full loop + edit history
python3 -m myjournal find WORDS                 # full-text search, including closed/stale
python3 -m myjournal history [--limit 30]
python3 -m myjournal sweep                      # expire unused loops past their date
python3 -m myjournal doctor                     # exit 0 healthy, 1 needs attention
python3 -m myjournal ping-due | pinged ID       # used by the watchdog
```

`WHEN` / `DATE` are ISO: `2027-01-05`, `2027-01-05T09:00`, or with an offset `2027-01-05T09:00+01:00`.

### Example session

(Run on 3 January 2027.)

```text
$ python3 -m myjournal add --kind user_said --ask-after 2027-01-08T09:00 --text "Renew car insurance"
+ 3f9c1a2e  [user_said/useful] Renew car insurance

$ python3 -m myjournal add --kind now --until 2027-01-09 --text "Working from home this week"
+ 8b04d7c1  [now/wait] Working from home this week

$ python3 -m myjournal board
myjournal board — live 1/15  now 1/10

Right now — background, never recite (1)
  8b04d7c1 [until 2027-01-09] Working from home this week

Next 7 days (1)
  3f9c1a2e [2027-01-08] Renew car insurance
```

The agent then tells you, in one line: `saved to journal — 3f9c1a2e Renew car insurance`.

## How the nudge plugin works

On every interactive turn the plugin opens `journal.db` **read-only**, finds due loops (same rule as
`myjournal due`), and adds a short block of context for the agent — at most 3 items, each trimmed —
telling it to mention one only if it's time to act or relevant to what you just said, and how to
`ask`/`close` it afterwards. It is throttled (once per session, again after 12 h in long sessions),
skips subagents, Kanban workers and cron, and fails silent on any error so it can never block a reply.

Without the plugin, the agent only sees loops when it runs `board`/`due` itself.

## Optional: proactive pings (watchdog)

Only for loops added with `--ping` ("ping me at 3 pm even if I'm not chatting"). The watchdog is a
Hermes cron **pre-run script**: when nothing is due it prints `{"wakeAgent": false}` and the tick costs
nothing; when something is due it marks the loop pinged first (at-most-once) and wakes the cron agent
with the loop details so it can write one short message. The loop stays open until you deal with it
in chat. A prompt template and schedule are in
[`skills/my-journal/references/watchdog-and-nudge.md`](skills/my-journal/references/watchdog-and-nudge.md).

Create that cron job only if you actually want your agent to message you first.

## Privacy

- Everything stays on your machine in one SQLite file (mode `600`). No network calls, no telemetry.
- The plugin only reads; only the agent (via the CLI) writes.
- `.gitignore` excludes `*.db` and plugin state, so you can't commit your journal by accident.
- Don't store secrets (passwords, card numbers, API keys) in loops.

## Tests

```bash
python3 -m unittest discover -s tests -v
MYJOURNAL_TZ=America/New_York python3 -m unittest discover -s tests   # time-zone check
```

## Upgrading / uninstalling

Re-run `./install.sh` after `git pull`; your `journal.db` is left alone. To remove: delete
`~/.hermes/myjournal/myjournal/`, `~/.hermes/plugins/myjournal_nudge/` and
`~/.hermes/skills/productivity/my-journal/` (and `journal.db` if you want your loops gone too), then
`hermes plugins disable myjournal_nudge`.

## License

MIT — see [LICENSE](LICENSE). Born from a real companion-agent setup and shared so other people's
agents can remember what matters, and know when to say it.
