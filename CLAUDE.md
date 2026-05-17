# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Single-user Telegram bot (whitelisted by numeric `TELEGRAM_USER_ID`) that tracks mental state, medications, and nutrition. Persists structured daily/weekly data into a private Obsidian-backed GitHub repo (default `quibex/life`). Long-polling; no inbound traffic. One container, one VPS.

Bot UX language is Russian — preserve Russian strings in user-facing messages, button labels, and commit messages when editing handlers.

## Common commands

Dev runs through Docker:

```bash
docker compose up                          # build & run, tails logs
docker compose up -d --force-recreate      # restart after .env changes
docker compose exec bot sqlite3 /app/data/bot.db "SELECT * FROM mood_entries;"
docker compose logs --tail=50 bot
```

Local Python (host machine) for tests/lint — `pip install -e ".[dev]"` once, then:

```bash
pytest tests/                              # full suite (asyncio_mode=auto, in-memory sqlite)
pytest tests/unit/test_eat_handler.py      # single file
pytest tests/unit/test_eat_handler.py::test_cmd_eat_text_only -v   # single test
ruff check src/                            # CI runs this
ruff format --check src/                   # CI runs this; `ruff format src/` to fix

alembic upgrade head                       # apply migrations to data/bot.db
alembic heads                              # CI fails if >1 head
alembic revision --autogenerate -m "msg"   # new migration after model change
```

Every push to `main` runs `.github/workflows/prod.yml` → ci (ruff + pytest + alembic) → build & push to `ghcr.io/quibex/rutix:latest` → SSH deploy to VPS → health check. README §"Phase 3" has the VPS / secrets setup.

## Architecture

### Two-tier persistence — SQLite is a buffer, GitHub is the source of truth

`MoodEntry` and `MedicationLog` hold the **current week only**. They get flushed to markdown files in `quibex/life` and then **deleted** by `flush_week` on Monday 03:00. Anything written there is ephemeral by design — don't add code that reads from SQLite for data older than 7 days.

`MedActive` (current med protocol) and `FlushLog` (idempotency ledger) are persistent. `FlushLog.period_id` uses `day:<iso>` / `week:<id>` keys — the flush jobs short-circuit when an entry exists.

The GitHub `Contents API` writes are atomic per file: `read()` returns text + SHA, `write()` requires that SHA for updates. A SHA mismatch will raise — there's no built-in retry. Caller decides.

### Cron is `daily_3am` + `evening_ping`, not three separate jobs

[src/rutix/jobs/scheduler.py](src/rutix/jobs/scheduler.py) registers exactly two cron triggers. `daily_3am` runs `flush_day(yesterday) → update_habits(yesterday) → flush_week(today)` in sequence; each is wrapped in its own try/except so a failure in one doesn't skip the others. `/sync` is a manual trigger that calls **only** `flush_day` for yesterday.

### Subjective day (3am boundary)

[src/rutix/time_utils.py](src/rutix/time_utils.py): `subjective_today()` returns yesterday if local time is before 03:00. Every handler that needs "today" should use this — not `date.today()`. This is why the daily flush runs at 03:00: by then, the subjective day has rolled over and the previous day is sealed.

### Dependency injection via aiogram Dispatcher dict

[src/rutix/__main__.py](src/rutix/__main__.py) stuffs `session_factory`, `github`, `claude`, `todoist`, `settings` into `dp[...]`; aiogram injects them by parameter name into handlers. New handlers should accept these as typed kwargs, not import singletons. Tests construct handlers by passing fakes directly — keep handlers easy to call this way.

### Markdown is parsed and surgically edited, not regenerated

[src/rutix/markdown/daily.py](src/rutix/markdown/daily.py) and [mood_tracker.py](src/rutix/markdown/mood_tracker.py) edit specific sections (`## Питание`, `## Заметки`, etc.) inside files the user maintains in Obsidian. **Never rewrite a whole file** — preserve unrelated sections and trailing whitespace. `flush_day` writes a single row; `flush_week` is the only path that creates files from scratch (`weekly/<id>.md`, `nutrition/<id>.md`).

`update_habits` compares only the checkbox lines via `_checkbox_lines()` (not the whole text) to decide whether anything changed — cosmetic whitespace diffs from the regex edit are expected.

### Claude `/eat` parser uses structured outputs + prompt caching

[src/rutix/integrations/claude.py](src/rutix/integrations/claude.py): JSON output is enforced via `output_config.format` with `EAT_SCHEMA` (a hard API-level constraint). The prompt instruction "return JSON only" alone is unreliable — model wraps in ```json fences. `_strip_markdown_fences()` is defense-in-depth for the residual cases.

The system prompt (eat.md + reference.md) carries `cache_control: ephemeral` — 5-min cache TTL, auto-extends. Mutating either should be conscious of cache invalidation cost.

`/eat` is a multi-turn session: each new text/photo is sent to Claude alongside the current parsed list (`current_items`), and the model returns the updated list (add/replace/remove/adjust). Telegram albums are buffered by `media_group_id` with a 1.5s debounce so multi-photo messages become one Claude call.

### Whitelist drops, doesn't reject

[src/rutix/bot/auth.py](src/rutix/bot/auth.py): `WhitelistMiddleware` returns `None` for any user other than the configured one. Silent. If `/track` does nothing in prod, first thing to check is `TELEGRAM_USER_ID`.

### Todoist Activity Log requires Pro

[src/rutix/integrations/todoist.py](src/rutix/integrations/todoist.py): swallows 403 and returns an empty set so the cron doesn't crash. Activity Log's `since`/`until` params are ignored by the v1 endpoint, so we fetch a 200-event page and filter client-side by local-date.

## Conventions

- `subjective_today(now, tz)` everywhere — never `date.today()`.
- Use the dispatcher kwargs (`session_factory`, `github`, `claude`, `todoist`, `settings`) — don't import the singletons.
- Github commit messages in handlers/jobs follow `<area>(<scope>): <ru text>` (e.g. `eat(2026-05-17): 3 позиций`).
- `MealItem.source` is `"reference"` or `"estimate"`; estimates get appended to `nutrition/reference.md` under `## Из бота` after `/eat ✅`.
- New SQLAlchemy models go into `db/models.py` + an Alembic migration. CI fails if there's more than one Alembic head.
- Tests are async — `pytest-asyncio` in `auto` mode; use the `session` fixture from `tests/conftest.py` for an in-memory DB.
