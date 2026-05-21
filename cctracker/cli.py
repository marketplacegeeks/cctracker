"""CLI entry point for cctracker."""

import json
import sys
from pathlib import Path

import click

from .parser import parse_transcript
from .report import console, render_table
from .storage import (
    get_last_session,
    get_session_by_id,
    get_sessions,
    update_achievement,
    upsert_session,
)


@click.group()
@click.version_option(package_name="cctracker")
def main():
    """cctracker — automatic Claude Code session tracker.

    Tracks every Claude Code session automatically via a Stop hook.
    Run `cctracker setup` once to install the hook, then use Claude normally.
    """


# ── helpers ───────────────────────────────────────────────────────────────────

def _discover_claude_dirs() -> list[Path]:
    """
    Find all ~/.claude* directories that contain a settings.json.

    Works for any setup:
      ~/.claude/           standard single-account
      ~/.claude-work/      custom work account
      ~/.claude-personal/  custom personal account
      ~/.claude-<any>/     any custom label
    """
    home = Path.home()
    found = []
    try:
        for d in sorted(home.iterdir()):
            if d.is_dir() and d.name.startswith(".claude") and (d / "settings.json").exists():
                found.append(d)
    except PermissionError:
        pass
    return found


def _find_cctracker_bin() -> str | None:
    """
    Locate the cctracker binary reliably across install methods
    (pip, pipx, pyenv, venv, uv, homebrew, etc.).
    """
    import shutil

    candidates = [
        Path(sys.executable).parent / "cctracker",   # same env as running Python
        Path.home() / ".pyenv" / "shims" / "cctracker",
        Path.home() / ".local" / "bin" / "cctracker",
        Path("/usr/local/bin/cctracker"),
    ]
    for c in candidates:
        if c.exists():
            return str(c)

    return shutil.which("cctracker")  # last resort: PATH lookup


# ── hook-stop ────────────────────────────────────────────────────────────────

@main.command("hook-stop")
def hook_stop():
    """
    Called automatically by Claude Code's Stop hook.
    Reads session JSON from stdin, parses the transcript, upserts into SQLite.
    Always exits 0 — never blocks Claude.
    """
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, OSError):
        sys.exit(0)

    transcript_path = data.get("transcript_path", "")
    if not transcript_path or not Path(transcript_path).exists():
        sys.exit(0)

    try:
        session_data = parse_transcript(transcript_path)
        upsert_session(session_data)
    except Exception:
        pass  # Never block Claude regardless of errors

    sys.exit(0)


# ── report ───────────────────────────────────────────────────────────────────

@main.command("report")
@click.option("--last", default=20, show_default=True, help="Number of sessions to show.")
@click.option("--days", default=None, type=int, help="Limit to last N days.")
@click.option("--account", default=None, help="Filter by account label (e.g. work, personal, default).")
def report(last, days, account):
    """Display the session log as a rich table."""
    sessions = get_sessions(limit=last, days=days, account=account)
    if not sessions:
        console.print(
            "[dim]No sessions recorded yet. "
            "Run [bold]cctracker setup[/bold] then use Claude Code normally.[/dim]"
        )
        return

    title = "Claude Session Log"
    if account:
        title += f"  ({account})"
    if days:
        title += f"  · last {days} days"

    render_table(sessions, title=title)

    total_tokens = sum(r["total_tokens"] or 0 for r in sessions)
    console.print(
        f"  [dim]{len(sessions)} session(s) shown  ·  {total_tokens:,} total tokens[/dim]\n"
    )


# ── note ─────────────────────────────────────────────────────────────────────

@main.command("note")
@click.argument("text")
@click.option(
    "--id", "session_id", default=None, type=int,
    help="Session ID to annotate. Defaults to most recent session.",
)
def note(text, session_id):
    """Add an achievement note to a session (default: most recent)."""
    if session_id is None:
        session = get_last_session()
        if not session:
            console.print("[red]No sessions found.[/red]")
            return
        session_id = session["id"]
        console.print(
            f"[dim]Updating session #{session_id}  ({session['date']}  {session['project']})[/dim]"
        )
    else:
        session = get_session_by_id(session_id)
        if not session:
            console.print(f"[red]Session #{session_id} not found.[/red]")
            return

    if update_achievement(session_id, text):
        console.print(f"[green]Saved:[/green] {text}")
    else:
        console.print(f"[red]Could not update session #{session_id}.[/red]")


# ── export ───────────────────────────────────────────────────────────────────

@main.command("export")
@click.option("--output", "output_path", default=None, help="Destination file (default: ~/cctracker-export.csv).")
@click.option("--days", default=None, type=int, help="Limit to last N days.")
@click.option("--account", default=None, help="Filter by account label.")
def export(output_path, days, account):
    """Export session log to CSV."""
    import csv

    sessions = get_sessions(limit=None, days=days, account=account)
    if not sessions:
        console.print("[dim]No sessions to export.[/dim]")
        return

    dest = Path(output_path) if output_path else Path.home() / "cctracker-export.csv"
    fields = [
        "id", "date", "start_time", "end_time", "duration_minutes",
        "input_tokens", "output_tokens", "total_tokens",
        "project", "achievement", "model", "account", "cwd",
    ]

    with open(dest, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in sessions:
            writer.writerow({k: row[k] for k in fields})

    console.print(f"[green]Exported {len(sessions)} session(s) → {dest}[/green]")


# ── setup ────────────────────────────────────────────────────────────────────

@main.command("setup")
def setup():
    """
    Install cctracker's Stop hook into all Claude Code settings files.

    Auto-discovers every ~/.claude* directory that has a settings.json.
    Safe to run multiple times — never duplicates or overwrites existing hooks.
    """
    cctracker_bin = _find_cctracker_bin()
    if not cctracker_bin:
        console.print(
            "[red]cctracker binary not found.[/red]\n"
            "Make sure the package is installed: [bold]pip install cctracker[/bold]"
        )
        return

    claude_dirs = _discover_claude_dirs()
    if not claude_dirs:
        console.print(
            "[yellow]No Claude Code config directories found.[/yellow]\n"
            "Expected at least one of: [dim]~/.claude/[/dim]  [dim]~/.claude-*/[/dim]"
        )
        return

    hook_entry = {
        "hooks": [
            {
                "type": "command",
                "command": f"{cctracker_bin} hook-stop",
                "timeout": 10,
            }
        ]
    }

    installed_any = False
    for config_dir in claude_dirs:
        settings_path = config_dir / "settings.json"

        with open(settings_path, encoding="utf-8") as f:
            settings = json.load(f)

        hooks_section = settings.setdefault("hooks", {})
        stop_hooks: list = hooks_section.setdefault("Stop", [])

        # Idempotency: skip if our hook is already there
        already = any(
            "cctracker" in h.get("hooks", [{}])[0].get("command", "")
            for h in stop_hooks
            if isinstance(h.get("hooks"), list) and h["hooks"]
        )
        if already:
            console.print(f"[dim]Already installed:[/dim] {settings_path}")
            continue

        stop_hooks.append(hook_entry)

        with open(settings_path, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
            f.write("\n")

        account_label = config_dir.name[len(".claude"):].lstrip("-") or "default"
        console.print(f"[green]✓[/green] {settings_path}  [dim]({account_label})[/dim]")
        installed_any = True

    if installed_any:
        console.print(
            f"\n[bold]Hook command:[/bold] [cyan]{cctracker_bin} hook-stop[/cyan]\n"
            "[dim]Restart any open Claude Code sessions for the hook to take effect.[/dim]\n"
        )
    else:
        console.print("\n[dim]Nothing to do — hook already installed everywhere.[/dim]\n")


# ── backfill ─────────────────────────────────────────────────────────────────

@main.command("backfill")
def backfill():
    """
    Import all existing Claude Code sessions from every ~/.claude* directory.

    Safe to run multiple times — uses UPSERT so no duplicates are created.
    Useful after first install or after switching machines.
    """
    claude_dirs = _discover_claude_dirs()
    if not claude_dirs:
        console.print("[yellow]No Claude Code config directories found.[/yellow]")
        return

    total = 0
    for config_dir in claude_dirs:
        projects_dir = config_dir / "projects"
        if not projects_dir.exists():
            continue

        jsonl_files = list(projects_dir.rglob("*.jsonl"))
        account_label = config_dir.name[len(".claude"):].lstrip("-") or "default"
        console.print(
            f"[dim]Scanning {len(jsonl_files)} transcript(s) in "
            f"{projects_dir}  ({account_label})[/dim]"
        )

        for jf in jsonl_files:
            try:
                data = parse_transcript(str(jf))
                upsert_session(data)
                total += 1
            except Exception as e:
                console.print(f"[dim]  Skipped {jf.name}: {e}[/dim]")

    console.print(f"\n[green]Backfill complete — {total} session(s) imported.[/green]\n")
