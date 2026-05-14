from datetime import date

from rutix.markdown.daily import MealItem
from rutix.markdown.nutrition_weekly import (
    NutritionDay,
    render_nutrition_weekly,
)


def test_render_with_one_day():
    days = [
        NutritionDay(
            date=date(2026, 5, 4),
            meals=[
                MealItem("Завтрак", "Яйца", 200, 14.0, 14.0, 2.0),
                MealItem("Обед", "Плов", 400, 17.0, 12.0, 56.0),
            ],
        )
    ]
    result = render_nutrition_weekly(year=2026, week_num=19, days=days)

    assert result.startswith("# КБЖУ — Неделя 19")
    # Сводка row for the day
    assert "| ПН 04 | 600 |" in result
    # Avg row (1 day, so equal to the day)
    assert "| **Ср.** | **600** |" in result
    # Per-day section header
    assert "### ПН 04" in result
    # Per-day table data + Итого
    assert "| Завтрак | Яйца | 200 | 14 | 14 | 2 |" in result
    assert "| **Итого** |  | **600** |" in result


def test_render_empty_day_shows_zeros_in_summary_only():
    days = [
        NutritionDay(date=date(2026, 5, 4), meals=[]),
        NutritionDay(
            date=date(2026, 5, 5),
            meals=[
                MealItem("Обед", "Плов", 400, 17.0, 12.0, 56.0),
            ],
        ),
    ]
    result = render_nutrition_weekly(year=2026, week_num=19, days=days)
    # Empty day shows zeros in the table
    assert "| ПН 04 | 0 | 0 | 0 | 0 |" in result
    # Empty day per-day section is omitted (no point in empty table)
    assert "### ПН 04" not in result
    assert "### ВТ 05" in result


def test_render_avg_skips_zero_days():
    """Average should be over days with data (>= 1 meal), not all 7."""
    days = [
        NutritionDay(date=date(2026, 5, 4), meals=[]),  # 0 kcal
        NutritionDay(date=date(2026, 5, 5), meals=[MealItem("Обед", "x", 1000, 0, 0, 0)]),
        NutritionDay(date=date(2026, 5, 6), meals=[MealItem("Обед", "y", 2000, 0, 0, 0)]),
    ]
    result = render_nutrition_weekly(year=2026, week_num=19, days=days)
    # Avg = (1000 + 2000) / 2 = 1500
    assert "| **Ср.** | **1500** |" in result
