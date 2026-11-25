# Changelog

## 1.1.0

Fixes and tuning from an independent audit of the code and of how the nudge feels in daily use.

**Bug fixes**
- An `asked` loop that was rescheduled to a later date no longer comes back early: resurfacing never
  happens before the loop's own `--ask-after`, and rescheduling an asked loop re-arms it.
- Re-adding the text of a closed loop no longer wipes its history: the earlier episode (including its
  close reason) is kept in the edit log as `prior_episode`.
- The watchdog checks and claims in one write transaction and claims one reminder per run, so two
  overlapping runs can never send the same ping twice. Other due pings go out on the next ticks.
- `find` treats the query literally (an `_` or `%` no longer matches every loop id).
- Stored timestamps are always normalised to UTC, whatever offset the caller passed.
- A `--confirm` status line that ended long ago no longer raises a fresh check-in after a long absence.
- The WAL/SHM sidecar files are now mode `600`, like the database.

**Quieter nudge**
- One item per turn (was up to 3).
- The same reminder is not shown again for 24 h, even in a new session (was once per session, 12 h).
  A rescheduled reminder counts as new.
- Asked loops resurface after 72 h (was 24 h).
- New optional `MYJOURNAL_OWNER_SESSION_KEY`: limit journal context to your own chat, so group chats
  or other people talking to your bot never see your reminders. Skips are logged without content.
- The ping marker now says the watchdog *claimed* a ping (delivery is not verified).

**Other**
- `show <id> --current` prints the current fields without the (possibly long) edit history.
- Skill: keep each loop to one line; project detail goes in a linked `--reference-path` file.

## 1.0.0

First public release.
