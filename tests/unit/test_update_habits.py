from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from rutix.integrations.github import FileContent
from rutix.jobs.update_habits import update_habits


DAILY = """# 13 мая

## Привычки

- [ ] 📚 Anki
- [ ] 🌅 Skincare AM

---

## Питание

| Приём | Что | Ккал | Б | Ж | У |
|-------|-----|------|---|---|---|
|  |  |  |  |  |  |
| **Итого** |  |  |  |  |  |

## Заметки

-
"""


@pytest.fixture
def fake_github():
    g = MagicMock()
    g.read = AsyncMock(return_value=FileContent(text=DAILY, sha="oldsha"))
    g.write = AsyncMock(return_value="newsha")
    return g


@pytest.fixture
def fake_todoist():
    t = MagicMock()
    t.completed_titles_for_day = AsyncMock()
    return t


async def test_update_habits_marks_done_in_yesterday(fake_github, fake_todoist):
    fake_todoist.completed_titles_for_day.return_value = {"📚 Anki"}

    result = await update_habits(fake_github, fake_todoist, day=date(2026, 5, 13))

    assert result.sha == "newsha"
    assert result.marked == ["📚 Anki"]
    fake_github.read.assert_awaited_once_with("daily/2026-05-13.md")
    written_text = fake_github.write.call_args.args[1]
    assert "- [x] 📚 Anki" in written_text
    assert "- [ ] 🌅 Skincare AM" in written_text


async def test_update_habits_skips_when_no_completions(fake_github, fake_todoist):
    fake_todoist.completed_titles_for_day.return_value = set()

    result = await update_habits(fake_github, fake_todoist, day=date(2026, 5, 13))
    assert result.sha is None
    assert result.marked == []
    fake_github.write.assert_not_called()


async def test_update_habits_skips_when_no_change(fake_github, fake_todoist):
    """If all matching habits are already checked, no write."""
    pre = DAILY.replace("- [ ] 📚 Anki", "- [x] 📚 Anki")
    fake_github.read = AsyncMock(return_value=FileContent(text=pre, sha="x"))
    fake_todoist.completed_titles_for_day.return_value = {"📚 Anki"}

    result = await update_habits(fake_github, fake_todoist, day=date(2026, 5, 13))
    assert result.sha is None
    assert result.marked == []
    fake_github.write.assert_not_called()


async def test_update_habits_skips_when_daily_missing(fake_github, fake_todoist):
    fake_github.read = AsyncMock(return_value=None)

    result = await update_habits(fake_github, fake_todoist, day=date(2026, 5, 13))
    assert result.sha is None
    assert result.marked == []


async def test_update_habits_returns_only_newly_marked(fake_github, fake_todoist):
    """Marked list should not include habits that were already [x] before the run."""
    pre = DAILY.replace("- [ ] 📚 Anki", "- [x] 📚 Anki")
    fake_github.read = AsyncMock(return_value=FileContent(text=pre, sha="x"))
    fake_todoist.completed_titles_for_day.return_value = {"📚 Anki", "🌅 Skincare AM"}

    result = await update_habits(fake_github, fake_todoist, day=date(2026, 5, 13))
    assert result.sha == "newsha"
    assert result.marked == ["🌅 Skincare AM"]
