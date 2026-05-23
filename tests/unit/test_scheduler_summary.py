from datetime import date

from rutix.jobs.flush_week import FlushWeekResult
from rutix.jobs.scheduler import build_3am_summary, build_retry_summary
from rutix.jobs.update_habits import UpdateHabitsResult

THURSDAY = date(2026, 5, 14)
WEDNESDAY = date(2026, 5, 13)
MONDAY = date(2026, 5, 18)
SUNDAY = date(2026, 5, 17)


def test_summary_happy_path_weekday():
    summary = build_3am_summary(
        today=THURSDAY,
        target=WEDNESDAY,
        flush_day_outcome="abcdef1234567",
        update_habits_outcome=UpdateHabitsResult(
            sha="1234567abcdef", marked=["📚 Anki", "🌅 Skincare AM"]
        ),
        flush_week_outcome=None,
    )
    assert "🌅 3am job: 2026-05-14" in summary
    assert "✅ flush_day за 2026-05-13: записал (abcdef1)" in summary
    assert "✅ update_habits за 2026-05-13: отметил 2 привычки (1234567)" in summary
    assert "   • 📚 Anki" in summary
    assert "   • 🌅 Skincare AM" in summary
    assert "⏭ flush_week: не понедельник, пропущено" in summary


def test_summary_flush_day_skipped():
    summary = build_3am_summary(
        today=THURSDAY,
        target=WEDNESDAY,
        flush_day_outcome=None,
        update_habits_outcome=UpdateHabitsResult(sha=None, marked=[], skip_reason="no_completions"),
        flush_week_outcome=None,
    )
    assert "⏭ flush_day за 2026-05-13: пропущено" in summary
    assert "⏭ update_habits за 2026-05-13: Todoist не вернул завершённых задач" in summary


def test_summary_update_habits_no_daily_file():
    summary = build_3am_summary(
        today=THURSDAY,
        target=WEDNESDAY,
        flush_day_outcome="abc",
        update_habits_outcome=UpdateHabitsResult(sha=None, marked=[], skip_reason="no_daily_file"),
        flush_week_outcome=None,
    )
    assert "⏭ update_habits за 2026-05-13: нет daily-файла в репо" in summary


def test_summary_update_habits_no_op():
    summary = build_3am_summary(
        today=THURSDAY,
        target=WEDNESDAY,
        flush_day_outcome="abc",
        update_habits_outcome=UpdateHabitsResult(sha=None, marked=[], skip_reason="no_op"),
        flush_week_outcome=None,
    )
    assert "⏭ update_habits за 2026-05-13: нечего менять (всё уже отмечено)" in summary


def test_summary_flush_day_error_includes_exception():
    summary = build_3am_summary(
        today=THURSDAY,
        target=WEDNESDAY,
        flush_day_outcome=RuntimeError("github 404"),
        update_habits_outcome=UpdateHabitsResult(sha=None, marked=[]),
        flush_week_outcome=None,
    )
    assert "⚠️ flush_day за 2026-05-13: ошибка — RuntimeError: github 404" in summary


def test_summary_update_habits_error_includes_exception():
    summary = build_3am_summary(
        today=THURSDAY,
        target=WEDNESDAY,
        flush_day_outcome="abc1234",
        update_habits_outcome=ValueError("todoist down"),
        flush_week_outcome=None,
    )
    assert "⚠️ update_habits за 2026-05-13: ошибка — ValueError: todoist down" in summary


def test_summary_monday_includes_week_id_when_flushed():
    summary = build_3am_summary(
        today=MONDAY,
        target=SUNDAY,
        flush_day_outcome="abc1234567",
        update_habits_outcome=UpdateHabitsResult(sha=None, marked=[]),
        flush_week_outcome=FlushWeekResult(sha="def9876543", user_message=""),
    )
    assert (
        "✅ flush_week 2026-W20: weekly+nutrition+thoughts+next-week записаны (def9876)" in summary
    )


def test_summary_monday_flush_week_already_done():
    summary = build_3am_summary(
        today=MONDAY,
        target=SUNDAY,
        flush_day_outcome=None,
        update_habits_outcome=UpdateHabitsResult(sha=None, marked=[]),
        flush_week_outcome=None,
    )
    assert "⏭ flush_week 2026-W20: уже записано" in summary


def test_summary_truncates_long_habit_list():
    many = [f"привычка {i}" for i in range(20)]
    summary = build_3am_summary(
        today=THURSDAY,
        target=WEDNESDAY,
        flush_day_outcome="abc1234",
        update_habits_outcome=UpdateHabitsResult(sha="def4567", marked=many),
        flush_week_outcome=None,
    )
    assert "отметил 20 привычек" in summary
    assert "   • привычка 0" in summary
    assert "   • привычка 14" in summary
    assert "   • привычка 15" not in summary
    assert "… и ещё 5" in summary


def test_summary_habit_pluralization_singular():
    summary = build_3am_summary(
        today=THURSDAY,
        target=WEDNESDAY,
        flush_day_outcome="x",
        update_habits_outcome=UpdateHabitsResult(sha="y", marked=["один"]),
        flush_week_outcome=None,
    )
    assert "отметил 1 привычку" in summary


def test_summary_habit_pluralization_five():
    summary = build_3am_summary(
        today=THURSDAY,
        target=WEDNESDAY,
        flush_day_outcome="x",
        update_habits_outcome=UpdateHabitsResult(sha="y", marked=["a", "b", "c", "d", "e"]),
        flush_week_outcome=None,
    )
    assert "отметил 5 привычек" in summary


# build_retry_summary — for the 06:00 / 08:00 catch-up cron


def test_retry_summary_marked_habits_always_notifies():
    """Recovery from a failed 3am run should always notify the user."""
    msg = build_retry_summary(
        target=WEDNESDAY,
        result=UpdateHabitsResult(sha="abc1234", marked=["📚 Anki"]),
        is_final_attempt=False,
    )
    assert msg is not None
    assert "🔁" in msg  # retry icon
    assert "2026-05-13" in msg
    assert "отметил 1 привычку" in msg
    assert "📚 Anki" in msg


def test_retry_summary_appended_done_only_notifies():
    """Even if no habits matched, new bullets in `## Что сделано` count as a change."""
    msg = build_retry_summary(
        target=WEDNESDAY,
        result=UpdateHabitsResult(
            sha="abc1234", marked=[], appended_done=["купить хлеб"]
        ),
        is_final_attempt=False,
    )
    assert msg is not None
    assert "купить хлеб" in msg


def test_retry_summary_no_op_silent_when_not_final():
    """3am already succeeded → catch-up should not spam user with `no_op`."""
    msg = build_retry_summary(
        target=WEDNESDAY,
        result=UpdateHabitsResult(sha=None, marked=[], skip_reason="no_op"),
        is_final_attempt=False,
    )
    assert msg is None


def test_retry_summary_no_completions_silent_when_not_final():
    msg = build_retry_summary(
        target=WEDNESDAY,
        result=UpdateHabitsResult(sha=None, marked=[], skip_reason="no_completions"),
        is_final_attempt=False,
    )
    assert msg is None


def test_retry_summary_exception_silent_on_intermediate_attempt():
    """Intermediate failures stay silent — we still have another attempt left."""
    msg = build_retry_summary(
        target=WEDNESDAY,
        result=RuntimeError("503"),
        is_final_attempt=False,
    )
    assert msg is None


def test_retry_summary_exception_notifies_on_final_attempt():
    """Final attempt failed — user must know habits weren't recovered."""
    msg = build_retry_summary(
        target=WEDNESDAY,
        result=RuntimeError("Todoist still down"),
        is_final_attempt=True,
    )
    assert msg is not None
    assert "⚠️" in msg
    assert "RuntimeError" in msg
    assert "Todoist still down" in msg
    assert "2026-05-13" in msg


def test_retry_summary_no_op_silent_on_final_attempt():
    """Even on the final attempt, no_op means 3am already covered it — stay quiet."""
    msg = build_retry_summary(
        target=WEDNESDAY,
        result=UpdateHabitsResult(sha=None, marked=[], skip_reason="no_op"),
        is_final_attempt=True,
    )
    assert msg is None
