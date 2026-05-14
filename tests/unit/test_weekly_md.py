from datetime import date

from rutix.markdown.weekly import (
    HabitsConfig,
    WeeklyDay,
    render_weekly,
    russian_date_range,
)


def test_russian_date_range_same_month():
    assert russian_date_range(date(2026, 5, 4), date(2026, 5, 10)) == "4 — 10 мая"


def test_russian_date_range_cross_month():
    assert russian_date_range(date(2026, 4, 27), date(2026, 5, 3)) == "27 апр — 3 мая"


def test_render_weekly_includes_header_and_week_label():
    result = render_weekly(
        year=2026,
        week_num=19,
        days=[],
        habits=HabitsConfig(daily=[], scheduled={}),
    )
    assert result.startswith("# Неделя 19")


def test_render_weekly_metrics_table_counts_habits():
    days = [
        WeeklyDay(
            date=date(2026, 5, 4),
            done_habits={"📚 Anki", "🌅 Skincare AM"},
            sleep_offh=None,
            sleep_onh=None,
            kcal=2200,
        ),
        WeeklyDay(
            date=date(2026, 5, 5),
            done_habits={"📚 Anki"},
            sleep_offh=None,
            sleep_onh=None,
            kcal=3000,
        ),
    ]
    result = render_weekly(
        year=2026, week_num=19, days=days,
        habits=HabitsConfig(
            daily=["📚 Anki", "🌅 Skincare AM"],
            scheduled={"🏋️ Strength": ["ВТ", "ЧТ", "СБ"]},
        ),
    )
    assert "| 📚 Anki" in result
    assert "| 2 |" in result  # Anki counted twice
    assert "| 🌅 Skincare AM" in result
    assert "| 1 |" in result  # Skincare AM only once
    assert "| 🏋️ Strength" in result


def test_render_weekly_includes_empty_editorial_templates():
    result = render_weekly(
        year=2026, week_num=19, days=[],
        habits=HabitsConfig(daily=[], scheduled={}),
    )
    for h in ("## 🎯 Фокус этой недели", "## ✅ Что получилось хорошо?",
              "## ❌ Что не получилось? Почему?", "## 📈 Прогресс",
              "## 💡 Инсайты", "## Оценка недели"):
        assert h in result


def test_render_weekly_avg_kcal_when_data_present():
    days = [
        WeeklyDay(date=date(2026, 5, 4), done_habits=set(), sleep_offh=None, sleep_onh=None, kcal=2000),
        WeeklyDay(date=date(2026, 5, 5), done_habits=set(), sleep_offh=None, sleep_onh=None, kcal=3000),
    ]
    result = render_weekly(
        year=2026, week_num=19, days=days,
        habits=HabitsConfig(daily=[], scheduled={}),
    )
    # Avg of 2 days with kcal data = (2000 + 3000) / 2 = 2500
    assert "Ср. ккал/день | **2500**" in result or "Ср. ккал/день | 2500" in result
