"""Generate weekly/2026-Wxx.md — metrics-filled, editorial-templated.

Bot fills hard numbers (Метрики table, Аналитика). Editorial sections
(Фокус, Что получилось, Что не получилось, Прогресс, Инсайты, Оценка)
are left as empty templates for the user to fill in Obsidian / via Claude.ai.
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


def render_weekly(year: int, week_num: int, days: list[WeeklyDay], habits: HabitsConfig) -> str:
    if days:
        date_range = russian_date_range(days[0].date, days[-1].date)
        title = f"# Неделя {week_num} ({date_range})"
    else:
        title = f"# Неделя {week_num}"

    metric_rows = []
    for h in habits.daily:
        count = _count_habit(days, h)
        metric_rows.append(f"| {h} | 7 | {count} |")
    for h, weekdays in habits.scheduled.items():
        count = _count_habit(days, h)
        metric_rows.append(f"| {h} | {len(weekdays)} | {count} |")

    avg_kcal = _avg_kcal(days)
    avg_kcal_str = str(avg_kcal) if avg_kcal is not None else "н/д"

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

-

## ❌ Что не получилось? Почему?

-

## 📈 Прогресс

-

## 💡 Инсайты

-

---

## Оценка недели: /10

---

## 📊 Аналитика

### Ключевые цифры

| Метрика | Факт |
|---------|------|
| Ср. ккал/день | **{avg_kcal_str}** |
"""
