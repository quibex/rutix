from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from rutix.db.models import FlushLog, MoodEntry
from rutix.integrations.claude import WeekClose
from rutix.integrations.github import FileContent
from rutix.jobs.flush_week import FlushWeekResult, flush_week

HABITS_MD = """# Привычки

## Ежедневные

| Привычка | Prio | Зачем |
|----------|------|-------|
| 🥤 Protein | p2 | x |
| 📚 Anki | p3 | x |

## По расписанию

| Привычка | Prio | Дни | Зачем |
|----------|------|-----|-------|
| 🏋️ Strength | p2 | ВТ/ЧТ/СБ | x |
"""


def _daily(
    name: str = "test", with_meals: bool = False, habits_done: list[str] | None = None
) -> str:
    habits_done = habits_done or []
    habit_lines = []
    for h in ("📚 Anki", "🥤 Protein", "🏋️ Strength"):
        marker = "x" if h in habits_done else " "
        habit_lines.append(f"- [{marker}] {h}")
    meals_block = "|  |  |  |  |  |  |\n| **Итого** |  |  |  |  |  |"
    if with_meals:
        meals_block = (
            "| Обед | Плов | 400 | 17 | 12 | 56 |\n"
            "| **Итого** |  | **400** | **17** | **12** | **56** |"
        )
    return f"""# {name}

## Сон
- Отбой:
- Подъём:

## Время (ч)
- VPN:
- Английский:

## Привычки

{chr(10).join(habit_lines)}

---

## Питание

| Приём | Что | Ккал | Б | Ж | У |
|-------|-----|------|---|---|---|
{meals_block}

---

## Что сделано

-

## Заметки

- мысль про {name}
"""


def _next_week_templates(next_dates: list[str]) -> dict[str, str]:
    """Minimal valid daily-template stubs for every requested next-week date."""
    return {
        iso: (
            f"# {iso}\n\n## Сон\n- Отбой:\n- Подъём:\n\n## Время (ч)\n- VPN:\n- Английский:\n\n"
            "## Привычки\n\n- [ ] 📚 Anki\n- [ ] 🥤 Protein\n\n---\n\n## Питание\n\n"
            "| Приём | Что | Ккал | Б | Ж | У |\n|-------|-----|------|---|---|---|\n"
            "|  |  |  |  |  |  |\n| **Итого** |  |  |  |  |  |\n\n---\n\n## Что сделано\n\n-\n\n"
            "## Заметки\n\n-\n"
        )
        for iso in next_dates
    }


@pytest.fixture
def fake_github():
    g = MagicMock()
    g.read = AsyncMock(return_value=None)
    g.write = AsyncMock(return_value="newsha")
    g.delete = AsyncMock(return_value="delsha")
    return g


@pytest.fixture
def fake_todoist():
    t = MagicMock()
    t.completed_titles_for_day = AsyncMock(return_value=set())
    return t


@pytest.fixture
def fake_claude():
    c = MagicMock()
    c.close_week = AsyncMock()
    return c


async def test_flush_week_skips_when_not_monday(session, fake_github, fake_claude, fake_todoist):
    result = await flush_week(
        session,
        fake_github,
        today=date(2026, 5, 14),  # Thursday
        claude=fake_claude,
        todoist=fake_todoist,
    )
    assert result is None
    fake_github.read.assert_not_called()
    fake_claude.close_week.assert_not_called()


async def test_flush_week_skips_when_already_flushed(
    session, fake_github, fake_claude, fake_todoist
):
    session.add(FlushLog(period_id="week:2026-W19", git_sha="x"))
    await session.commit()
    # Monday 2026-05-11 is in W20, but yesterday (Sun) was 2026-05-10 = W19
    result = await flush_week(
        session, fake_github, today=date(2026, 5, 11), claude=fake_claude, todoist=fake_todoist
    )
    assert result is None
    fake_claude.close_week.assert_not_called()


async def test_flush_week_writes_all_artifacts_and_deletes_daily(
    session, fake_github, fake_claude, fake_todoist
):
    # Monday 2026-05-11 → flush W19 (Mon 5-04 .. Sun 5-10)
    week_days = [date(2026, 5, d) for d in range(4, 11)]
    next_week_days = [date(2026, 5, d) for d in range(11, 18)]
    next_week_isos = [d.isoformat() for d in next_week_days]

    daily_contents = {
        f"daily/{d.isoformat()}.md": FileContent(
            text=_daily(str(d), with_meals=(i == 0), habits_done=["📚 Anki"]), sha=f"sha-{d}"
        )
        for i, d in enumerate(week_days)
    }
    daily_contents["habits.md"] = FileContent(text=HABITS_MD, sha="habits-sha")

    async def fake_read(path):
        return daily_contents.get(path)

    fake_github.read.side_effect = fake_read

    fake_claude.close_week.return_value = WeekClose(
        habits_aggregate={"🥤 Protein": 3, "📚 Anki": 1, "🏋️ Strength": 2},
        avg_kcal=2100,
        days_with_food_data=1,
        trend_kcal="↑",
        trend_habits="=",
        score=7,
        what_worked=["Anki стабилен"],
        what_failed=["Йога 0/3"],
        focus_next_week=["вернуть вечерний skincare"],
        next_week_daily=_next_week_templates(next_week_isos),
        user_message="🗓 W19 закрыта (7/10).",
    )

    # Add a MoodEntry that should get purged
    session.add(MoodEntry(day=date(2026, 5, 8), mood=1))
    await session.commit()

    result = await flush_week(
        session,
        fake_github,
        today=date(2026, 5, 11),
        claude=fake_claude,
        todoist=fake_todoist,
    )

    assert isinstance(result, FlushWeekResult)
    assert result.sha == "newsha"
    assert "W19" in result.user_message

    # Wrote weekly + nutrition + thoughts + 7 next-week dailies
    write_paths = [
        c.args[0] if c.args else c.kwargs["path"] for c in fake_github.write.call_args_list
    ]
    assert "weekly/2026-W19.md" in write_paths
    assert "nutrition/2026-W19.md" in write_paths
    assert "thoughts/2026-W19.md" in write_paths
    for iso in next_week_isos:
        assert f"daily/{iso}.md" in write_paths

    # Claude was called with the right inputs
    fake_claude.close_week.assert_awaited_once()
    call_kwargs = fake_claude.close_week.call_args.kwargs
    assert call_kwargs["week_id"] == "2026-W19"
    assert call_kwargs["dates"] == [d.isoformat() for d in week_days]
    assert call_kwargs["next_week_dates"] == next_week_isos
    assert call_kwargs["habits_md"] == HABITS_MD
    # 7 daily payloads passed in (some empty if file missing, but here all present)
    assert set(call_kwargs["daily_files"].keys()) == {d.isoformat() for d in week_days}

    # Deleted 7 daily files (closed week)
    delete_paths = [
        c.args[0] if c.args else c.kwargs["path"] for c in fake_github.delete.call_args_list
    ]
    for d in week_days:
        assert f"daily/{d.isoformat()}.md" in delete_paths
    assert len(delete_paths) == 7

    # Purged SQLite mood for that week
    remaining = await session.get(MoodEntry, date(2026, 5, 8))
    assert remaining is None

    # FlushLog marked
    log = await session.get(FlushLog, "week:2026-W19")
    assert log is not None


async def test_flush_week_passes_todoist_completions_per_day(
    session, fake_github, fake_claude, fake_todoist
):
    """Each of 7 days should be queried independently and passed to Claude
    keyed by ISO date — so close_week can match completions to the right day."""
    week_days = [date(2026, 5, d) for d in range(4, 11)]
    daily_contents = {
        f"daily/{d.isoformat()}.md": FileContent(text=_daily(str(d)), sha=f"s-{d}")
        for d in week_days
    }
    daily_contents["habits.md"] = FileContent(text=HABITS_MD, sha="hsha")
    fake_github.read.side_effect = lambda p: daily_contents.get(p)

    # Different Todoist returns per day to verify they don't get merged
    def per_day(d):
        if d == date(2026, 5, 4):
            return {"🥤 Protein"}
        if d == date(2026, 5, 6):
            return {"🚗 Driving theory ×3"}
        return set()

    fake_todoist.completed_titles_for_day = AsyncMock(side_effect=per_day)

    next_week_isos = [(d).isoformat() for d in [date(2026, 5, d) for d in range(11, 18)]]
    fake_claude.close_week.return_value = WeekClose(
        habits_aggregate={},
        avg_kcal=None,
        days_with_food_data=0,
        trend_kcal=None,
        trend_habits=None,
        score=5,
        what_worked=[],
        what_failed=[],
        focus_next_week=[],
        next_week_daily=_next_week_templates(next_week_isos),
        user_message="x",
    )

    await flush_week(
        session,
        fake_github,
        today=date(2026, 5, 11),
        claude=fake_claude,
        todoist=fake_todoist,
    )

    completions = fake_claude.close_week.call_args.kwargs["todoist_completions"]
    assert completions["2026-05-04"] == ["🥤 Protein"]
    assert completions["2026-05-06"] == ["🚗 Driving theory ×3"]
    assert completions["2026-05-05"] == []
