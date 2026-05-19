"""Generate weekly/2026-Wxx.md.

Metric numbers and editorial sections are filled from the close_week Claude
call (semantic habit matching, score, what worked / failed, focus). When no
editorial data is passed in, sections are left as empty templates for manual
fill — keeps the function usable from tests and for the deterministic path.
"""

from dataclasses import dataclass, field
from datetime import date

RU_MONTHS_GENITIVE = {
    1: "января",
    2: "февраля",
    3: "марта",
    4: "апреля",
    5: "мая",
    6: "июня",
    7: "июля",
    8: "августа",
    9: "сентября",
    10: "октября",
    11: "ноября",
    12: "декабря",
}
RU_MONTH_SHORT = {
    1: "янв",
    2: "фев",
    3: "мар",
    4: "апр",
    5: "мая",
    6: "июн",
    7: "июл",
    8: "авг",
    9: "сен",
    10: "окт",
    11: "ноя",
    12: "дек",
}


@dataclass
class WeeklyDay:
    date: date
    done_habits: set[str]
    sleep_offh: float | None  # bedtime hour (e.g. 1.5 for 01:30) — Phase 2 keeps None
    sleep_onh: float | None  # wakeup hour
    kcal: int | None  # total kcal for the day (from daily.py parse_meals)


@dataclass
class HabitsConfig:
    daily: list[str]  # ["📚 Anki", ...]
    # {"🏋️ Strength": ["ВТ","ЧТ","СБ"]}
    scheduled: dict[str, list[str]] = field(default_factory=dict)


def russian_date_range(start: date, end: date) -> str:
    if start.month == end.month:
        return f"{start.day} — {end.day} {RU_MONTHS_GENITIVE[start.month]}"
    return f"{start.day} {RU_MONTH_SHORT[start.month]} — {end.day} {RU_MONTHS_GENITIVE[end.month]}"


def _count_habit(days: list[WeeklyDay], habit: str) -> int:
    return sum(1 for d in days if habit in d.done_habits)


def _avg_kcal(days: list[WeeklyDay]) -> int | None:
    vals = [d.kcal for d in days if d.kcal is not None]
    if not vals:
        return None
    return round(sum(vals) / len(vals))


def _bullet_section(items: list[str] | None) -> str:
    if not items:
        return "-"
    return "\n".join(f"- {it}" for it in items)


def _focus_section(items: list[str] | None) -> str:
    if not items:
        return "1."
    return "\n".join(f"{i + 1}. {it}" for i, it in enumerate(items))


def render_weekly(
    year: int,
    week_num: int,
    days: list[WeeklyDay],
    habits: HabitsConfig,
    *,
    habits_count: dict[str, int] | None = None,
    avg_kcal_override: int | None = None,
    score: int | None = None,
    what_worked: list[str] | None = None,
    what_failed: list[str] | None = None,
    focus_next_week: list[str] | None = None,
    trend_kcal: str | None = None,
) -> str:
    """Render weekly markdown.

    Counts come from `habits_count` (semantic, from Claude) if provided,
    else fall back to byte-equal counting via WeeklyDay.done_habits.
    Editorial sections are filled from the keyword args when present.
    """
    if days:
        date_range = russian_date_range(days[0].date, days[-1].date)
        title = f"# Неделя {week_num} ({date_range})"
    else:
        title = f"# Неделя {week_num}"

    def _count(label: str) -> int:
        if habits_count is not None:
            return int(habits_count.get(label, 0))
        return _count_habit(days, label)

    metric_rows = []
    for h in habits.daily:
        metric_rows.append(f"| {h} | 7 | {_count(h)} |")
    for h, weekdays in habits.scheduled.items():
        metric_rows.append(f"| {h} | {len(weekdays)} | {_count(h)} |")

    if avg_kcal_override is not None:
        avg_kcal_str = str(avg_kcal_override)
    else:
        avg_kcal = _avg_kcal(days)
        avg_kcal_str = str(avg_kcal) if avg_kcal is not None else "н/д"

    kcal_cell = f"**{avg_kcal_str}**"
    if trend_kcal in ("↑", "↓", "="):
        kcal_cell = f"**{avg_kcal_str}** {trend_kcal}"

    score_line = f"## Оценка недели: {score}/10" if score is not None else "## Оценка недели: /10"

    return f"""{title}

> Контекст:

## 🎯 Фокус этой недели

1.

---

## Метрики

| Привычка | План | Факт |
|----------|------|------|
{chr(10).join(metric_rows) if metric_rows else "|  |  |  |"}

---

## ✅ Что получилось хорошо?

{_bullet_section(what_worked)}

## ❌ Что не получилось? Почему?

{_bullet_section(what_failed)}

## 📈 Прогресс

-

## 💡 Инсайты

-

---

{score_line}

---

## ➡️ Фокус на следующую неделю

{_focus_section(focus_next_week)}

---

## 📊 Аналитика

### Ключевые цифры

| Метрика | Факт |
|---------|------|
| Ср. ккал/день | {kcal_cell} |
"""
