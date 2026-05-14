# Phase 1: Mental Tracker Core — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a single-user Telegram bot that records mood/sleep/medications via `/track`, persists to SQLite during the day, and at 03:00 every day flushes yesterday's row into `health/mood_tracker.md` in a separate private GitHub repo.

**Architecture:** aiogram 3 long-polling app, SQLAlchemy 2 + aiosqlite for DB, Alembic for migrations, APScheduler in-process for the 03:00 cron, httpx for GitHub Contents API. Strict TDD: pure-function logic (markdown rendering, time helpers) is unit-tested first; the bot handler and scheduler get a single integration smoke test each.

**Tech Stack:** Python 3.12, aiogram ≥3.25, SQLAlchemy ≥2.0.36 + aiosqlite, Alembic ≥1.14, APScheduler ≥3.11, httpx ≥0.28, pydantic-settings ≥2.7, ruff, pytest + pytest-asyncio + respx + freezegun.

**Spec:** `quibex/life:projects/mood-bot.md` (Status, Motive, Architecture, SQLite schema, Commands, Cron 03:00 — all defined there).

**Repo:** https://github.com/quibex/rutix (already created with skeleton).

**Reference patterns:** `/Users/elabdi/Desktop/kurut/kurut-pie/` — same stack, look at `pyproject.toml`, `Dockerfile`, `.github/workflows/ci.yml` for proven patterns. **Do not copy code blindly** — kurut-pie is a payments bot with totally different domain.

---

## Out of scope for Phase 1

These belong to Phase 2 / Phase 3 — do NOT add them in this plan even if they look easy:
- `/eat`, `/note`, `/done`, `/today`, `/week`, `/meds` commands
- Claude API integration (no `anthropic` SDK in deps yet)
- Todoist API integration (no `todoist` calls)
- Sunday weekly flush (`weekly/`, `nutrition/` files)
- Habits ✓ in daily files
- Production deploy (`prod.yml`, GHCR push, SSH deploy)

Phase 1 ends with: `docker compose up` locally → `/track` works → 03:00 next morning a row appears in `quibex/life:health/mood_tracker.md`.

---

## File map

**Created:**
```
rutix/
├── .github/workflows/
│   └── ci.yml                      — ruff + pytest + alembic upgrade
├── alembic.ini
├── alembic/
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│       └── 0001_initial.py         — 4 tables
├── docs/
│   └── plans/
│       └── 2026-05-14-phase-1-mental-tracker-core.md  (this file)
├── docker-compose.yml              — local dev
├── Dockerfile                      — multi-stage
├── src/rutix/
│   ├── __init__.py
│   ├── __main__.py                 — entry point: wire bot+scheduler+db
│   ├── settings.py                 — pydantic-settings
│   ├── time_utils.py               — subjective_today, week_id, is_saturday
│   ├── bot/
│   │   ├── __init__.py
│   │   ├── app.py                  — build Bot + Dispatcher
│   │   ├── auth.py                 — whitelist middleware
│   │   └── handlers/
│   │       ├── __init__.py
│   │       ├── track.py            — /track FSM (mood→anx→irr→sleep→meds→[weight])
│   │       └── sync.py             — /sync command
│   ├── db/
│   │   ├── __init__.py
│   │   ├── engine.py               — async engine + session factory
│   │   └── models.py               — MoodEntry, MedicationLog, MedActive, FlushLog
│   ├── integrations/
│   │   ├── __init__.py
│   │   └── github.py               — Contents API client
│   ├── markdown/
│   │   ├── __init__.py
│   │   └── mood_tracker.py         — render_row + update_day_row
│   └── jobs/
│       ├── __init__.py
│       ├── scheduler.py            — APScheduler bootstrap
│       └── flush_day.py            — daily flush logic
└── tests/
    ├── __init__.py
    ├── conftest.py                 — async session fixture
    └── unit/
        ├── __init__.py
        ├── test_settings.py
        ├── test_time_utils.py
        ├── test_models.py
        ├── test_github_client.py
        ├── test_mood_tracker_render.py
        ├── test_mood_tracker_update.py
        ├── test_flush_day.py
        └── test_auth_middleware.py
```

**Modified:**
- `pyproject.toml` — add deps + dev-deps
- `.gitignore` — add alembic-related ignores

**Untouched** (already exist from skeleton): `README.md`, `.env.example`.

---

## Task ordering rationale

Pure functions and isolated I/O clients first (1–7) — fully unit-testable, no env required. Then the orchestration layer (8: flush_day) using mocked deps. Bot scaffold (9), the heavy `/track` FSM (10), the trivial `/sync` (11), then APScheduler (12), final wiring (13). Docker + CI come last (14, 15) so that the green-tests state of the repo is verified continuously as we go.

---

## Task 1: Bootstrap — deps, package layout, dev env

**Files:**
- Modify: `pyproject.toml`
- Modify: `.gitignore`
- Create: `src/rutix/__init__.py`
- Create: `src/rutix/__main__.py`
- Create: `tests/__init__.py`
- Create: `tests/unit/__init__.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: Replace `pyproject.toml`**

```toml
[project]
name = "rutix"
version = "0.1.0"
description = "Personal Telegram bot — mental state & nutrition tracker"
requires-python = ">=3.12"
dependencies = [
    "aiogram>=3.25.0",
    "sqlalchemy>=2.0.36",
    "aiosqlite>=0.20.0",
    "alembic>=1.14.0",
    "httpx>=0.28.1",
    "pydantic>=2.10.5",
    "pydantic-settings>=2.7.1",
    "apscheduler>=3.11.0",
    "python-json-logger>=3.0.0",
]

[project.optional-dependencies]
dev = [
    "ruff>=0.9.1",
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "respx>=0.21.0",
    "freezegun>=1.5.0",
    "greenlet>=3.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/rutix"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I"]

[tool.ruff.format]
quote-style = "double"
indent-style = "space"

[tool.pytest.ini_options]
asyncio_mode = "auto"
pythonpath = ["src"]
```

- [ ] **Step 2: Append to `.gitignore`**

Append these lines (after the existing entries):

```
# Alembic temp
alembic/versions/__pycache__/
```

- [ ] **Step 3: Create package files**

`src/rutix/__init__.py`:
```python
__version__ = "0.1.0"
```

`src/rutix/__main__.py`:
```python
"""Entry point — fully wired in Task 13."""


def main() -> None:
    raise NotImplementedError("Filled in Task 13")


if __name__ == "__main__":
    main()
```

`tests/__init__.py`: (empty file)

`tests/unit/__init__.py`: (empty file)

`tests/conftest.py`:
```python
"""Shared pytest fixtures."""
```

- [ ] **Step 4: Install deps**

Run: `cd /Users/elabdi/Desktop/rutix && pip install -e ".[dev]"`
Expected: Successfully installs aiogram, sqlalchemy, alembic, etc.

(If you prefer `uv`: `uv pip install -e ".[dev]"` after `uv venv && source .venv/bin/activate`.)

- [ ] **Step 5: Verify ruff and pytest run cleanly**

Run: `ruff check src/`
Expected: no output (success)

Run: `ruff format --check src/`
Expected: no output (success)

Run: `pytest tests/`
Expected: `no tests ran in 0.0Xs`

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml .gitignore src/rutix/ tests/
git commit -m "feat: bootstrap deps and package layout"
```

---

## Task 2: Settings (pydantic-settings)

**Files:**
- Create: `src/rutix/settings.py`
- Create: `tests/unit/test_settings.py`

- [ ] **Step 1: Write failing tests**

`tests/unit/test_settings.py`:
```python
import pytest
from pydantic import ValidationError

from rutix.settings import Settings


def test_settings_loads_required_fields_from_env(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_USER_ID", "12345")
    monkeypatch.setenv("GITHUB_API_TOKEN", "ghp_test")

    s = Settings(_env_file=None)

    assert s.bot_token == "test-token"
    assert s.telegram_user_id == 12345
    assert s.github_api_token == "ghp_test"


def test_settings_defaults(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_USER_ID", "1")
    monkeypatch.setenv("GITHUB_API_TOKEN", "x")

    s = Settings(_env_file=None)

    assert s.life_repo == "quibex/life"
    assert s.tz == "Europe/Moscow"
    assert s.database_url == "sqlite+aiosqlite:///data/bot.db"


def test_settings_missing_required_raises(monkeypatch):
    for var in ["BOT_TOKEN", "TELEGRAM_USER_ID", "GITHUB_API_TOKEN"]:
        monkeypatch.delenv(var, raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `pytest tests/unit/test_settings.py -v`
Expected: ImportError — `rutix.settings` doesn't exist.

- [ ] **Step 3: Implement `settings.py`**

`src/rutix/settings.py`:
```python
"""Configuration via env vars (validated by pydantic-settings)."""
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    bot_token: str = Field(...)
    telegram_user_id: int = Field(...)
    github_api_token: str = Field(...)

    life_repo: str = Field(default="quibex/life")
    database_url: str = Field(default="sqlite+aiosqlite:///data/bot.db")
    tz: str = Field(default="Europe/Moscow")

    # Reserved for Phase 2 — declared so tests don't fail when present in .env
    anthropic_api_key: str = Field(default="")
    todoist_token: str = Field(default="")


def load_settings() -> Settings:
    return Settings()
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `pytest tests/unit/test_settings.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/rutix/settings.py tests/unit/test_settings.py
git commit -m "feat(settings): pydantic-settings env config with whitelist user_id"
```

---

## Task 3: Time utilities

**Files:**
- Create: `src/rutix/time_utils.py`
- Create: `tests/unit/test_time_utils.py`

- [ ] **Step 1: Write failing tests**

`tests/unit/test_time_utils.py`:
```python
from datetime import date, datetime
from zoneinfo import ZoneInfo

from rutix.time_utils import (
    days_of_week,
    is_saturday,
    is_sunday,
    subjective_today,
    week_id,
    yesterday_of,
)

MSK = ZoneInfo("Europe/Moscow")


def test_subjective_today_after_5am_returns_calendar_day():
    now = datetime(2026, 5, 14, 10, 0, tzinfo=MSK)
    assert subjective_today(now) == date(2026, 5, 14)


def test_subjective_today_before_5am_returns_yesterday():
    now = datetime(2026, 5, 14, 4, 30, tzinfo=MSK)
    assert subjective_today(now) == date(2026, 5, 13)


def test_subjective_today_at_exactly_5am_returns_today():
    now = datetime(2026, 5, 14, 5, 0, tzinfo=MSK)
    assert subjective_today(now) == date(2026, 5, 14)


def test_subjective_today_handles_utc_input():
    # 02:00 UTC == 05:00 MSK
    now = datetime(2026, 5, 14, 2, 0, tzinfo=ZoneInfo("UTC"))
    assert subjective_today(now) == date(2026, 5, 14)


def test_yesterday_of():
    assert yesterday_of(date(2026, 5, 14)) == date(2026, 5, 13)
    # Cross-month
    assert yesterday_of(date(2026, 6, 1)) == date(2026, 5, 31)


def test_is_saturday():
    assert is_saturday(date(2026, 5, 16))
    assert not is_saturday(date(2026, 5, 14))


def test_is_sunday():
    assert is_sunday(date(2026, 5, 17))
    assert not is_sunday(date(2026, 5, 14))


def test_week_id_iso_format():
    assert week_id(date(2026, 5, 14)) == "2026-W20"
    # First week of January edge case
    assert week_id(date(2026, 1, 5)) == "2026-W02"


def test_days_of_week_returns_mon_to_sun():
    days = days_of_week(date(2026, 5, 14))  # Thursday
    assert days[0] == date(2026, 5, 11)
    assert days[6] == date(2026, 5, 17)
    assert len(days) == 7


def test_days_of_week_when_sunday_input():
    days = days_of_week(date(2026, 5, 17))  # Sunday
    assert days[0] == date(2026, 5, 11)
    assert days[6] == date(2026, 5, 17)
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `pytest tests/unit/test_time_utils.py -v`
Expected: ImportError — `rutix.time_utils` doesn't exist.

- [ ] **Step 3: Implement `time_utils.py`**

`src/rutix/time_utils.py`:
```python
"""Time helpers — subjective day, week ids, weekday checks."""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

EARLY_MORNING_BOUNDARY = time(5, 0)


def subjective_today(now: datetime, tz: str = "Europe/Moscow") -> date:
    """User's perceived 'today'.

    If local time is before 05:00, returns yesterday — the user hasn't slept yet.
    """
    local = now.astimezone(ZoneInfo(tz))
    if local.time() < EARLY_MORNING_BOUNDARY:
        return (local - timedelta(days=1)).date()
    return local.date()


def yesterday_of(d: date) -> date:
    return d - timedelta(days=1)


def is_saturday(d: date) -> bool:
    return d.weekday() == 5


def is_sunday(d: date) -> bool:
    return d.weekday() == 6


def week_id(d: date) -> str:
    """ISO week id like '2026-W19' (zero-padded week number)."""
    iso = d.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def days_of_week(any_day_of_week: date) -> list[date]:
    """Mon..Sun for the ISO week containing the given date."""
    monday = any_day_of_week - timedelta(days=any_day_of_week.weekday())
    return [monday + timedelta(days=i) for i in range(7)]
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `pytest tests/unit/test_time_utils.py -v`
Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add src/rutix/time_utils.py tests/unit/test_time_utils.py
git commit -m "feat(time): subjective_today + week helpers"
```

---

## Task 4: SQLAlchemy models + async engine

**Files:**
- Create: `src/rutix/db/__init__.py`
- Create: `src/rutix/db/models.py`
- Create: `src/rutix/db/engine.py`
- Create: `tests/unit/test_models.py`

- [ ] **Step 1: Create empty `db/__init__.py`**

(empty file)

- [ ] **Step 2: Implement models**

`src/rutix/db/models.py`:
```python
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
    sleep_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    weight: Mapped[float | None] = mapped_column(Float, nullable=True)
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


class FlushLog(Base):
    __tablename__ = "flush_log"

    period_id: Mapped[str] = mapped_column(String, primary_key=True)
    flushed_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp()
    )
    git_sha: Mapped[str | None] = mapped_column(String, nullable=True)
```

- [ ] **Step 3: Implement engine factory**

`src/rutix/db/engine.py`:
```python
"""Async SQLAlchemy engine + session factory."""
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def make_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(database_url, echo=False, future=True)


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
```

- [ ] **Step 4: Add async session fixture to conftest**

Replace `tests/conftest.py` content:
```python
"""Shared pytest fixtures."""
import pytest_asyncio

from rutix.db.engine import make_engine, make_session_factory
from rutix.db.models import Base


@pytest_asyncio.fixture
async def session():
    """In-memory SQLite session with all tables created."""
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = make_session_factory(engine)
    async with Session() as s:
        yield s
    await engine.dispose()
```

- [ ] **Step 5: Write model tests**

`tests/unit/test_models.py`:
```python
from datetime import date

from rutix.db.models import MedActive, MedicationLog, MoodEntry


async def test_mood_entry_persists_and_loads(session):
    entry = MoodEntry(
        day=date(2026, 5, 14),
        mood=2,
        anxiety=1,
        irritability=0,
        sleep_hours=7.5,
    )
    session.add(entry)
    await session.commit()

    loaded = await session.get(MoodEntry, date(2026, 5, 14))
    assert loaded is not None
    assert loaded.mood == 2
    assert loaded.sleep_hours == 7.5
    assert loaded.weight is None


async def test_medication_log_composite_key(session):
    session.add(MedicationLog(day=date(2026, 5, 14), med_key="seizar", taken=True))
    session.add(MedicationLog(day=date(2026, 5, 14), med_key="gidr_kanon", taken=False))
    await session.commit()

    loaded = await session.get(MedicationLog, (date(2026, 5, 14), "seizar"))
    assert loaded.taken is True


async def test_med_active_archived_at_nullable(session):
    session.add(MedActive(
        key="seizar", name="Сейзар", column_label="Сейзар",
        current_dose="25", started_at=date(2026, 4, 26),
    ))
    await session.commit()

    loaded = await session.get(MedActive, "seizar")
    assert loaded.archived_at is None
```

- [ ] **Step 6: Run tests, verify they pass**

Run: `pytest tests/unit/test_models.py -v`
Expected: 3 passed.

- [ ] **Step 7: Commit**

```bash
git add src/rutix/db/ tests/conftest.py tests/unit/test_models.py
git commit -m "feat(db): SQLAlchemy 2 models + async engine factory"
```

---

## Task 5: Alembic init + first migration

**Files:**
- Create: `alembic.ini`
- Create: `alembic/env.py`
- Create: `alembic/script.py.mako`
- Create: `alembic/versions/0001_initial.py`
- Create: `tests/unit/test_alembic.py`
- Create: `data/.gitkeep` (so the dir exists for SQLite)

**Note:** Alembic uses a *sync* SQLAlchemy URL even though the app uses async at runtime. Alembic only runs at startup (or `alembic upgrade head` in CI), so a separate sync engine is the standard pattern and avoids async-driver complications.

- [ ] **Step 1: Create `alembic.ini`**

```ini
[alembic]
script_location = alembic
sqlalchemy.url = sqlite:///data/bot.db
prepend_sys_path = src

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console
qualname =

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
```

- [ ] **Step 2: Create `alembic/env.py`**

```python
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from rutix.db.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 3: Create `alembic/script.py.mako`**

```mako
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

"""
from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

# revision identifiers, used by Alembic.
revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

- [ ] **Step 4: Create initial migration `alembic/versions/0001_initial.py`**

```python
"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-05-14
"""
from alembic import op
import sqlalchemy as sa


revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mood_entries",
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("mood", sa.Integer(), nullable=True),
        sa.Column("anxiety", sa.Integer(), nullable=True),
        sa.Column("irritability", sa.Integer(), nullable=True),
        sa.Column("sleep_hours", sa.Float(), nullable=True),
        sa.Column("weight", sa.Float(), nullable=True),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.func.current_timestamp(),
            nullable=False,
        ),
    )
    op.create_table(
        "medication_log",
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("med_key", sa.String(), primary_key=True),
        sa.Column("taken", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    )
    op.create_table(
        "meds_active",
        sa.Column("key", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("column_label", sa.String(), nullable=False),
        sa.Column("current_dose", sa.String(), nullable=False),
        sa.Column("started_at", sa.Date(), nullable=False),
        sa.Column("archived_at", sa.Date(), nullable=True),
    )
    op.create_table(
        "flush_log",
        sa.Column("period_id", sa.String(), primary_key=True),
        sa.Column(
            "flushed_at",
            sa.DateTime(),
            server_default=sa.func.current_timestamp(),
            nullable=False,
        ),
        sa.Column("git_sha", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("flush_log")
    op.drop_table("meds_active")
    op.drop_table("medication_log")
    op.drop_table("mood_entries")
```

- [ ] **Step 5: Add `data/.gitkeep`**

Create empty file `data/.gitkeep` so the dir exists for the SQLite file in dev.

- [ ] **Step 6: Verify alembic upgrade works**

Run: `cd /Users/elabdi/Desktop/rutix && alembic upgrade head`
Expected: `INFO  [alembic.runtime.migration] Running upgrade  -> 0001, initial schema`. Creates `data/bot.db`.

Verify with: `sqlite3 data/bot.db ".tables"`
Expected: `flush_log  medication_log meds_active   mood_entries`

Then clean up: `rm data/bot.db`

- [ ] **Step 7: Add alembic head-count test**

`tests/unit/test_alembic.py`:
```python
"""Defends against accidental dual-head migrations after merges."""
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_alembic_heads_count_is_one():
    result = subprocess.run(
        ["alembic", "heads"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stderr
    heads = [line for line in result.stdout.strip().splitlines() if line.strip()]
    assert len(heads) == 1, f"Expected exactly 1 alembic head, got {len(heads)}: {heads}"
```

- [ ] **Step 8: Run test, verify it passes**

Run: `pytest tests/unit/test_alembic.py -v`
Expected: 1 passed.

- [ ] **Step 9: Commit**

```bash
git add alembic.ini alembic/ data/.gitkeep tests/unit/test_alembic.py
git commit -m "feat(db): alembic init + 0001 initial schema migration"
```

---

## Task 6: GitHub Contents API client

**Files:**
- Create: `src/rutix/integrations/__init__.py`
- Create: `src/rutix/integrations/github.py`
- Create: `tests/unit/test_github_client.py`

- [ ] **Step 1: Create empty `integrations/__init__.py`**

(empty file)

- [ ] **Step 2: Write failing tests**

`tests/unit/test_github_client.py`:
```python
import base64
import json

import httpx
import pytest
import respx

from rutix.integrations.github import FileContent, GitHubClient


@pytest.fixture
def client():
    c = GitHubClient(token="ghp_test", repo="quibex/life")
    yield c


@respx.mock
async def test_read_returns_decoded_text_and_sha(client):
    content = "hello world\n"
    respx.get("https://api.github.com/repos/quibex/life/contents/test.md").mock(
        return_value=httpx.Response(200, json={
            "content": base64.b64encode(content.encode()).decode(),
            "sha": "abc123",
            "encoding": "base64",
        })
    )
    result = await client.read("test.md")
    assert result == FileContent(text="hello world\n", sha="abc123")
    await client.aclose()


@respx.mock
async def test_read_returns_none_when_404(client):
    respx.get("https://api.github.com/repos/quibex/life/contents/missing.md").mock(
        return_value=httpx.Response(404)
    )
    result = await client.read("missing.md")
    assert result is None
    await client.aclose()


@respx.mock
async def test_write_create_file_no_sha(client):
    route = respx.put("https://api.github.com/repos/quibex/life/contents/new.md").mock(
        return_value=httpx.Response(201, json={"commit": {"sha": "newsha"}})
    )
    result = await client.write("new.md", "content", "create new.md")
    assert result == "newsha"
    body = json.loads(route.calls[0].request.content)
    assert "sha" not in body
    assert body["message"] == "create new.md"
    assert base64.b64decode(body["content"]).decode() == "content"
    await client.aclose()


@respx.mock
async def test_write_update_file_with_sha(client):
    route = respx.put("https://api.github.com/repos/quibex/life/contents/existing.md").mock(
        return_value=httpx.Response(200, json={"commit": {"sha": "updated"}})
    )
    result = await client.write("existing.md", "new content", "update", sha="oldsha")
    assert result == "updated"
    body = json.loads(route.calls[0].request.content)
    assert body["sha"] == "oldsha"
    await client.aclose()


@respx.mock
async def test_read_raises_on_5xx(client):
    respx.get("https://api.github.com/repos/quibex/life/contents/x.md").mock(
        return_value=httpx.Response(500)
    )
    with pytest.raises(httpx.HTTPStatusError):
        await client.read("x.md")
    await client.aclose()
```

- [ ] **Step 3: Run tests, verify they fail**

Run: `pytest tests/unit/test_github_client.py -v`
Expected: ImportError — `rutix.integrations.github` missing.

- [ ] **Step 4: Implement `github.py`**

`src/rutix/integrations/github.py`:
```python
"""GitHub Contents API client.

Atomic per-file read/write: GET → modify → PUT with SHA.
SHA-mismatch (concurrent edit) → caller decides whether to retry.
"""
import base64
from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class FileContent:
    """Decoded text + sha needed to update the file."""
    text: str
    sha: str


class GitHubClient:
    BASE_URL = "https://api.github.com"

    def __init__(self, token: str, repo: str, http: httpx.AsyncClient | None = None):
        self.repo = repo
        self.http = http or httpx.AsyncClient(
            base_url=self.BASE_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=15.0,
        )

    async def read(self, path: str) -> FileContent | None:
        """Return None if file doesn't exist (404). Raises on other errors."""
        r = await self.http.get(f"/repos/{self.repo}/contents/{path}")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        data = r.json()
        text = base64.b64decode(data["content"]).decode("utf-8")
        return FileContent(text=text, sha=data["sha"])

    async def write(
        self, path: str, text: str, message: str, sha: str | None = None
    ) -> str:
        """Create or update the file. Returns the new commit SHA."""
        body: dict = {
            "message": message,
            "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
        }
        if sha:
            body["sha"] = sha
        r = await self.http.put(f"/repos/{self.repo}/contents/{path}", json=body)
        r.raise_for_status()
        return r.json()["commit"]["sha"]

    async def aclose(self) -> None:
        await self.http.aclose()
```

- [ ] **Step 5: Run tests, verify they pass**

Run: `pytest tests/unit/test_github_client.py -v`
Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add src/rutix/integrations/ tests/unit/test_github_client.py
git commit -m "feat(github): Contents API client with read/write+SHA"
```

---

## Task 7: Mood tracker markdown — render row + update day row

**Files:**
- Create: `src/rutix/markdown/__init__.py`
- Create: `src/rutix/markdown/mood_tracker.py`
- Create: `tests/unit/test_mood_tracker_render.py`
- Create: `tests/unit/test_mood_tracker_update.py`

This is the only domain-specific code in Phase 1. It must produce rows that match the existing format in `quibex/life:health/mood_tracker.md` exactly:

```
| День | Настр. | Сон (ч) | Вес | Тревога | Раздр. | Сейзар | Гидр.К | Алк/Нарк | Заметки |
| 14   | +2     | 7.5     |     | 1       | 0      | ✓ 25   | ✓ 12.5 |          | заметка |
```

(`Алк/Нарк` column is always empty — we removed alcohol/drugs tracking, but the column stays for backwards-compat with existing months.)

- [ ] **Step 1: Create empty `markdown/__init__.py`**

(empty file)

- [ ] **Step 2: Write render tests**

`tests/unit/test_mood_tracker_render.py`:
```python
from rutix.markdown.mood_tracker import DayRow, MedColumn, render_row


def test_render_full_row():
    row = DayRow(
        day=14, mood=2, sleep_hours=7.5, anxiety=1, irritability=0,
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
```

- [ ] **Step 3: Write update tests**

`tests/unit/test_mood_tracker_update.py`:
```python
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
```

- [ ] **Step 4: Run tests, verify they fail**

Run: `pytest tests/unit/test_mood_tracker_render.py tests/unit/test_mood_tracker_update.py -v`
Expected: ImportError.

- [ ] **Step 5: Implement `mood_tracker.py`**

`src/rutix/markdown/mood_tracker.py`:
```python
"""Surgical edits to health/mood_tracker.md.

Schema (matches the existing format in quibex/life:health/mood_tracker.md):
| День | Настр. | Сон (ч) | Вес | Тревога | Раздр. | <med1> | ... | Алк/Нарк | Заметки |

`Алк/Нарк` column is always rendered empty — kept for back-compat with existing rows.
"""
import re
from dataclasses import dataclass, field

RU_MONTHS = {
    1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
    5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
    9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь",
}


@dataclass
class MedColumn:
    column_label: str    # "Сейзар" — used by header generator (Phase 2+); not by render_row
    taken: bool
    dose: str             # "25" or "12.5"


@dataclass
class DayRow:
    day: int
    mood: int | None = None
    sleep_hours: float | None = None
    weight: float | None = None
    anxiety: int | None = None
    irritability: int | None = None
    meds: list[MedColumn] = field(default_factory=list)
    notes: str = ""


def render_row(row: DayRow) -> str:
    """Render a single table row in the canonical format."""
    cells: list[str] = [
        str(row.day),
        _signed(row.mood),
        _float_cell(row.sleep_hours),
        _float_cell(row.weight),
        _int_cell(row.anxiety),
        _int_cell(row.irritability),
    ]
    cells.extend(_med_cell(m) for m in row.meds)
    cells.append("")            # Алк/Нарк always empty
    cells.append(row.notes)
    return "| " + " | ".join(cells) + " |"


def _int_cell(v: int | None) -> str:
    return "" if v is None else str(v)


def _signed(v: int | None) -> str:
    if v is None:
        return ""
    if v > 0:
        return f"+{v}"
    return str(v)


def _float_cell(v: float | None) -> str:
    if v is None:
        return ""
    if v == int(v):
        return str(int(v))
    return str(v)


def _med_cell(m: MedColumn) -> str:
    return f"✓ {m.dose}" if m.taken else ""


def update_day_row(markdown: str, year: int, month: int, day: int, new_row: str) -> str:
    """Replace the row for given day in the month section.

    Raises ValueError if the section header or the day row is not found.
    """
    section_header = f"## {RU_MONTHS[month]} {year}"
    section_idx = markdown.find(section_header)
    if section_idx == -1:
        raise ValueError(f"Section not found: {section_header}")

    after_header = markdown[section_idx + len(section_header):]
    next_section = re.search(r"\n## ", after_header)
    section_end = (
        section_idx + len(section_header) + next_section.start()
        if next_section
        else len(markdown)
    )
    section_text = markdown[section_idx:section_end]

    pattern = re.compile(rf"^\|\s*{day}\s*\|.*$", re.MULTILINE)
    if not pattern.search(section_text):
        raise ValueError(f"Day row {day} not found in section {section_header}")
    new_section = pattern.sub(new_row, section_text, count=1)
    return markdown[:section_idx] + new_section + markdown[section_end:]
```

- [ ] **Step 6: Run tests, verify they pass**

Run: `pytest tests/unit/test_mood_tracker_render.py tests/unit/test_mood_tracker_update.py -v`
Expected: 13 passed.

- [ ] **Step 7: Commit**

```bash
git add src/rutix/markdown/ tests/unit/test_mood_tracker_render.py tests/unit/test_mood_tracker_update.py
git commit -m "feat(markdown): mood_tracker row render + section/day-aware update"
```

---

## Task 8: Daily flush job

**Files:**
- Create: `src/rutix/jobs/__init__.py`
- Create: `src/rutix/jobs/flush_day.py`
- Create: `tests/unit/test_flush_day.py`

- [ ] **Step 1: Create empty `jobs/__init__.py`**

(empty file)

- [ ] **Step 2: Write failing tests**

`tests/unit/test_flush_day.py`:
```python
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from rutix.db.models import FlushLog, MedActive, MedicationLog, MoodEntry
from rutix.integrations.github import FileContent
from rutix.jobs.flush_day import MOOD_TRACKER_PATH, flush_day


SAMPLE_TRACKER = """# Таблица настроения

## Май 2026

| День | Настр. | Сон (ч) | Вес | Тревога | Раздр. | Сейзар | Гидр.К | Алк/Нарк | Заметки |
|------|--------|---------|-----|---------|--------|--------|--------|----------|---------|
| 13   |        |         |     |         |        |        |        |          |         |
"""


@pytest.fixture
def fake_github():
    g = MagicMock()
    g.read = AsyncMock(return_value=FileContent(text=SAMPLE_TRACKER, sha="oldsha"))
    g.write = AsyncMock(return_value="newsha")
    return g


async def test_flush_day_writes_row_and_marks_log(session, fake_github):
    session.add(MedActive(
        key="seizar", name="Сейзар", column_label="Сейзар",
        current_dose="25", started_at=date(2026, 5, 1),
    ))
    session.add(MedActive(
        key="gidr_kanon", name="Гидр.Канон", column_label="Гидр.К",
        current_dose="12.5", started_at=date(2026, 5, 1),
    ))
    session.add(MoodEntry(
        day=date(2026, 5, 13), mood=1, anxiety=0, irritability=0, sleep_hours=7.5,
    ))
    session.add(MedicationLog(day=date(2026, 5, 13), med_key="seizar", taken=True))
    session.add(MedicationLog(day=date(2026, 5, 13), med_key="gidr_kanon", taken=True))
    await session.commit()

    sha = await flush_day(session, fake_github, date(2026, 5, 13))
    assert sha == "newsha"

    fake_github.read.assert_awaited_once_with(MOOD_TRACKER_PATH)
    fake_github.write.assert_awaited_once()
    call_kwargs = fake_github.write.call_args.kwargs
    written_text = (
        fake_github.write.call_args.args[1] if len(fake_github.write.call_args.args) > 1
        else call_kwargs["text"]
    )
    assert "| 13 | +1 |" in written_text
    assert "✓ 25" in written_text
    assert "✓ 12.5" in written_text

    log = await session.get(FlushLog, "day:2026-05-13")
    assert log is not None
    assert log.git_sha == "newsha"


async def test_flush_day_skips_if_already_flushed(session, fake_github):
    session.add(FlushLog(period_id="day:2026-05-13", git_sha="oldsha"))
    session.add(MoodEntry(day=date(2026, 5, 13), mood=1))
    await session.commit()

    sha = await flush_day(session, fake_github, date(2026, 5, 13))
    assert sha is None
    fake_github.write.assert_not_called()


async def test_flush_day_skips_if_no_mood_entry(session, fake_github):
    sha = await flush_day(session, fake_github, date(2026, 5, 13))
    assert sha is None
    fake_github.write.assert_not_called()


async def test_flush_day_no_op_if_content_unchanged(session, fake_github):
    """If the rendered row equals what's already in the file, skip the PUT."""
    # Pre-populate the markdown with the exact same row we're about to render
    rendered = "| 13 | +1 | 7 |  | 0 | 0 |  |  |  | quiet |"
    pre_filled = SAMPLE_TRACKER.replace(
        "| 13   |        |         |     |         |        |        |        |          |         |",
        rendered,
    )
    fake_github.read = AsyncMock(return_value=FileContent(text=pre_filled, sha="x"))

    session.add(MoodEntry(
        day=date(2026, 5, 13), mood=1, anxiety=0, irritability=0,
        sleep_hours=7.0, notes="quiet",
    ))
    await session.commit()

    sha = await flush_day(session, fake_github, date(2026, 5, 13))
    assert sha is None
    fake_github.write.assert_not_called()
```

- [ ] **Step 3: Run tests, verify they fail**

Run: `pytest tests/unit/test_flush_day.py -v`
Expected: ImportError — `rutix.jobs.flush_day` missing.

- [ ] **Step 4: Implement `flush_day.py`**

`src/rutix/jobs/flush_day.py`:
```python
"""Daily flush: SQLite mood/meds for a given day → row in mood_tracker.md."""
import logging
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rutix.db.models import FlushLog, MedActive, MedicationLog, MoodEntry
from rutix.integrations.github import GitHubClient
from rutix.markdown.mood_tracker import DayRow, MedColumn, render_row, update_day_row

logger = logging.getLogger(__name__)

MOOD_TRACKER_PATH = "health/mood_tracker.md"


async def flush_day(
    session: AsyncSession,
    github: GitHubClient,
    day: date,
) -> str | None:
    """Flush a single day's data to mood_tracker.md.

    Returns the new commit SHA on success, or None when:
    - already flushed (idempotent re-run),
    - no MoodEntry for that day (nothing to write),
    - content already matches (no-op).
    """
    period_id = f"day:{day.isoformat()}"

    if await session.get(FlushLog, period_id):
        logger.info("flush_day skipped — %s already flushed", period_id)
        return None

    mood = await session.get(MoodEntry, day)
    if mood is None:
        logger.info("flush_day skipped — no MoodEntry for %s", day)
        return None

    meds_active = (
        await session.scalars(
            select(MedActive)
            .where(MedActive.archived_at.is_(None))
            .order_by(MedActive.started_at)
        )
    ).all()
    log_rows = (
        await session.scalars(
            select(MedicationLog).where(MedicationLog.day == day)
        )
    ).all()
    taken_by_key = {r.med_key: r.taken for r in log_rows}

    row = DayRow(
        day=day.day,
        mood=mood.mood,
        sleep_hours=mood.sleep_hours,
        weight=mood.weight,
        anxiety=mood.anxiety,
        irritability=mood.irritability,
        notes=mood.notes or "",
        meds=[
            MedColumn(
                column_label=m.column_label,
                taken=taken_by_key.get(m.key, False),
                dose=m.current_dose,
            )
            for m in meds_active
        ],
    )

    file = await github.read(MOOD_TRACKER_PATH)
    if file is None:
        raise RuntimeError(f"{MOOD_TRACKER_PATH} not found in repo")

    new_text = update_day_row(
        file.text, day.year, day.month, day.day, render_row(row)
    )
    if new_text == file.text:
        logger.info("flush_day no-op — content unchanged for %s", day)
        return None

    sha = await github.write(
        MOOD_TRACKER_PATH,
        new_text,
        f"mood({day.isoformat()}): авто-запись из rutix-bot",
        sha=file.sha,
    )

    session.add(FlushLog(period_id=period_id, git_sha=sha))
    await session.commit()
    logger.info("flush_day committed %s as %s", day, sha)
    return sha
```

- [ ] **Step 5: Run tests, verify they pass**

Run: `pytest tests/unit/test_flush_day.py -v`
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add src/rutix/jobs/__init__.py src/rutix/jobs/flush_day.py tests/unit/test_flush_day.py
git commit -m "feat(jobs): daily flush — SQLite → mood_tracker.md row"
```

---

## Task 9: Bot scaffold + auth middleware

**Files:**
- Create: `src/rutix/bot/__init__.py`
- Create: `src/rutix/bot/auth.py`
- Create: `tests/unit/test_auth_middleware.py`

- [ ] **Step 1: Create empty `bot/__init__.py`**

(empty file)

- [ ] **Step 2: Write failing tests**

`tests/unit/test_auth_middleware.py`:
```python
from unittest.mock import AsyncMock, MagicMock

from aiogram.types import User

from rutix.bot.auth import WhitelistMiddleware


async def test_middleware_allows_whitelisted_user():
    mw = WhitelistMiddleware(allowed_user_id=42)
    handler = AsyncMock(return_value="ok")
    user = User(id=42, is_bot=False, first_name="Test")
    data = {"event_from_user": user}

    result = await mw(handler, MagicMock(), data)

    assert result == "ok"
    handler.assert_awaited_once()


async def test_middleware_blocks_other_user():
    mw = WhitelistMiddleware(allowed_user_id=42)
    handler = AsyncMock(return_value="ok")
    user = User(id=999, is_bot=False, first_name="Stranger")
    data = {"event_from_user": user}

    result = await mw(handler, MagicMock(), data)

    assert result is None
    handler.assert_not_awaited()


async def test_middleware_blocks_when_no_user_in_data():
    """Defensive: if event_from_user is missing entirely, block (zero trust)."""
    mw = WhitelistMiddleware(allowed_user_id=42)
    handler = AsyncMock(return_value="ok")

    result = await mw(handler, MagicMock(), {})

    assert result is None
    handler.assert_not_awaited()
```

- [ ] **Step 3: Run tests, verify they fail**

Run: `pytest tests/unit/test_auth_middleware.py -v`
Expected: ImportError.

- [ ] **Step 4: Implement `auth.py`**

`src/rutix/bot/auth.py`:
```python
"""Whitelist middleware — silently drop everyone except the configured user."""
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject


class WhitelistMiddleware(BaseMiddleware):
    def __init__(self, allowed_user_id: int):
        self.allowed_user_id = allowed_user_id

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is None or user.id != self.allowed_user_id:
            return None
        return await handler(event, data)
```

- [ ] **Step 5: Run tests, verify they pass**

Run: `pytest tests/unit/test_auth_middleware.py -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add src/rutix/bot/__init__.py src/rutix/bot/auth.py tests/unit/test_auth_middleware.py
git commit -m "feat(bot): whitelist middleware blocks non-allowed user_id"
```

---

## Task 10: /track FSM handler

**Files:**
- Create: `src/rutix/bot/handlers/__init__.py`
- Create: `src/rutix/bot/handlers/track.py`

This is the largest single handler — a multi-step FSM that walks the user through mood → anxiety → irritability → sleep → meds (one button per active med) → optional weight on Saturday → save.

**Important architectural choices:**

- **Aiogram DI** — `session_factory` is injected via `dp["session_factory"]` (set in Task 13). Handler signatures take it as a kwarg.
- **`subjective_today`** — at handler entry we record the day in FSM data; subsequent steps re-use it (so a `/track` started at 23:59 on day X stays day X even if it finishes after midnight).
- **Editing one message** — every step `edit_text` on the original message instead of sending new ones (cleaner UX, less spam).
- **Med order** — fetched once at the sleep step, processed sequentially via FSM data.
- **Weight on Saturday only** — checked via `is_saturday(day)`.

- [ ] **Step 1: Create empty `handlers/__init__.py`**

(empty file)

- [ ] **Step 2: Implement `track.py`**

`src/rutix/bot/handlers/track.py`:
```python
"""/track — multi-step mood entry via inline buttons."""
import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rutix.db.models import MedActive, MedicationLog, MoodEntry
from rutix.settings import Settings
from rutix.time_utils import is_saturday, subjective_today

logger = logging.getLogger(__name__)

router = Router(name="track")


class TrackStates(StatesGroup):
    mood = State()
    anxiety = State()
    irritability = State()
    sleep = State()
    meds = State()
    weight = State()


def _kb_grid(values: list[tuple[str, str]], cols: int) -> InlineKeyboardMarkup:
    rows = [values[i:i + cols] for i in range(0, len(values), cols)]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=cb) for label, cb in row]
            for row in rows
        ]
    )


def _mood_keyboard() -> InlineKeyboardMarkup:
    return _kb_grid(
        [
            ("-3", "mood:-3"), ("-2", "mood:-2"), ("-1", "mood:-1"), ("0", "mood:0"),
            ("+1", "mood:1"), ("+2", "mood:2"), ("+3", "mood:3"),
        ],
        cols=4,
    )


def _0_to_3(prefix: str) -> InlineKeyboardMarkup:
    return _kb_grid([(str(i), f"{prefix}:{i}") for i in range(4)], cols=4)


def _sleep_keyboard() -> InlineKeyboardMarkup:
    return _kb_grid(
        [(h, f"sleep:{h}") for h in ("6.5", "7", "7.5", "8", "8.5", "9")],
        cols=3,
    )


def _med_keyboard(key: str) -> InlineKeyboardMarkup:
    return _kb_grid([("✓ Да", f"med:{key}:1"), ("✗ Нет", f"med:{key}:0")], cols=2)


def _weight_skip_keyboard() -> InlineKeyboardMarkup:
    return _kb_grid([("Пропустить", "weight:skip")], cols=1)


@router.message(Command("track"))
async def cmd_track(message: Message, state: FSMContext, settings: Settings):
    today = subjective_today(datetime.now(ZoneInfo(settings.tz)), settings.tz)
    await state.update_data(day=today.isoformat(), meds_taken=[], meds_pending=[])
    await state.set_state(TrackStates.mood)
    await message.answer(
        f"📊 Трек за {today.isoformat()}\n\nНастроение?",
        reply_markup=_mood_keyboard(),
    )


@router.callback_query(TrackStates.mood, F.data.startswith("mood:"))
async def cb_mood(cb: CallbackQuery, state: FSMContext):
    value = int(cb.data.split(":", 1)[1])
    await state.update_data(mood=value)
    await state.set_state(TrackStates.anxiety)
    await cb.message.edit_text(
        f"Настроение: {value:+d}\n\nТревога?",
        reply_markup=_0_to_3("anx"),
    )
    await cb.answer()


@router.callback_query(TrackStates.anxiety, F.data.startswith("anx:"))
async def cb_anxiety(cb: CallbackQuery, state: FSMContext):
    value = int(cb.data.split(":", 1)[1])
    await state.update_data(anxiety=value)
    await state.set_state(TrackStates.irritability)
    await cb.message.edit_text(
        f"Тревога: {value}\n\nРаздражительность?",
        reply_markup=_0_to_3("irr"),
    )
    await cb.answer()


@router.callback_query(TrackStates.irritability, F.data.startswith("irr:"))
async def cb_irritability(cb: CallbackQuery, state: FSMContext):
    value = int(cb.data.split(":", 1)[1])
    await state.update_data(irritability=value)
    await state.set_state(TrackStates.sleep)
    await cb.message.edit_text(
        f"Раздр.: {value}\n\nСон (часы)?",
        reply_markup=_sleep_keyboard(),
    )
    await cb.answer()


@router.callback_query(TrackStates.sleep, F.data.startswith("sleep:"))
async def cb_sleep(
    cb: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
):
    value = float(cb.data.split(":", 1)[1])
    await state.update_data(sleep_hours=value)
    await state.set_state(TrackStates.meds)

    async with session_factory() as session:
        meds = (await session.scalars(
            select(MedActive)
            .where(MedActive.archived_at.is_(None))
            .order_by(MedActive.started_at)
        )).all()
    await state.update_data(meds_pending=[m.key for m in meds], meds_taken=[])

    if meds:
        await _ask_next_med(cb.message, state, session_factory)
    else:
        await _maybe_ask_weight_or_save(cb.message, state, session_factory)
    await cb.answer()


async def _ask_next_med(message: Message, state: FSMContext, session_factory):
    data = await state.get_data()
    pending = list(data.get("meds_pending", []))
    if not pending:
        return await _maybe_ask_weight_or_save(message, state, session_factory)
    next_key = pending[0]
    async with session_factory() as session:
        med = await session.get(MedActive, next_key)
    if med is None:
        await state.update_data(meds_pending=pending[1:])
        return await _ask_next_med(message, state, session_factory)
    await message.edit_text(
        f"{med.name} ({med.current_dose}) — принял?",
        reply_markup=_med_keyboard(next_key),
    )


@router.callback_query(TrackStates.meds, F.data.startswith("med:"))
async def cb_med(
    cb: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
):
    _, key, taken_str = cb.data.split(":", 2)
    taken = bool(int(taken_str))

    data = await state.get_data()
    taken_list = list(data.get("meds_taken", []))
    taken_list.append({"key": key, "taken": taken})
    pending = [k for k in data.get("meds_pending", []) if k != key]
    await state.update_data(meds_taken=taken_list, meds_pending=pending)

    if pending:
        await _ask_next_med(cb.message, state, session_factory)
    else:
        await _maybe_ask_weight_or_save(cb.message, state, session_factory)
    await cb.answer()


async def _maybe_ask_weight_or_save(message: Message, state: FSMContext, session_factory):
    data = await state.get_data()
    day = date.fromisoformat(data["day"])
    if is_saturday(day):
        await state.set_state(TrackStates.weight)
        await message.edit_text(
            "Вес (кг)? Напиши числом или жми «Пропустить».",
            reply_markup=_weight_skip_keyboard(),
        )
    else:
        await _save_and_finish(message, state, session_factory)


@router.message(TrackStates.weight, F.text)
async def msg_weight(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
):
    try:
        weight = float(message.text.replace(",", "."))
    except ValueError:
        await message.answer("Не число. Попробуй ещё раз или жми «Пропустить».")
        return
    await state.update_data(weight=weight)
    await _save_and_finish(message, state, session_factory)


@router.callback_query(TrackStates.weight, F.data == "weight:skip")
async def cb_weight_skip(
    cb: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
):
    await _save_and_finish(cb.message, state, session_factory)
    await cb.answer()


async def _save_and_finish(message: Message, state: FSMContext, session_factory):
    data = await state.get_data()
    day = date.fromisoformat(data["day"])

    async with session_factory() as session:
        existing = await session.get(MoodEntry, day)
        if existing:
            existing.mood = data.get("mood")
            existing.anxiety = data.get("anxiety")
            existing.irritability = data.get("irritability")
            existing.sleep_hours = data.get("sleep_hours")
            if "weight" in data:
                existing.weight = data["weight"]
        else:
            session.add(MoodEntry(
                day=day,
                mood=data.get("mood"),
                anxiety=data.get("anxiety"),
                irritability=data.get("irritability"),
                sleep_hours=data.get("sleep_hours"),
                weight=data.get("weight"),
            ))
        for entry in data.get("meds_taken", []):
            log = await session.get(MedicationLog, (day, entry["key"]))
            if log:
                log.taken = entry["taken"]
            else:
                session.add(MedicationLog(
                    day=day, med_key=entry["key"], taken=entry["taken"],
                ))
        await session.commit()

    summary = (
        f"✅ Сохранено за {day.isoformat()}:\n"
        f"настр. {data.get('mood', '?'):+d}, "
        f"тревога {data.get('anxiety', '?')}, "
        f"раздр. {data.get('irritability', '?')}, "
        f"сон {data.get('sleep_hours', '?')}ч"
    )
    if "weight" in data:
        summary += f", вес {data['weight']}кг"
    await message.edit_text(summary)
    await state.clear()
    logger.info("track saved for %s by handler", day)
```

- [ ] **Step 3: Verify the module imports cleanly**

Run: `python -c "from rutix.bot.handlers import track; print(track.router.name)"`
Expected: `track`

Run: `ruff check src/rutix/bot/handlers/track.py`
Expected: no output.

- [ ] **Step 4: Commit**

```bash
git add src/rutix/bot/handlers/__init__.py src/rutix/bot/handlers/track.py
git commit -m "feat(bot): /track FSM — mood→anx→irr→sleep→meds→[sat:weight]→save"
```

---

## Task 11: /sync handler

**Files:**
- Create: `src/rutix/bot/handlers/sync.py`

`/sync` forces an immediate flush of yesterday — for when 03:00 cron failed
or when a retroactive `/track` edit needs to land in git right now.

- [ ] **Step 1: Implement `sync.py`**

`src/rutix/bot/handlers/sync.py`:
```python
"""/sync — force flush of yesterday into mood_tracker.md."""
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rutix.integrations.github import GitHubClient
from rutix.jobs.flush_day import flush_day
from rutix.settings import Settings
from rutix.time_utils import subjective_today, yesterday_of

logger = logging.getLogger(__name__)

router = Router(name="sync")


@router.message(Command("sync"))
async def cmd_sync(
    message: Message,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    github: GitHubClient,
):
    today = subjective_today(datetime.now(ZoneInfo(settings.tz)), settings.tz)
    target = yesterday_of(today)
    async with session_factory() as session:
        try:
            sha = await flush_day(session, github, target)
        except Exception as e:
            logger.exception("sync failed")
            await message.answer(f"❌ /sync упал: {type(e).__name__}: {e}")
            return
    if sha:
        await message.answer(f"✅ Закоммитил {target.isoformat()} → {sha[:7]}")
    else:
        await message.answer(f"⏭ Нечего коммитить за {target.isoformat()} (уже сделано или нет данных)")
```

- [ ] **Step 2: Verify import**

Run: `python -c "from rutix.bot.handlers import sync; print(sync.router.name)"`
Expected: `sync`

- [ ] **Step 3: Commit**

```bash
git add src/rutix/bot/handlers/sync.py
git commit -m "feat(bot): /sync — force flush yesterday"
```

---

## Task 12: APScheduler integration

**Files:**
- Create: `src/rutix/jobs/scheduler.py`

- [ ] **Step 1: Implement `scheduler.py`**

`src/rutix/jobs/scheduler.py`:
```python
"""APScheduler — daily 03:00 cron that calls flush_day(yesterday)."""
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from rutix.integrations.github import GitHubClient
from rutix.jobs.flush_day import flush_day
from rutix.time_utils import subjective_today, yesterday_of

logger = logging.getLogger(__name__)


def make_scheduler(
    session_factory: async_sessionmaker[AsyncSession],
    github: GitHubClient,
    tz: str,
) -> AsyncIOScheduler:
    """Build (but don't start) the scheduler with the daily flush job."""
    scheduler = AsyncIOScheduler(timezone=ZoneInfo(tz))

    async def daily_flush():
        today = subjective_today(datetime.now(ZoneInfo(tz)), tz)
        target = yesterday_of(today)
        logger.info("scheduled flush running for %s", target)
        async with session_factory() as session:
            try:
                sha = await flush_day(session, github, target)
                if sha:
                    logger.info("scheduled flush committed: %s", sha)
                else:
                    logger.info("scheduled flush — nothing to do")
            except Exception:
                logger.exception("scheduled flush failed")

    scheduler.add_job(
        daily_flush,
        trigger=CronTrigger(hour=3, minute=0, timezone=ZoneInfo(tz)),
        id="daily_flush",
        replace_existing=True,
    )
    return scheduler
```

- [ ] **Step 2: Verify import**

Run: `python -c "from rutix.jobs.scheduler import make_scheduler; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add src/rutix/jobs/scheduler.py
git commit -m "feat(jobs): APScheduler with 03:00 MSK daily flush"
```

---

## Task 13: Entry point — wire everything in `__main__.py`

**Files:**
- Modify: `src/rutix/__main__.py`

- [ ] **Step 1: Replace `__main__.py` content**

`src/rutix/__main__.py`:
```python
"""rutix entry point — long-poll bot + APScheduler in one process."""
import asyncio
import logging

from pythonjsonlogger import jsonlogger

from rutix.bot.app import build_bot, build_dispatcher
from rutix.db.engine import make_engine, make_session_factory
from rutix.integrations.github import GitHubClient
from rutix.jobs.scheduler import make_scheduler
from rutix.settings import load_settings


def _setup_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(jsonlogger.JsonFormatter(
        "%(asctime)s %(name)s %(levelname)s %(message)s"
    ))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers = [handler]


async def _run() -> None:
    _setup_logging()
    log = logging.getLogger("rutix")
    settings = load_settings()
    log.info("rutix starting (user_id=%s tz=%s)", settings.telegram_user_id, settings.tz)

    engine = make_engine(settings.database_url)
    session_factory = make_session_factory(engine)
    github = GitHubClient(token=settings.github_api_token, repo=settings.life_repo)

    bot = build_bot(settings.bot_token)
    dp = build_dispatcher(allowed_user_id=settings.telegram_user_id)
    dp["session_factory"] = session_factory
    dp["github"] = github
    dp["settings"] = settings

    scheduler = make_scheduler(session_factory, github, settings.tz)
    scheduler.start()

    try:
        await dp.start_polling(bot)
    finally:
        log.info("rutix shutting down")
        scheduler.shutdown(wait=False)
        await github.aclose()
        await bot.session.close()
        await engine.dispose()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Implement `bot/app.py`**

`src/rutix/bot/app.py`:
```python
"""Build aiogram Bot + Dispatcher with all routers wired."""
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from rutix.bot.auth import WhitelistMiddleware
from rutix.bot.handlers import sync as sync_handler
from rutix.bot.handlers import track as track_handler


def build_bot(token: str) -> Bot:
    return Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


def build_dispatcher(allowed_user_id: int) -> Dispatcher:
    dp = Dispatcher()
    dp.update.middleware(WhitelistMiddleware(allowed_user_id))
    dp.include_router(track_handler.router)
    dp.include_router(sync_handler.router)
    return dp
```

- [ ] **Step 3: Verify the package imports end-to-end**

Run: `python -c "import rutix.__main__; print('imports ok')"`
Expected: `imports ok` (no exception).

Run: `ruff check src/`
Expected: no output.

- [ ] **Step 4: Commit**

```bash
git add src/rutix/__main__.py src/rutix/bot/app.py
git commit -m "feat: wire bot + scheduler in __main__"
```

---

## Task 14: Local Docker setup

**Files:**
- Create: `Dockerfile`
- Create: `docker-compose.yml`
- Create: `.dockerignore`

- [ ] **Step 1: Create `.dockerignore`**

```
.git
.venv
.ruff_cache
.pytest_cache
__pycache__
*.pyc
data/
.env
docs/
tests/
.claude/
```

- [ ] **Step 2: Create `Dockerfile` (multi-stage)**

```dockerfile
FROM python:3.12-slim AS builder
WORKDIR /build
COPY pyproject.toml ./
RUN pip install --no-cache-dir .

FROM python:3.12-slim
WORKDIR /app
RUN mkdir -p /app/data
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY src ./src
COPY alembic ./alembic
COPY alembic.ini ./
ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src
CMD ["sh", "-c", "alembic upgrade head && python -m rutix"]
```

- [ ] **Step 3: Create `docker-compose.yml` (local dev)**

```yaml
services:
  bot:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: rutix-bot
    restart: unless-stopped
    volumes:
      - ./data:/app/data
    env_file:
      - .env
    environment:
      - DATABASE_URL=sqlite+aiosqlite:///data/bot.db
```

- [ ] **Step 4: Verify the image builds**

Run: `cd /Users/elabdi/Desktop/rutix && docker build -t rutix:local .`
Expected: Successfully built. Last lines should mention `naming to docker.io/library/rutix:local`.

(Don't run the container yet — that's the smoke-test task. This step only verifies the Dockerfile is syntactically correct and all `COPY` paths resolve.)

- [ ] **Step 5: Commit**

```bash
git add Dockerfile docker-compose.yml .dockerignore
git commit -m "feat(docker): multi-stage build + local docker-compose"
```

---

## Task 15: CI workflow (GitHub Actions)

**Files:**
- Create: `.github/workflows/ci.yml`

This is a `workflow_call` workflow — Phase 3 will add `prod.yml` that calls it. For Phase 1, we also wire a `pull_request` + `push to main` trigger so it runs on every commit.

- [ ] **Step 1: Create `.github/workflows/ci.yml`**

```yaml
name: CI

on:
  workflow_call:
  pull_request:
  push:
    branches: [main]

jobs:
  lint:
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
          cache-dependency-path: pyproject.toml

      - name: Install ruff
        run: pip install ruff

      - name: Ruff check
        run: ruff check src/

      - name: Ruff format check
        run: ruff format --check src/

  test:
    runs-on: ubuntu-latest
    permissions:
      contents: read
    env:
      BOT_TOKEN: "000000000:fake-token-for-testing"
      TELEGRAM_USER_ID: "1"
      GITHUB_API_TOKEN: "ghp_fake"
      DATABASE_URL: "sqlite+aiosqlite:///:memory:"
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
          cache-dependency-path: pyproject.toml

      - name: Install dependencies
        run: pip install -e ".[dev]"

      - name: Validate Alembic heads count
        run: |
          set -e
          HEAD_COUNT=$(alembic heads | wc -l | tr -d ' ')
          if [ "$HEAD_COUNT" -ne 1 ]; then
            echo "Expected exactly 1 Alembic head, got $HEAD_COUNT"
            alembic heads
            exit 1
          fi

      - name: Validate Alembic upgrade
        run: |
          mkdir -p data
          alembic -x sqlalchemy.url=sqlite:///data/test.db upgrade head

      - name: Run pytest
        run: pytest tests/ -v
```

- [ ] **Step 2: Commit and push to trigger first CI run**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: ruff + pytest + alembic head/upgrade validation"
git push origin main
```

- [ ] **Step 3: Verify CI passes on GitHub**

Run: `gh run watch`
Expected: Both `lint` and `test` jobs succeed (~1-2 min).

If CI fails: read the logs (`gh run view --log-failed`), fix the issue locally, commit + push, repeat.

---

## Task 16: Local smoke test guide

**Files:**
- Modify: `README.md`

This task adds a written runbook for verifying Phase 1 end-to-end on a local machine. No code, no tests — just documenting how to know the bot actually works.

- [ ] **Step 1: Append a "Phase 1 smoke test" section to `README.md`**

Add to the end of `/Users/elabdi/Desktop/rutix/README.md`:

```markdown
## Phase 1 smoke test

Pre-reqs:
- Telegram bot created via [@BotFather](https://t.me/botfather) → `BOT_TOKEN`
- Your Telegram numeric user_id (use [@userinfobot](https://t.me/userinfobot)) → `TELEGRAM_USER_ID`
- GitHub fine-grained PAT for `quibex/life`, `Contents: read+write` → `GITHUB_API_TOKEN`

### 1. Configure

```bash
cp .env.example .env
# Fill in BOT_TOKEN, TELEGRAM_USER_ID, GITHUB_API_TOKEN
```

### 2. Seed the active medications protocol

```bash
docker compose up -d
docker compose exec bot python -c "
import asyncio
from datetime import date
from rutix.db.engine import make_engine, make_session_factory
from rutix.db.models import MedActive
from rutix.settings import load_settings

async def seed():
    settings = load_settings()
    engine = make_engine(settings.database_url)
    Session = make_session_factory(engine)
    async with Session() as s:
        s.add(MedActive(key='seizar', name='Сейзар', column_label='Сейзар',
                        current_dose='25', started_at=date(2026, 4, 26)))
        s.add(MedActive(key='gidr_kanon', name='Гидр.Канон', column_label='Гидр.К',
                        current_dose='12.5', started_at=date(2026, 4, 26)))
        await s.commit()

asyncio.run(seed())
"
```

(Future: a `/meds add` command will replace this — Phase 2.)

### 3. Run the bot

```bash
docker compose up
```

Watch logs for `rutix starting (user_id=... tz=Europe/Moscow)`.

### 4. /track flow

In Telegram with your bot:
- Send `/track`
- Tap through the buttons: mood → anxiety → irritability → sleep → meds (one per active)
- (If today is Saturday, also enter weight)
- Bot replies `✅ Сохранено за <date>: ...`

Verify the data landed in SQLite:
```bash
docker compose exec bot sqlite3 /app/data/bot.db "SELECT * FROM mood_entries;"
docker compose exec bot sqlite3 /app/data/bot.db "SELECT * FROM medication_log;"
```

### 5. Force a flush with /sync

In Telegram: send `/sync`.
- If yesterday has a tracked entry → bot replies `✅ Закоммитил <YYYY-MM-DD> → <sha>`
- Otherwise → `⏭ Нечего коммитить за <date>`

Verify in `quibex/life` repo:
```bash
cd /Users/elabdi/Documents/StarkSync/life
git pull
grep -A1 "$(date -v-1d +%d)" health/mood_tracker.md  # macOS
```

The yesterday row should now have your data.

### 6. Wait for 03:00 (or fake the cron)

For instant verification without waiting until 03:00, you can trigger the
scheduled function manually inside the container:

```bash
docker compose exec bot python -c "
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from rutix.db.engine import make_engine, make_session_factory
from rutix.integrations.github import GitHubClient
from rutix.jobs.flush_day import flush_day
from rutix.settings import load_settings
from rutix.time_utils import subjective_today, yesterday_of

async def go():
    s = load_settings()
    engine = make_engine(s.database_url)
    Session = make_session_factory(engine)
    gh = GitHubClient(s.github_api_token, s.life_repo)
    today = subjective_today(datetime.now(ZoneInfo(s.tz)), s.tz)
    target = yesterday_of(today)
    async with Session() as sess:
        sha = await flush_day(sess, gh, target)
        print('flush result:', sha)
    await gh.aclose()

asyncio.run(go())
"
```
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: phase 1 smoke test runbook"
```

- [ ] **Step 3: Push and confirm CI passes**

```bash
git push origin main
gh run watch
```

Expected: CI green.

---

## Phase 1 Done When

All of the following are true:

1. `pytest tests/` — all green
2. `ruff check src/ && ruff format --check src/` — clean
3. `alembic upgrade head` against a fresh sqlite — works
4. `docker build -t rutix:local . && docker compose up` — bot starts, logs show `rutix starting`
5. `/track` in Telegram completes successfully and data lands in SQLite
6. `/sync` in Telegram writes a row into `quibex/life:health/mood_tracker.md` matching the entered values
7. The 03:00 cron is registered (visible in logs at startup) — actual fire is verifiable manually via the snippet in section 6 of the smoke test
8. CI is green on GitHub for the `main` branch

---

## Self-Review notes (filled by author)

**Spec coverage:**
- ✅ `/track` (mood, anxiety, irritability, sleep, meds, sat-only weight) — Task 10
- ✅ `/sync` — Task 11
- ✅ SQLite schema (mood_entries, medication_log, meds_active, flush_log) — Task 4 + 5
- ✅ Cron 03:00 daily flush — Task 12
- ✅ GitHub writes to `health/mood_tracker.md` — Task 7 + 8
- ✅ Whitelist by `TELEGRAM_USER_ID` — Task 9
- ✅ `subjective_today` (< 05:00 → yesterday) — Task 3
- ✅ Idempotent flush via `flush_log` — Task 8
- ⏳ Out of scope (Phase 2/3): `/eat`, `/note`, `/done`, `/today`, `/week`, `/meds`, Todoist habits, Sunday weekly+nutrition flush, prod deploy

**Type consistency check:**
- `MedColumn.column_label` / `MedColumn.dose` / `MedColumn.taken` — used identically in render (Task 7) and flush_day (Task 8) ✓
- `FlushLog.period_id` format `"day:YYYY-MM-DD"` — produced by `flush_day` and consumed by the same idempotency check ✓
- `make_session_factory` returns `async_sessionmaker[AsyncSession]` — matches handler kwarg type annotations ✓

**Placeholder scan:** none found.
