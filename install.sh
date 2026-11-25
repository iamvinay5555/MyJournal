#!/usr/bin/env bash
# MyJournal installer for Hermes Agent. Copies files only; enables nothing by itself.
#   ./install.sh            # journal + skill + nudge plugin (plugin stays disabled until you enable it)
#   ./install.sh --watchdog # also copy the optional ping watchdog script (no cron job is created)
set -euo pipefail

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
JOURNAL_HOME="${MYJOURNAL_HOME:-$HERMES_HOME/myjournal}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Running tests first..."
(cd "$HERE" && python3 -m unittest discover -s tests -q)

mkdir -p "$JOURNAL_HOME" "$HERMES_HOME/plugins" "$HERMES_HOME/skills/productivity"
rm -rf "$JOURNAL_HOME/myjournal"
cp -R "$HERE/myjournal" "$JOURNAL_HOME/myjournal"
rm -rf "$HERMES_HOME/plugins/myjournal_nudge"
cp -R "$HERE/plugin/myjournal_nudge" "$HERMES_HOME/plugins/myjournal_nudge"
rm -rf "$HERMES_HOME/skills/productivity/my-journal"
cp -R "$HERE/skills/my-journal" "$HERMES_HOME/skills/productivity/my-journal"
find "$JOURNAL_HOME" "$HERMES_HOME/plugins/myjournal_nudge" -name __pycache__ -type d -prune -exec rm -rf {} +

if [[ "${1:-}" == "--watchdog" ]]; then
  mkdir -p "$HERMES_HOME/scripts"
  cp "$HERE/scripts/myjournal_watchdog.py" "$HERMES_HOME/scripts/myjournal_watchdog.py"
  echo "Watchdog script copied to $HERMES_HOME/scripts/ (create the cron job yourself; see README)."
fi

(cd "$JOURNAL_HOME" && python3 -m myjournal doctor)

cat <<EOF

MyJournal installed.
  journal : $JOURNAL_HOME   (database: journal.db, created on first use)
  skill   : $HERMES_HOME/skills/productivity/my-journal
  plugin  : $HERMES_HOME/plugins/myjournal_nudge   (disabled)

Next steps:
  1. hermes plugins enable myjournal_nudge     # optional, recommended
  2. Restart your Hermes gateway / CLI session.
  3. Tell your agent to read the my-journal skill and run 'board' at the start of each session.
EOF
