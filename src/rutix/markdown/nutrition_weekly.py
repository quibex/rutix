"""Generate nutrition/2026-Wxx.md from per-day MealItem lists.

Pure aggregation — Сводка table + per-day full meal tables. Editorial
"Наблюдения" section is omitted (Phase 3 might add it via Claude).
"""

from dataclasses import dataclass
from datetime import date

from rutix.markdown.daily import MealItem
from rutix.markdown.weekly import russian_date_range

RU_WEEKDAYS_SHORT = {0: "ПН", 1: "ВТ", 2: "СР", 3: "ЧТ", 4: "ПТ", 5: "СБ", 6: "ВС"}


@dataclass
class NutritionDay:
    date: date
    meals: list[MealItem]


def _day_label(d: date) -> str:
    return f"{RU_WEEKDAYS_SHORT[d.weekday()]} {d.day:02d}"


def _totals(meals: list[MealItem]) -> tuple[int, float, float, float]:
    return (
        sum(m.kcal for m in meals),
        round(sum(m.protein for m in meals), 1),
        round(sum(m.fat for m in meals), 1),
        round(sum(m.carbs for m in meals), 1),
    )


def _format_num(v: float) -> str:
    if v == int(v):
        return str(int(v))
    return f"{v:g}"


def render_nutrition_weekly(year: int, week_num: int, days: list[NutritionDay]) -> str:
    if days:
        date_range = russian_date_range(days[0].date, days[-1].date)
        title = f"# КБЖУ — Неделя {week_num} ({date_range})"
    else:
        title = f"# КБЖУ — Неделя {week_num}"

    # --- Сводка table ---
    summary_rows = []
    days_with_data = []
    for d in days:
        kcal, p, f, c = _totals(d.meals)
        summary_rows.append(
            f"| {_day_label(d.date)} | {kcal} |"
            f" {_format_num(p)} | {_format_num(f)} | {_format_num(c)} |"
        )
        if d.meals:
            days_with_data.append((kcal, p, f, c))

    if days_with_data:
        n = len(days_with_data)
        avg_kcal = round(sum(x[0] for x in days_with_data) / n)
        avg_p = round(sum(x[1] for x in days_with_data) / n, 1)
        avg_f = round(sum(x[2] for x in days_with_data) / n, 1)
        avg_c = round(sum(x[3] for x in days_with_data) / n, 1)
        avg_row = (
            f"| **Ср.** | **{avg_kcal}** | **{_format_num(avg_p)}** | "
            f"**{_format_num(avg_f)}** | **{_format_num(avg_c)}** |"
        )
    else:
        avg_row = "| **Ср.** | — | — | — | — |"

    # --- Per-day tables ---
    detail_blocks = []
    for d in days:
        if not d.meals:
            continue
        rows = []
        prev_slot = ""
        for m in d.meals:
            slot_cell = m.slot if m.slot != prev_slot else ""
            if m.slot:
                prev_slot = m.slot
            rows.append(
                f"| {slot_cell} | {m.name} | {m.kcal} | {_format_num(m.protein)} | "
                f"{_format_num(m.fat)} | {_format_num(m.carbs)} |"
            )
        kcal, p, f, c = _totals(d.meals)
        rows.append(
            f"| **Итого** |  | **{kcal}** | **{_format_num(p)}** | "
            f"**{_format_num(f)}** | **{_format_num(c)}** |"
        )
        detail_blocks.append(
            f"### {_day_label(d.date)}\n\n"
            "| Приём | Что | Ккал | Б | Ж | У |\n"
            "|-------|-----|------|---|---|---|\n" + "\n".join(rows)
        )

    return (
        f"{title}\n\n"
        f"## Сводка\n\n"
        f"| День | Ккал | Б | Ж | У |\n"
        f"|------|------|---|---|---|\n"
        + ("\n".join(summary_rows) + "\n" if summary_rows else "")
        + f"{avg_row}\n\n"
        + "## Детали по дням\n\n"
        + ("\n\n".join(detail_blocks) if detail_blocks else "")
        + ("\n" if detail_blocks else "")
    )
