from rutix.markdown.mood_tracker import DayRow, MedColumn, render_row


def test_render_full_row():
    row = DayRow(
        day=14,
        mood=2,
        sleep_hours=7.5,
        anxiety=1,
        irritability=0,
        meds=[MedColumn("Сейзар", True, "25"), MedColumn("Гидр.К", True, "12.5")],
        notes="ок",
    )
    assert render_row(row) == "| 14 | +2 | 7.5 |  | 1 | 0 | ✓ 25 | ✓ 12.5 |  | ок |"


def test_render_negative_mood():
    row = DayRow(day=14, mood=-2)
    assert "| -2 |" in render_row(row)


def test_render_zero_mood_no_sign():
    row = DayRow(day=14, mood=0)
    assert "| 0 |" in render_row(row)


def test_render_med_not_taken_is_empty():
    row = DayRow(day=14, meds=[MedColumn("Сейзар", False, "25")])
    assert "✓" not in render_row(row)


def test_render_integer_sleep_drops_decimal():
    row = DayRow(day=14, sleep_hours=7.0)
    assert "| 7 |" in render_row(row)
    assert "| 7.0 |" not in render_row(row)


def test_render_no_meds_no_extra_pipes():
    row = DayRow(day=14, mood=1)
    s = render_row(row)
    # Exact column count: День | Настр. | Сон | Вес | Тревога | Раздр. | Алк/Нарк | Заметки = 8 cells
    assert s.count("|") == 9  # 8 cells = 9 pipes


def test_render_with_weight():
    row = DayRow(day=14, weight=72.5)
    assert "| 72.5 |" in render_row(row)


def test_render_empty_notes_renders_blank():
    row = DayRow(day=14, mood=0)
    assert render_row(row).endswith("|  |")
