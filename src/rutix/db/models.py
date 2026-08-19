"""SQLAlchemy 2.x models for Phase 1.

Tables:
- state_entries:   subjective-state snapshots (mood/energy/appetite), many/day (/state)
- mood_entries:    daily report buffer (sleep/vpn/eng/weight), one row per day (/report)
- medication_log:  med-taken flags per (day, med_key)
- meds_active:     active medication protocol (persistent, archived rows kept)
- flush_log:       what's been flushed to git (persistent)
- user_prefs:      single-row runtime prefs — currently the timezone (persistent)
- job_prefs:       per-cron overrides — on/off + time (persistent)

SQLite is a write buffer; flush_day materialises these into the daily .md file.
"""

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, Integer, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class MoodEntry(Base):
    __tablename__ = "mood_entries"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    mood: Mapped[int | None] = mapped_column(Integer, nullable=True)
    anxiety: Mapped[int | None] = mapped_column(Integer, nullable=True)
    irritability: Mapped[int | None] = mapped_column(Integer, nullable=True)
    energy: Mapped[int | None] = mapped_column(Integer, nullable=True)
    appetite: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sleep_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    vpn_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    eng_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
    )


class StateEntry(Base):
    """One subjective-state snapshot. Multiple rows per day — /state can be run
    morning, noon, evening. flush_day renders them as timestamped lines in the
    daily file's `## Самочувствие` section. No averaging."""

    __tablename__ = "state_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    day: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    ts: Mapped[datetime] = mapped_column(DateTime, nullable=False)  # local wall-clock
    mood: Mapped[int | None] = mapped_column(Integer, nullable=True)
    energy: Mapped[int | None] = mapped_column(Integer, nullable=True)
    appetite: Mapped[int | None] = mapped_column(Integer, nullable=True)


class MedicationLog(Base):
    __tablename__ = "medication_log"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    med_key: Mapped[str] = mapped_column(String, primary_key=True)
    taken: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class MedActive(Base):
    __tablename__ = "meds_active"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    column_label: Mapped[str] = mapped_column(String, nullable=False)
    current_dose: Mapped[str] = mapped_column(String, nullable=False)
    started_at: Mapped[date] = mapped_column(Date, nullable=False)
    archived_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    # "HH:MM" local time; NULL = no reminder. The med_reminder_tick cron polls
    # every minute and fires for meds whose reminder_time matches now.
    reminder_time: Mapped[str | None] = mapped_column(String, nullable=True)


class MedSnooze(Base):
    """Deferred med reminder: fire_at is naive local wall-clock (the tick compares
    it against the user's local now), med_keys is comma-separated."""

    __tablename__ = "med_snooze"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fire_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    med_keys: Mapped[str] = mapped_column(String, nullable=False)


class UserPref(Base):
    """Single-row (id=1) runtime preferences. Currently only the timezone.

    The timezone lives in the DB rather than only in the `TZ` env var because
    the user travels: the hourly Todoist sync rewrites it whenever they move,
    and a restart must not snap the whole schedule back to the env default.
    `TZ` stays the *seed* — read once, when this row doesn't exist yet — and
    this row is the last-known-good fallback when Todoist is unreachable at
    startup.
    """

    __tablename__ = "user_prefs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    tz: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
    )


class JobPref(Base):
    """Per-cron override: is the job on, and at what time does it run.

    A row exists only for a job the user actually touched — the code-level
    `JobSpec` registry holds the defaults, so a missing row means "as shipped"
    and a new job starts with its default schedule without a data migration.
    `hour`/`minute` are stored as strings because they're fed straight to
    `CronTrigger`, which also accepts wildcards ("*") and step syntax.
    """

    __tablename__ = "job_prefs"

    job_id: Mapped[str] = mapped_column(String, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # NULL = keep the spec's default for this field.
    hour: Mapped[str | None] = mapped_column(String, nullable=True)
    minute: Mapped[str | None] = mapped_column(String, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
    )


class FlushLog(Base):
    __tablename__ = "flush_log"

    period_id: Mapped[str] = mapped_column(String, primary_key=True)
    flushed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())
    git_sha: Mapped[str | None] = mapped_column(String, nullable=True)
