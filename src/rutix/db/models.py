"""SQLAlchemy 2.x models for Phase 1.

Tables:
- mood_entries:    one row per day, current week only (purged after Sunday flush)
- medication_log:  med-taken flags per (day, med_key), current week only
- meds_active:     active medication protocol (persistent, archived rows kept)
- flush_log:       what's been flushed to git (persistent)
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
    """Deferred med reminder: fire_at is UTC datetime, med_keys is comma-separated."""

    __tablename__ = "med_snooze"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fire_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    med_keys: Mapped[str] = mapped_column(String, nullable=False)


class FlushLog(Base):
    __tablename__ = "flush_log"

    period_id: Mapped[str] = mapped_column(String, primary_key=True)
    flushed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())
    git_sha: Mapped[str | None] = mapped_column(String, nullable=True)
