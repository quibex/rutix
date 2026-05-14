"""Daily 03:00 cron — fetch yesterday's Todoist completions, mark matching
habits in yesterday's daily/*.md."""
import logging
import re
from datetime import date

from rutix.integrations.github import GitHubClient
from rutix.integrations.todoist import TodoistClient
from rutix.markdown.daily import update_habits_checked

logger = logging.getLogger(__name__)

_CHECKBOX_RE = re.compile(r"^\s*-\s*\[[ x]\]", re.MULTILINE)


def _checkbox_lines(text: str) -> list[str]:
    """Return all checkbox lines (stripped) from the text."""
    return [line.strip() for line in text.splitlines() if _CHECKBOX_RE.match(line)]


async def update_habits(
    github: GitHubClient,
    todoist: TodoistClient,
    day: date,
) -> str | None:
    """Returns commit SHA if a write happened, None otherwise."""
    done = await todoist.completed_titles_for_day(day)
    if not done:
        logger.info("update_habits skipped — no completions for %s", day)
        return None

    path = f"daily/{day.isoformat()}.md"
    file = await github.read(path)
    if file is None:
        logger.warning("update_habits skipped — no daily file for %s", day)
        return None

    new_text = update_habits_checked(file.text, done)
    # update_habits_checked may cause cosmetic whitespace diffs; compare only
    # the checkbox lines to detect real changes.
    if _checkbox_lines(new_text) == _checkbox_lines(file.text):
        logger.info("update_habits no-op — habits already checked for %s", day)
        return None

    sha = await github.write(
        path, new_text,
        f"habits({day.isoformat()}): авто-запись из rutix-bot (Todoist)",
        sha=file.sha,
    )
    logger.info("update_habits committed %s as %s", day, sha)
    return sha
