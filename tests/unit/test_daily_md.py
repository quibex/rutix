import pytest

from rutix.markdown.daily import (
    MealItem,
    WellbeingData,
    WellbeingMed,
    append_done,
    append_meal,
    append_note,
    has_section,
    parse_day_plan,
    parse_done,
    parse_habit_labels,
    parse_meals,
    parse_notes,
    render_wellbeing_section,
    update_habits_checked,
    update_time_section,
    upsert_section,
)

SAMPLE = """# Четверг, 14 мая

[[2026-W20|← Неделя 20]]

## План на день

- one
- two

---

## Сон

- Отбой:
- Подъём:

---

## Время (ч)

- VPN:
- Английский:

## Привычки

- [ ] 📚 Anki
- [ ] 🌅 Skincare AM
- [x] 🌙 Skincare PM
- [ ] 🥤 Протеин

---

## Питание

| Приём | Что | Ккал | Б | Ж | У |
|-------|-----|------|---|---|---|
|  |  |  |  |  |  |
| **Итого** |  |  |  |  |  |

---

## Что сделано

- existing done line

## Заметки

- existing note line
"""


# --- append_note ---


def test_append_note_adds_bullet_under_section():
    result = append_note(SAMPLE, "новая заметка")
    notes_block = result.split("## Заметки", 1)[1]
    assert "- existing note line" in notes_block
    assert "- новая заметка" in notes_block


def test_append_note_when_section_only_has_empty_dash():
    md = SAMPLE.replace("- existing note line", "- ")
    result = append_note(md, "первая")
    notes_block = result.split("## Заметки", 1)[1]
    # Empty dash is replaced/preserved as we keep section tidy
    assert "- первая" in notes_block


# --- append_done ---


def test_append_done_adds_bullet_under_section():
    result = append_done(SAMPLE, "сделал X")
    done_block = result.split("## Что сделано", 1)[1].split("## Заметки", 1)[0]
    assert "- existing done line" in done_block
    assert "- сделал X" in done_block


def test_append_done_is_idempotent_for_existing_bullet():
    """update_habits is retried by the 06:00/08:00 catch-up crons; without
    dedup, each retry duplicates every bullet in `## Что сделано`."""
    once = append_done(SAMPLE, "сделал X")
    twice = append_done(once, "сделал X")
    assert once == twice


def test_append_done_is_idempotent_for_preexisting_bullet():
    once = append_done(SAMPLE, "existing done line")
    assert once == SAMPLE


def test_append_note_is_idempotent_for_existing_bullet():
    once = append_note(SAMPLE, "новая заметка")
    twice = append_note(once, "новая заметка")
    assert once == twice


# --- update_habits_checked ---


def test_update_habits_checked_marks_matching_habits():
    result = update_habits_checked(SAMPLE, done={"📚 Anki", "🥤 Протеин"})
    habits_block = result.split("## Привычки", 1)[1].split("\n---\n", 1)[0]
    assert "- [x] 📚 Anki" in habits_block
    assert "- [x] 🥤 Протеин" in habits_block
    assert "- [ ] 🌅 Skincare AM" in habits_block  # untouched


def test_update_habits_preserves_already_checked():
    result = update_habits_checked(SAMPLE, done={"🌙 Skincare PM"})
    habits_block = result.split("## Привычки", 1)[1].split("\n---\n", 1)[0]
    assert "- [x] 🌙 Skincare PM" in habits_block


def test_update_habits_no_change_when_done_set_empty():
    assert update_habits_checked(SAMPLE, done=set()) == SAMPLE


# --- parse_habit_labels ---


def test_parse_habit_labels_returns_labels_in_order():
    assert parse_habit_labels(SAMPLE) == [
        "📚 Anki",
        "🌅 Skincare AM",
        "🌙 Skincare PM",
        "🥤 Протеин",
    ]


def test_parse_habit_labels_includes_checked_and_unchecked():
    # SAMPLE has both [ ] and [x] — already covered by the order test above,
    # but assert explicitly: a label marked [x] is still returned.
    assert "🌙 Skincare PM" in parse_habit_labels(SAMPLE)


def test_parse_habit_labels_empty_section_returns_empty():
    md = SAMPLE.replace(
        "- [ ] 📚 Anki\n- [ ] 🌅 Skincare AM\n- [x] 🌙 Skincare PM\n- [ ] 🥤 Протеин",
        "",
    )
    assert parse_habit_labels(md) == []


def test_parse_habit_labels_raises_when_section_missing():
    with pytest.raises(ValueError, match="Привычки"):
        parse_habit_labels("# header\n\n## Заметки\n- x\n")


# --- append_meal + parse_meals + totals ---


def test_append_meal_writes_row_and_recomputes_totals():
    item = MealItem(slot="Обед", name="Шаурма", kcal=450, protein=22.0, fat=18.0, carbs=45.0)
    result = append_meal(SAMPLE, item)
    food = result.split("## Питание", 1)[1].split("\n---\n", 1)[0]
    assert "| Обед | Шаурма | 450 | 22 | 18 | 45 |" in food
    # Totals row updated
    assert "| **Итого** |  | **450** | **22** | **18** | **45** |" in food


def test_append_meal_to_non_empty_table_sums_totals():
    pre = SAMPLE.replace(
        "|  |  |  |  |  |  |\n| **Итого** |  |  |  |  |  |",
        "| Завтрак | Яйца | 200 | 14 | 14 | 2 |\n| **Итого** |  | **200** | **14** | **14** | **2** |",
    )
    item = MealItem(slot="Обед", name="Бургер", kcal=500, protein=20.0, fat=25.0, carbs=40.0)
    result = append_meal(pre, item)
    food = result.split("## Питание", 1)[1].split("\n---\n", 1)[0]
    assert "| Завтрак | Яйца | 200 | 14 | 14 | 2 |" in food
    assert "| Обед | Бургер | 500 | 20 | 25 | 40 |" in food
    assert "| **Итого** |  | **700** | **34** | **39** | **42** |" in food


def test_append_meal_omits_slot_label_if_same_as_previous_row():
    pre = SAMPLE.replace(
        "|  |  |  |  |  |  |\n| **Итого** |  |  |  |  |  |",
        "| Обед | Плов | 400 | 17 | 12 | 56 |\n| **Итого** |  | **400** | **17** | **12** | **56** |",
    )
    item = MealItem(slot="Обед", name="Чиабатта", kcal=300, protein=10.0, fat=15.0, carbs=30.0)
    result = append_meal(pre, item)
    food = result.split("## Питание", 1)[1].split("\n---\n", 1)[0]
    # Second Обед row should have empty slot column to mirror existing convention
    assert "|  | Чиабатта | 300 | 10 | 15 | 30 |" in food


def test_parse_meals_returns_all_items():
    pre = SAMPLE.replace(
        "|  |  |  |  |  |  |\n| **Итого** |  |  |  |  |  |",
        (
            "| Завтрак | Яйца | 200 | 14 | 14 | 2 |\n"
            "|  | Хлеб | 100 | 3 | 1 | 18 |\n"
            "| Обед | Плов | 400 | 17 | 12 | 56 |\n"
            "| **Итого** |  | **700** | **34** | **27** | **76** |"
        ),
    )
    items = parse_meals(pre)
    assert len(items) == 3
    assert items[0] == MealItem("Завтрак", "Яйца", 200, 14.0, 14.0, 2.0)
    assert items[1] == MealItem("Завтрак", "Хлеб", 100, 3.0, 1.0, 18.0)  # carries slot
    assert items[2] == MealItem("Обед", "Плов", 400, 17.0, 12.0, 56.0)


def test_parse_meals_empty_returns_empty_list():
    assert parse_meals(SAMPLE) == []


def test_append_meal_raises_if_no_food_section():
    with pytest.raises(ValueError, match="Питание section not found"):
        append_meal("# header only\n\n## Заметки\n", MealItem("Обед", "x", 1, 1, 1, 1))


# --- parse_notes / parse_done ---


def test_parse_notes_returns_non_empty_bullets():
    assert parse_notes(SAMPLE) == ["existing note line"]


def test_parse_done_returns_non_empty_bullets():
    assert parse_done(SAMPLE) == ["existing done line"]


def test_parse_notes_skips_placeholder_dash():
    md = "# x\n\n## Заметки\n\n-\n"
    assert parse_notes(md) == []


def test_parse_notes_returns_empty_when_section_missing():
    assert parse_notes("# x\n\n## Питание\n") == []


def test_parse_notes_preserves_order_and_multiple_bullets():
    md = "# x\n\n## Заметки\n\n- первая мысль\n- вторая мысль\n- третья\n"
    assert parse_notes(md) == ["первая мысль", "вторая мысль", "третья"]


# --- upsert_section / has_section ---


def test_upsert_section_replaces_existing_body():
    md = "## A\n\nold\n\n## B\n\nb-body\n"
    result = upsert_section(md, "A", "\nnew\n")
    assert "## A\n\nnew\n" in result
    assert "## B\n\nb-body" in result


def test_upsert_section_appends_when_missing():
    md = "# header\n\n## A\n\na-body\n"
    result = upsert_section(md, "Самочувствие", "\n- x\n")
    assert "## Самочувствие" in result
    assert "## A" in result
    # original A is intact
    assert "a-body" in result


def test_has_section_returns_true_only_when_present():
    md = "## Питание\n\n|...|\n"
    assert has_section(md, "Питание")
    assert not has_section(md, "Самочувствие")


# --- render_wellbeing_section ---


def test_render_wellbeing_weekday_no_weight():
    body = render_wellbeing_section(
        WellbeingData(
            mood=1,
            anxiety=0,
            irritability=0,
            sleep_hours=8.0,
            include_weight=False,
            meds=[
                WellbeingMed("Сейзар", taken=True, dose="25"),
                WellbeingMed("Гидр.К", taken=True, dose="12.5"),
            ],
        )
    )
    assert "- Настроение: +1" in body
    assert "- Тревога: 0" in body
    assert "- Раздражительность: 0" in body
    assert "- Сон (ч): 8" in body
    assert "- Сейзар: ✓ 25" in body
    assert "- Гидр.К: ✓ 12.5" in body
    assert "Вес:" not in body


def test_render_wellbeing_saturday_includes_weight():
    body = render_wellbeing_section(WellbeingData(weight=57.0, include_weight=True))
    assert "- Вес: 57" in body


def test_render_wellbeing_unknown_values_render_as_dash():
    body = render_wellbeing_section(WellbeingData())
    assert "- Настроение: —" in body
    assert "- Тревога: —" in body
    assert "- Сон (ч): —" in body


def test_render_wellbeing_med_not_taken_shows_cross():
    body = render_wellbeing_section(
        WellbeingData(meds=[WellbeingMed("Сейзар", taken=False, dose="25")])
    )
    assert "- Сейзар: ✗" in body
    assert "✓" not in body


def test_render_wellbeing_negative_mood():
    body = render_wellbeing_section(WellbeingData(mood=-2))
    assert "- Настроение: -2" in body


def test_render_wellbeing_zero_mood_no_sign():
    body = render_wellbeing_section(WellbeingData(mood=0))
    assert "- Настроение: 0" in body


# --- update_time_section ---

_TIME_SAMPLE = """## Сон

- Отбой:

## Время (ч)

- VPN:
- Английский:

## Привычки

- [ ] x
"""


def test_update_time_section_fills_hours():
    result = update_time_section(_TIME_SAMPLE, vpn_hours=2.0, eng_hours=1.0)
    block = result.split("## Время (ч)", 1)[1].split("## Привычки", 1)[0]
    assert "- VPN: 2" in block
    assert "- Английский: 1" in block


def test_update_time_section_renders_half_hours():
    result = update_time_section(_TIME_SAMPLE, vpn_hours=0.5, eng_hours=1.5)
    block = result.split("## Время (ч)", 1)[1].split("## Привычки", 1)[0]
    assert "- VPN: 0.5" in block
    assert "- Английский: 1.5" in block


def test_update_time_section_none_leaves_blank():
    result = update_time_section(_TIME_SAMPLE, vpn_hours=None, eng_hours=2.0)
    block = result.split("## Время (ч)", 1)[1].split("## Привычки", 1)[0]
    assert "- VPN:" in block and "VPN: 0" not in block
    assert "- Английский: 2" in block


def test_update_time_section_overwrites_existing_value():
    pre = _TIME_SAMPLE.replace("- VPN:", "- VPN: 9")
    result = update_time_section(pre, vpn_hours=1.0, eng_hours=None)
    block = result.split("## Время (ч)", 1)[1].split("## Привычки", 1)[0]
    assert "- VPN: 1" in block
    assert "- VPN: 9" not in block


def test_update_time_section_raises_when_missing():
    md = "## Сон\n\n- Отбой:\n"
    with pytest.raises(ValueError, match="Время"):
        update_time_section(md, vpn_hours=1.0, eng_hours=1.0)


# --- parse_day_plan ---


def test_parse_day_plan_returns_bullets_in_order():
    md = "## 🗓 План на день\n\n- утром бегать\n- встреча 14:00\n- вечером чтение\n\n## Сон\n"
    assert parse_day_plan(md) == ["утром бегать", "встреча 14:00", "вечером чтение"]


def test_parse_day_plan_skips_empty_placeholder():
    md = "## 🗓 План на день\n\n-\n\n## Сон\n"
    assert parse_day_plan(md) == []


def test_parse_day_plan_missing_section_returns_empty():
    assert parse_day_plan("## Сон\n") == []
