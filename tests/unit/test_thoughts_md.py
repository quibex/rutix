from datetime import date

from rutix.markdown.thoughts import ThoughtsDay, render_thoughts_weekly


def test_render_thoughts_includes_header_and_week_label():
    out = render_thoughts_weekly(year=2026, week_num=19, days=[])
    assert out.startswith("# Мысли — Неделя 19")
    assert "не было заметок" in out


def test_render_thoughts_groups_notes_by_day():
    days = [
        ThoughtsDay(date=date(2026, 5, 4), notes=["сильно устал", "получилось досмотреть курс"]),
        ThoughtsDay(date=date(2026, 5, 5), notes=[]),
        ThoughtsDay(date=date(2026, 5, 6), notes=["разговор с N"]),
    ]
    out = render_thoughts_weekly(year=2026, week_num=19, days=days)

    assert "## 4 мая" in out
    assert "## 6 мая" in out
    # Empty day must not produce its own section header
    assert "## 5 мая" not in out
    assert "- сильно устал" in out
    assert "- получилось досмотреть курс" in out
    assert "- разговор с N" in out


def test_render_thoughts_empty_when_no_notes_anywhere():
    days = [
        ThoughtsDay(date=date(2026, 5, 4), notes=[]),
        ThoughtsDay(date=date(2026, 5, 5), notes=[]),
    ]
    out = render_thoughts_weekly(year=2026, week_num=19, days=days)
    assert "не было заметок" in out
    assert "## 4 мая" not in out
