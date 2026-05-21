"""Rich table renderer for session reports."""

from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text

console = Console()


def render_table(sessions: list, title: str = "Claude Session Log") -> None:
    table = Table(
        title=title,
        box=box.ROUNDED,
        show_header=True,
        header_style="bold cyan",
        title_style="bold white",
        border_style="dim",
        show_lines=False,
        padding=(0, 1),
    )

    table.add_column("#",         style="dim",           width=4,  justify="right")
    table.add_column("Date",                             width=11)
    table.add_column("Start",                            width=6)
    table.add_column("End",                              width=6)
    table.add_column("Dur",       justify="right",       width=6)
    table.add_column("Tokens",    justify="right",       width=10)
    table.add_column("Project",   no_wrap=True,          width=22)
    table.add_column("Achievement",                      width=36)
    table.add_column("Model",     no_wrap=True,          width=20)
    table.add_column("Account",                          width=9)

    for row in sessions:
        tokens = row["total_tokens"] or 0
        token_str = f"{tokens:,}" if tokens else "—"
        if tokens == 0:
            token_color = "dim"
        elif tokens < 50_000:
            token_color = "green"
        elif tokens < 200_000:
            token_color = "yellow"
        else:
            token_color = "red"

        account = row["account"] or "—"
        account_text = Text(account, style="blue" if account == "personal" else "magenta")

        duration = row["duration_minutes"]
        dur_str = f"{duration}m" if duration is not None else "—"

        achievement = row["achievement"]
        achievement_text = (
            Text(achievement) if achievement else Text("—", style="dim")
        )

        model = row["model"] or "—"
        # Shorten common model names
        model = (
            model.replace("claude-sonnet-4-6", "sonnet-4.6")
                 .replace("claude-opus-4-6", "opus-4.6")
                 .replace("claude-haiku-4-5", "haiku-4.5")
                 .replace("claude-", "")
        )

        table.add_row(
            str(row["id"]),
            row["date"] or "—",
            row["start_time"] or "—",
            row["end_time"] or "—",
            dur_str,
            Text(token_str, style=token_color),
            row["project"] or "—",
            achievement_text,
            model,
            account_text,
        )

    console.print()
    console.print(table)
