"""Daily 03:00 cron — fetch yesterday's Todoist completions, mark matching
habits in yesterday's daily/*.md."""

import logging
import re
from datetime import date
from typing import NamedTuple

from rutix.integrations.github import GitHubClient
from rutix.integrations.todoist import TodoistClient
from rutix.markdown.daily import update_habits_checked

logger = logging.getLogger(__name__)

_CHECKBOX_RE = re.compile(r"^\s*-\s*\[[ x]\]", re.MULTILINE)
_HABIT_LABEL_RE = re.compile(r"^\s*-\s*\[([ x])\]\s*(.+?)\s*$")


class UpdateHabitsResult(NamedTuple):
    sha: str | None
    marked: list[str]


def _checkbox_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if _CHECKBOX_RE.match(line)]


def _checked_habit_labels(md: str) -> set[str]:
    labels: set[str] = set()
    in_section = False
    for line in md.splitlines():
        if line.startswith("## Привычки"):
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if not in_section:
            continue
        m = _HABIT_LABEL_RE.match(line)
        if m and m.group(1) == "x":
            labels.add(m.group(2))
    return labels


async def update_habits(
    github: GitHubClient,
    todoist: TodoistClient,
    day: date,
) -> UpdateHabitsResult:
    """Mark Todoist completions on `day`'s daily file. `marked` lists labels
    that newly flipped to [x] in this run; `sha` is None on skip/no-op.
    """
    done = await todoist.completed_titles_for_day(day)
    if not done:
        logger.info("update_habits skipped — no completions for %s", day)
        return UpdateHabitsResult(sha=None, marked=[])

    path = f"daily/{day.isoformat()}.md"
    file = await github.read(path)
    if file is None:
        logger.warning("update_habits skipped — no daily file for %s", day)
        return UpdateHabitsResult(sha=None, marked=[])

    new_text = update_habits_checked(file.text, done)
    if _checkbox_lines(new_text) == _checkbox_lines(file.text):
        logger.info("update_habits no-op — habits already checked for %s", day)
        return UpdateHabitsResult(sha=None, marked=[])

    marked = sorted(_checked_habit_labels(new_text) - _checked_habit_labels(file.text))

    sha = await github.write(
        path,
        new_text,
        f"habits({day.isoformat()}): авто-запись из rutix-bot (Todoist)",
        sha=file.sha,
    )
    logger.info("update_habits committed %s as %s (marked: %s)", day, sha, marked)
    return UpdateHabitsResult(sha=sha, marked=marked)
