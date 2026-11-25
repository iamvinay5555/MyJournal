# Nudge plugin and watchdog

## Nudge plugin (`myjournal_nudge`)

A Hermes `pre_llm_call` hook. On each interactive turn it opens the journal **read-only**, finds
due loops with the same rule as `myjournal due`, and injects at most 1 short line of context
telling the agent what is due and how to `ask`/`close` it. It never writes the journal.

- Surfaces: `MYJOURNAL_NUDGE_PLATFORMS` (comma list, default `telegram,cli`). Subagents, Kanban
  workers and single-query runs are always skipped. Cron sessions never match.
- Name used in the context line: `MYJOURNAL_USER_NAME` (default "the user").
- Throttle: the same dated occurrence is shown at most once per 24 h, across sessions (a reschedule
  counts as a new occurrence). State lives in `nudge_state.json` next to the journal (mode 600).
- Owner gate (recommended if your bot is in groups or talks to other people): set
  `MYJOURNAL_OWNER_SESSION_KEY` to your own chat's Hermes session key, e.g.
  `agent:main:telegram:dm:<your user id>`. Messaging turns from any other chat then get no journal
  context; the CLI is unaffected. Skipped turns are logged (without content), so a wrong key shows up
  in the logs instead of silently muting the journal.
- Any error -> silence; it can never block a reply.
- Enable: `hermes plugins enable myjournal_nudge`, then restart the gateway / CLI.
- Set these env vars in the environment the gateway / CLI process runs with.

## Watchdog (optional, for `--ping` loops only)

`scripts/myjournal_watchdog.py` is a Hermes cron **pre-run script**:

- Nothing to ping -> prints `{"wakeAgent": false}`: the tick is silent and costs no tokens.
- Something due -> claims ONE loop as pinged FIRST, in a single write transaction (at-most-once, even
  if two runs overlap), then wakes the cron agent with
  `{"now_local", "loops_to_ping": [{id, text, why, due_at, status}]}`.
- A ping older than 12 h is skipped as stale; the chat nudge still carries it.
- The loop stays open after the ping; the conversation closes it.

Create the job only after the user explicitly asks for proactive pings. Example prompt:

```
You are <agent name>, sending the user ONE short proactive message because a journal
reminder they asked to be pinged about has come due. They have NOT messaged you; this
message opens the conversation. You have no tools; just write.

The script context above has now_local and loops_to_ping (id, text, why, due_at).
Say each reminder plainly in everyday words with the time it was due, in 1-3 short lines,
in your usual voice. No filler questions, no other plans, no invented details.
The script already marked these loops as pinged; they stay open for the chat.
Your final response is delivered verbatim, so output ONLY the message.
```

Schedule: `every 5m`, script `myjournal_watchdog.py`, delivered to the user's DM, with an empty
toolset (`enabled_toolsets: []`) since the agent only writes text.
