"""Parse Claude Code JSONL session transcripts."""

import json
from datetime import datetime, timezone
from pathlib import Path


def parse_transcript(transcript_path: str) -> dict:
    """
    Parse a Claude Code session JSONL and return a session data dict.

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
                    if cwd is None:
                        cwd = obj.get("cwd")
                    if start_time is None:
                        start_time = obj.get("timestamp")

                elif msg_type == "assistant":
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
