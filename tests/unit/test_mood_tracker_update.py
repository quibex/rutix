import pytest

from rutix.markdown.mood_tracker import update_day_row


SAMPLE = """# Таблица настроения

## Май 2026

| День | Настр. | Сон (ч) | Вес | Тревога | Раздр. | Сейзар | Гидр.К | Алк/Нарк | Заметки |
|------|--------|---------|-----|---------|--------|--------|--------|----------|---------|
| 1    | +2     | 7       |     |         |        | ✓ 25   | ✓      |          |         |
| 13   |        |         |     |         |        |        |        |          |         |
| 14   |        |         |     |         |        |        |        |          |         |

## Апрель 2026

| День | Настр. |
|------|--------|
| 14   | +1     |
"""


def test_update_existing_day_in_target_section():
    new_row = "| 13 | +1 | 7.5 |  | 0 | 0 | ✓ 25 | ✓ 12.5 |  | test |"
    result = update_day_row(SAMPLE, 2026, 5, 13, new_row)

    assert "| 13 | +1 | 7.5" in result
    # April section untouched
    assert "## Апрель 2026" in result
    assert "| 14   | +1     |" in result


def test_update_does_not_match_other_section():
    # May day 14 should be updated; April day 14 must stay as is
    new_row = "| 14 | -1 | 6 |  |  |  |  |  |  | may |"
    result = update_day_row(SAMPLE, 2026, 5, 14, new_row)

    assert "| 14   | +1     |" in result  # April preserved
    assert "| 14 | -1 | 6 " in result      # May updated


def test_update_missing_section_raises():
    with pytest.raises(ValueError, match="Section not found"):
        update_day_row(SAMPLE, 2026, 6, 14, "anything")


def test_update_missing_day_raises():
    with pytest.raises(ValueError, match="Day row 99"):
        update_day_row(SAMPLE, 2026, 5, 99, "anything")


def test_update_idempotent_when_same_content():
    # Replacing with the exact same row should produce identical text
    new_row = "| 1    | +2     | 7       |     |         |        | ✓ 25   | ✓      |          |         |"
    result = update_day_row(SAMPLE, 2026, 5, 1, new_row)
    assert result == SAMPLE
