"""Read-or-create helper for daily/<date>.md files.

The user maintains daily files in Obsidian, but when one is missing the bot must
not silently drop the day's data (mood/meds, food, words, Todoist completions).
`read_or_init_daily` returns the existing file, or an in-memory scaffold with
`sha=None` so the caller's `github.write(..., sha=file.sha)` *creates* the file
in a single commit instead of failing.
"""

from datetime import date

from rutix.integrations.github import FileContent, GitHubClient
from rutix.markdown.daily import render_daily_template


def daily_path(day: date) -> str:
    return f"daily/{day.isoformat()}.md"


async def read_or_init_daily(github: GitHubClient, day: date) -> FileContent:
    """Return the daily file for `day`, scaffolding it in memory if missing.

    A returned `FileContent` with `sha=None` signals "not on the remote yet" —
    writing it with `sha=file.sha` creates the file rather than updating it.
    """
    path = daily_path(day)
    file = await github.read(path)
    if file is not None:
        return file
    return FileContent(text=render_daily_template(day), sha=None)
