"""Daily 03:00 cron — pull yesterday's Todoist completions, split into:
- matched habits → check the box in the daily file's `## Привычки`
- the rest → append as bullets to `## Что сделано`

Classification is delegated to Claude (semantic match across RU/EN/emoji).
If the API call fails, falls back to byte-equal matching for habits and dumps
everything that didn't match into `## Что сделано` — better partial update
than skipping the day entirely.
"""

import logging
import re
from datetime import date
from typing import NamedTuple

from rutix.daily_io import daily_path, read_or_init_daily
from rutix.integrations.claude import ClaudeClient
from rutix.integrations.github import GitHubClient
from rutix.integrations.todoist import TodoistClient
from rutix.markdown.daily import append_done, parse_habit_labels, update_habits_checked

logger = logging.getLogger(__name__)

_CHECKBOX_RE = re.compile(r"^\s*-\s*\[[ x]\]", re.MULTILINE)
_HABIT_LABEL_RE = re.compile(r"^\s*-\s*\[([ x])\]\s*(.+?)\s*$")


class UpdateHabitsResult(NamedTuple):
    sha: str | None
    marked: list[str]
    appended_done: list[str] = []
    # When sha is None: which branch fired. One of:
    # - "no_completions" — Todoist returned 0 completions for the day
    # - "no_op"          — completions exist but everything was already marked
    # ("no_daily_file" is no longer produced — a missing file is scaffolded.)
    skip_reason: str | None = None


def _checkbox_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if _CHECKBOX_RE.match(line)]


def _checked_habit_labels(md: str) -> set[str]:
    """Return set of habit labels currently marked [x] in the ## Привычки section."""
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


def _exact_match_fallback(
    habit_labels: list[str], completions: list[str]
) -> tuple[set[str], list[str]]:
    """Cheap fallback when Claude is unreachable: byte-equal match for habits,
    everything else to `## Что сделано`."""
    habit_set = set(habit_labels)
    matched = {c for c in completions if c in habit_set}
    unmatched = [c for c in completions if c not in matched]
    return matched, unmatched


async def update_habits(
    github: GitHubClient,
    todoist: TodoistClient,
    claude: ClaudeClient,
    day: date,
) -> UpdateHabitsResult:
    """Mark Todoist completions on `day`'s daily file.

    `marked` lists habit labels that newly flipped to [x] in this run (excludes
    those already checked before). `appended_done` lists Todoist titles
    appended to `## Что сделано`. `sha` is None on skip/no-op.
    """
    done_titles = await todoist.completed_titles_for_day(day)
    if not done_titles:
        logger.info("update_habits skipped — no completions for %s", day)
        return UpdateHabitsResult(
            sha=None, marked=[], appended_done=[], skip_reason="no_completions"
        )

    path = daily_path(day)
    file = await read_or_init_daily(github, day)
    if file.sha is None:
        logger.info("update_habits scaffolding missing daily file for %s", day)

    try:
        habit_labels = parse_habit_labels(file.text)
    except ValueError:
        logger.warning("update_habits — no ## Привычки section in %s, all → Что сделано", path)
        habit_labels = []

    completions = sorted(done_titles)

    try:
        matched, unmatched = await claude.classify_completions(habit_labels, completions)
    except Exception:
        logger.exception("claude.classify_completions failed — falling back to exact-string match")
        matched, unmatched = _exact_match_fallback(habit_labels, completions)

    new_text = update_habits_checked(file.text, matched)
    appended_done: list[str] = []
    for title in unmatched:
        try:
            candidate = append_done(new_text, title)
        except ValueError:
            logger.warning("no ## Что сделано section in %s — dropping %r", path, title)
            break
        if candidate != new_text:
            new_text = candidate
            appended_done.append(title)

    # update_habits_checked may cause cosmetic whitespace diffs; gate on real
    # semantic change (checkboxes flipped OR a bullet appended).
    if _checkbox_lines(new_text) == _checkbox_lines(file.text) and not appended_done:
        logger.info("update_habits no-op — nothing to change for %s", day)
        return UpdateHabitsResult(sha=None, marked=[], appended_done=[], skip_reason="no_op")

    marked = sorted(_checked_habit_labels(new_text) - _checked_habit_labels(file.text))

    msg_parts = []
    if marked:
        msg_parts.append(f"{len(marked)} привычек")
    if appended_done:
        msg_parts.append(f"{len(appended_done)} в Что сделано")
    summary = " + ".join(msg_parts) if msg_parts else "no-op"

    sha = await github.write(
        path,
        new_text,
        f"habits({day.isoformat()}): {summary} (Todoist)",
        sha=file.sha,
    )
    logger.info("update_habits committed %s as %s (%s)", day, sha, summary)
    return UpdateHabitsResult(sha=sha, marked=marked, appended_done=appended_done)
