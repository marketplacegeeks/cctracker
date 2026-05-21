"""Parse Claude Code JSONL session transcripts."""

import json
from datetime import datetime, timezone
from pathlib import Path


def parse_transcript(transcript_path: str) -> dict | None:
    """
    Parse a Claude Code session JSONL and return a session data dict.

    Returns None for ghost sessions — transcripts that contain only hook
    progress events with no user messages or assistant responses (e.g. Claude
    was opened then immediately closed without typing anything).

    Token accounting:
      input  = input_tokens + cache_creation_input_tokens + cache_read_input_tokens
      output = output_tokens
    These are summed across every assistant message in the file.
    """
    path = Path(transcript_path)
    session_id = path.stem
    account = _account_from_path(path)

    input_tokens = 0
    output_tokens = 0
    model = None
    start_time = None   # ISO string of first user message
    last_msg_time = None  # ISO string of last assistant message
    cwd = None
    has_conversation = False  # True once any user or assistant message is seen

    try:
        with open(path, encoding="utf-8") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                msg_type = obj.get("type")

                if msg_type == "user":
                    has_conversation = True
                    if cwd is None:
                        cwd = obj.get("cwd")
                    if start_time is None:
                        start_time = obj.get("timestamp")

                elif msg_type == "assistant":
                    has_conversation = True
                    msg = obj.get("message", {})
                    usage = msg.get("usage", {})

                    input_tokens += (
                        usage.get("input_tokens", 0)
                        + usage.get("cache_creation_input_tokens", 0)
                        + usage.get("cache_read_input_tokens", 0)
                    )
                    output_tokens += usage.get("output_tokens", 0)

                    if model is None:
                        model = msg.get("model")

                    ts = obj.get("timestamp")
                    if ts:
                        last_msg_time = ts

    except (OSError, IOError):
        pass

    if not has_conversation:
        return None

    now = datetime.now(timezone.utc)
    start_dt = _parse_ts(start_time) if start_time else now
    # Use now as end_time: the Stop hook fires when the response is delivered
    end_dt = now

    duration = max(0, int((end_dt - start_dt).total_seconds() / 60))

    return {
        "session_uuid": session_id,
        "date": start_dt.strftime("%Y-%m-%d"),
        "start_time": start_dt.strftime("%H:%M"),
        "end_time": end_dt.strftime("%H:%M"),
        "duration_minutes": duration,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "project": _project_name(cwd, path),
        "model": model or "unknown",
        "account": account,
        "cwd": cwd or "",
        "transcript_path": str(path),
    }


def parse_start_stub(hook_data: dict) -> dict | None:
    """
    Build a minimal stub session dict from a SessionStart hook payload.

    The stub is written to the DB with model='pending' and zero tokens so that
    if Claude Code is killed before the Stop hook fires, the session still
    exists in the DB. The Stop hook's upsert (or a backfill run) will
    overwrite the stub with full data once the transcript is available.

    Returns None if transcript_path is absent from the hook payload.
    """
    transcript_path = hook_data.get("transcript_path", "")
    if not transcript_path:
        return None

    path = Path(transcript_path)
    now = datetime.now(timezone.utc)

    return {
        "session_uuid": hook_data.get("session_id", path.stem),
        "date": now.strftime("%Y-%m-%d"),
        "start_time": now.strftime("%H:%M"),
        "end_time": now.strftime("%H:%M"),
        "duration_minutes": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "project": _project_name(hook_data.get("cwd"), path),
        "model": "pending",
        "account": _account_from_path(path),
        "cwd": hook_data.get("cwd", ""),
        "transcript_path": str(path),
    }


# ── helpers ───────────────────────────────────────────────────────────────────

def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _account_from_path(path: Path) -> str:
    """
    Derive an account label from where the transcript lives.

    Examples:
      ~/.claude/projects/…          → "default"
      ~/.claude-work/projects/…     → "work"
      ~/.claude-personal/projects/… → "personal"
      ~/.claude-acme/projects/…     → "acme"
    """
    home = Path.home()
    for parent in path.parents:
        if parent.parent == home and parent.name.startswith(".claude"):
            suffix = parent.name[len(".claude"):]   # e.g. "" / "-work" / "-personal"
            return suffix.lstrip("-") or "default"
    return "unknown"


def _project_name(cwd: str | None, path: Path) -> str:
    """Derive a human-readable project name."""
    if cwd:
        return Path(cwd).name or cwd

    # Fallback: decode the encoded directory name
    # e.g. -Users-aniket-Documents-Development-myproject → myproject
    parts = path.parent.name.lstrip("-").split("-")
    return parts[-1] if parts else path.parent.name
