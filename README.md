# cctracker

Automatic Claude Code session tracker. Every session is logged with tokens consumed, duration, project, model used, and which account you were on — with zero manual effort.

## What it tracks

| Field | How it's captured |
|---|---|
| Date / Start / End | First and last message timestamps in the session transcript |
| Duration | Derived from start → end |
| Tokens (input + output) | Summed from every assistant message in the JSONL |
| Project | Last folder of your working directory at session start |
| Achievement | You add this with `cctracker note "..."` |
| Model | Extracted from the assistant messages (e.g. `claude-sonnet-4-6`) |
| Account | Derived from the config dir name (`~/.claude-work` → `work`) |

## Requirements

- Python 3.10+
- [Claude Code](https://claude.ai/download) CLI installed and configured

## Installation

```bash
pip install cctracker
```

Or install from source:

```bash
git clone https://github.com/marketplacegeeks/cctracker
cd cctracker
pip install -e .
```

## Setup (one-time)

```bash
cctracker setup
```

This scans for all `~/.claude*/` directories and installs a `Stop` hook into each `settings.json`. The hook fires automatically after every Claude response — no further configuration needed.

```
✓ /Users/you/.claude/settings.json  (default)
✓ /Users/you/.claude-work/settings.json  (work)

Hook command: /path/to/cctracker hook-stop
Restart any open Claude Code sessions for the hook to take effect.
```

### Multi-account setup

cctracker works with any number of Claude config directories. Account labels are derived automatically from the directory name:

| Directory | Account label |
|---|---|
| `~/.claude/` | `default` |
| `~/.claude-work/` | `work` |
| `~/.claude-personal/` | `personal` |
| `~/.claude-acme/` | `acme` |

## Importing existing sessions

```bash
cctracker backfill
```

Scans all discovered `~/.claude*/projects/` directories and imports every existing session. Safe to run multiple times — no duplicates.

## Commands

### View sessions

```bash
cctracker report                    # last 20 sessions
cctracker report --last 50          # last 50 sessions
cctracker report --days 7           # last 7 days
cctracker report --account work     # filter by account
```

### Add an achievement note

```bash
cctracker note "Built the auth module"         # annotate most recent session
cctracker note --id 42 "Fixed the login bug"   # annotate a specific session
```

### Export to CSV

```bash
cctracker export                             # ~/cctracker-export.csv
cctracker export --output ~/sessions.csv     # custom path
cctracker export --days 30                   # last 30 days only
```

### Re-run setup

```bash
cctracker setup    # idempotent — safe to re-run after upgrades
```

## Session data location

```
~/.cctracker/sessions.db    SQLite database
```

## Shell integration (optional)

Show a session summary automatically when you exit Claude Code by wrapping your launch commands in your shell config:

```zsh
# ~/.zshrc  (or ~/.bashrc)
_CCTRACKER="$(which cctracker)"

function claude-work() {
  claude "$@"
  "$_CCTRACKER" report --last 1
}
```

## How it works

Claude Code fires a `Stop` hook after every response turn. cctracker registers `cctracker hook-stop` as that hook. The hook receives the path to the session transcript (a JSONL file), parses it for token usage, model, timestamps, and working directory, then upserts a row into `~/.cctracker/sessions.db`.

Nothing is sent anywhere — all data stays local.

## Token accounting

```
input tokens  = input_tokens
              + cache_creation_input_tokens
              + cache_read_input_tokens
output tokens = output_tokens
total         = input + output
```

These are cumulative across the entire session (all turns summed together).

## License

MIT
