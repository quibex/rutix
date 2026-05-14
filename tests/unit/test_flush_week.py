from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from rutix.db.models import FlushLog, MoodEntry
from rutix.integrations.github import FileContent
from rutix.jobs.flush_week import flush_week


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


def _daily(name: str = "test", with_meals: bool = False, habits_done: list[str] | None = None) -> str:
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

-
"""


@pytest.fixture
def fake_github():
    g = MagicMock()
    g.read = AsyncMock()
    g.write = AsyncMock(return_value="newsha")
    g.delete = AsyncMock(return_value="delsha")
    return g


async def test_flush_week_skips_when_not_monday(session, fake_github):
    sha = await flush_week(session, fake_github, today=date(2026, 5, 14))  # Thursday
    assert sha is None
    fake_github.read.assert_not_called()


async def test_flush_week_skips_when_already_flushed(session, fake_github):
    session.add(FlushLog(period_id="week:2026-W19", git_sha="x"))
    await session.commit()
    # Monday 2026-05-11 is in W20, but yesterday (Sun) was 2026-05-10 = W19
    sha = await flush_week(session, fake_github, today=date(2026, 5, 11))
    assert sha is None


async def test_flush_week_writes_files_and_deletes_daily(session, fake_github):
    # Monday 2026-05-11 → flush W19 (Mon 5-04 .. Sun 5-10)
    week_days = [date(2026, 5, d) for d in range(4, 11)]
    daily_contents = {
        f"daily/{d.isoformat()}.md": FileContent(text=_daily(str(d), with_meals=(i == 0), habits_done=["📚 Anki"]), sha=f"sha-{d}")
        for i, d in enumerate(week_days)
    }
    daily_contents["habits.md"] = FileContent(text=HABITS_MD, sha="habits-sha")

    async def fake_read(path):
        return daily_contents.get(path)

    fake_github.read.side_effect = fake_read

    # Add a MoodEntry that should get purged
    session.add(MoodEntry(day=date(2026, 5, 8), mood=1))
    await session.commit()

    sha = await flush_week(session, fake_github, today=date(2026, 5, 11))

    assert sha == "newsha"

    # Wrote weekly + nutrition
    write_paths = [c.args[0] if c.args else c.kwargs["path"] for c in fake_github.write.call_args_list]
    assert "weekly/2026-W19.md" in write_paths
    assert "nutrition/2026-W19.md" in write_paths

    # Deleted 7 daily files
    delete_paths = [c.args[0] if c.args else c.kwargs["path"] for c in fake_github.delete.call_args_list]
    for d in week_days:
        assert f"daily/{d.isoformat()}.md" in delete_paths
    assert len(delete_paths) == 7

    # Purged SQLite mood for that week
    remaining = await session.get(MoodEntry, date(2026, 5, 8))
    assert remaining is None

    # FlushLog marked
    log = await session.get(FlushLog, "week:2026-W19")
    assert log is not None
