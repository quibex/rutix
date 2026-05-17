from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from rutix.integrations.github import FileContent
from rutix.jobs.update_habits import update_habits


DAILY = """# 13 мая

## Привычки

- [ ] 📚 Anki
- [ ] 🌅 Skincare AM
- [ ] 🥤 Протеин

---

## Питание

| Приём | Что | Ккал | Б | Ж | У |
|-------|-----|------|---|---|---|
|  |  |  |  |  |  |
| **Итого** |  |  |  |  |  |

---

## Что сделано

- existing done

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


@pytest.fixture
def fake_claude():
    c = MagicMock()
    c.classify_completions = AsyncMock()
    return c


async def test_marks_matched_habits_and_appends_unmatched_to_done(
    fake_github, fake_todoist, fake_claude
):
    fake_todoist.completed_titles_for_day.return_value = {
        "🥤 Protein",
        "🌅 Skincare AM",
        "do the laundry",
        "Eng HW",
    }
    fake_claude.classify_completions.return_value = (
        {"🥤 Протеин", "🌅 Skincare AM"},
        ["do the laundry", "Eng HW"],
    )

    result = await update_habits(fake_github, fake_todoist, fake_claude, day=date(2026, 5, 13))

    assert result.sha == "newsha"
    assert sorted(result.marked) == ["🌅 Skincare AM", "🥤 Протеин"]
    assert result.appended_done == ["do the laundry", "Eng HW"]

    fake_github.read.assert_awaited_once_with("daily/2026-05-13.md")
    written = fake_github.write.call_args.args[1]

    assert "- [ ] 📚 Anki" in written
    assert "- [x] 🌅 Skincare AM" in written
    assert "- [x] 🥤 Протеин" in written

    done_section = written.split("## Что сделано", 1)[1].split("## Заметки", 1)[0]
    assert "- existing done" in done_section
    assert "- do the laundry" in done_section
    assert "- Eng HW" in done_section


async def test_skips_when_no_completions(fake_github, fake_todoist, fake_claude):
    fake_todoist.completed_titles_for_day.return_value = set()

    result = await update_habits(fake_github, fake_todoist, fake_claude, day=date(2026, 5, 13))

    assert result.sha is None
    assert result.marked == []
    assert result.appended_done == []
    fake_github.write.assert_not_called()
    fake_claude.classify_completions.assert_not_called()


async def test_skips_when_daily_missing(fake_github, fake_todoist, fake_claude):
    fake_github.read = AsyncMock(return_value=None)
    fake_todoist.completed_titles_for_day.return_value = {"🌅 Skincare AM"}

    result = await update_habits(fake_github, fake_todoist, fake_claude, day=date(2026, 5, 13))

    assert result.sha is None
    assert result.marked == []
    fake_github.write.assert_not_called()


async def test_skips_when_classifier_returns_nothing_already_checked(
    fake_github, fake_todoist, fake_claude
):
    """All habits already checked + no unmatched → no write."""
    pre = DAILY.replace("- [ ] 🥤 Протеин", "- [x] 🥤 Протеин")
    fake_github.read = AsyncMock(return_value=FileContent(text=pre, sha="x"))
    fake_todoist.completed_titles_for_day.return_value = {"🥤 Protein"}
    fake_claude.classify_completions.return_value = ({"🥤 Протеин"}, [])

    result = await update_habits(fake_github, fake_todoist, fake_claude, day=date(2026, 5, 13))

    assert result.sha is None
    assert result.marked == []
    fake_github.write.assert_not_called()


async def test_only_unmatched_writes_done_section(fake_github, fake_todoist, fake_claude):
    """No habit matches but completions exist — write to Что сделано only."""
    fake_todoist.completed_titles_for_day.return_value = {"random task"}
    fake_claude.classify_completions.return_value = (set(), ["random task"])

    result = await update_habits(fake_github, fake_todoist, fake_claude, day=date(2026, 5, 13))

    assert result.sha == "newsha"
    assert result.marked == []
    assert result.appended_done == ["random task"]
    written = fake_github.write.call_args.args[1]
    done_section = written.split("## Что сделано", 1)[1].split("## Заметки", 1)[0]
    assert "- random task" in done_section
    assert "- [x]" not in written.split("## Привычки", 1)[1].split("\n---\n", 1)[0]


async def test_only_habits_no_done_change(fake_github, fake_todoist, fake_claude):
    """All completions match habits — no Что сделано write."""
    fake_todoist.completed_titles_for_day.return_value = {"🌅 Skincare AM"}
    fake_claude.classify_completions.return_value = ({"🌅 Skincare AM"}, [])

    result = await update_habits(fake_github, fake_todoist, fake_claude, day=date(2026, 5, 13))

    assert result.sha == "newsha"
    assert result.marked == ["🌅 Skincare AM"]
    assert result.appended_done == []
    written = fake_github.write.call_args.args[1]
    assert "- [x] 🌅 Skincare AM" in written
    done_section = written.split("## Что сделано", 1)[1].split("## Заметки", 1)[0]
    assert done_section.count("- ") == 1


async def test_marked_excludes_already_checked_habits(fake_github, fake_todoist, fake_claude):
    """If a habit was already [x] before the run, classifier returning it as
    matched should NOT count it as newly-marked."""
    pre = DAILY.replace("- [ ] 📚 Anki", "- [x] 📚 Anki")
    fake_github.read = AsyncMock(return_value=FileContent(text=pre, sha="x"))
    fake_todoist.completed_titles_for_day.return_value = {"Anki", "Skincare AM"}
    fake_claude.classify_completions.return_value = (
        {"📚 Anki", "🌅 Skincare AM"},
        [],
    )

    result = await update_habits(fake_github, fake_todoist, fake_claude, day=date(2026, 5, 13))

    assert result.sha == "newsha"
    assert result.marked == ["🌅 Skincare AM"]


async def test_falls_back_to_exact_match_when_classifier_raises(
    fake_github, fake_todoist, fake_claude
):
    """If Anthropic API errors at 03:00 — exact-string match for habits,
    everything else to Что сделано. Better partial update than empty day."""
    fake_todoist.completed_titles_for_day.return_value = {
        "🌅 Skincare AM",  # exact match
        "🥤 Protein",  # mismatch (habit is "🥤 Протеин")
        "random task",
    }
    fake_claude.classify_completions.side_effect = RuntimeError("anthropic down")

    result = await update_habits(fake_github, fake_todoist, fake_claude, day=date(2026, 5, 13))

    assert result.sha == "newsha"
    assert result.marked == ["🌅 Skincare AM"]
    assert sorted(result.appended_done) == ["random task", "🥤 Protein"]
    written = fake_github.write.call_args.args[1]
    assert "- [x] 🌅 Skincare AM" in written
    assert "- [ ] 🥤 Протеин" in written
    done_section = written.split("## Что сделано", 1)[1].split("## Заметки", 1)[0]
    assert "- 🥤 Protein" in done_section
    assert "- random task" in done_section
