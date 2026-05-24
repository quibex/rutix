# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Single-user Telegram bot (whitelisted by numeric `TELEGRAM_USER_ID`) that tracks mental state, medications, and nutrition. Persists structured daily data into a private Obsidian-backed GitHub repo (default `quibex/life`). Long-polling; no inbound traffic. One container, one VPS.

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

Prod VPS is reachable as `ssh monitor` (alias in `~/.ssh/config` → `193.109.193.167`, user `root`, key `~/.ssh/id_ed25519_kurut`). Compose stack lives at `/opt/rutix/`. Quick live-log peek: `ssh monitor 'cd /opt/rutix && docker compose logs --tail=200 bot'`.

## Architecture

### Two-tier persistence — SQLite is a buffer, GitHub is the source of truth

`MoodEntry` and `MedicationLog` are write-buffered in SQLite by `/track` and then flushed by `flush_day` into the daily file's `## Самочувствие` (and `## Время (ч)`) sections. There is no weekly closure — old SQLite rows just sit there harmlessly; the source of truth lives in the daily `.md` files.

`MedActive` (current med protocol) and `FlushLog` (idempotency ledger) are persistent. `FlushLog.period_id` uses `day:<iso>` keys — `flush_day` short-circuits when an entry exists.

The GitHub `Contents API` writes are atomic per file: `read()` returns text + SHA, `write()` requires that SHA for updates. A SHA mismatch will raise — there's no built-in retry. Caller decides.

### Cron jobs

[src/rutix/jobs/scheduler.py](src/rutix/jobs/scheduler.py) registers daily-only crons (no weekly job — the user plans the next week themselves and seeds `## 🗓 План на день` by hand in each daily file):

- `daily_3am` (03:00) → `flush_day(yesterday)` + `update_habits(yesterday)` + `reschedule_overdue(today)`, each in its own try/except.
- `update_habits_retry` (06:00, 08:00) — catch-up for Todoist outages.
- `daily_plan_ping` (09:00) → reads `## 🗓 План на день` from today's daily file and posts it.
- `med_reminder_tick` (every minute) — fires for active meds whose `reminder_time` matches now.
- `evening_ping` (21:00) — nudges to `/track` if not done.

`/sync` is a manual trigger that calls only `flush_day` for yesterday.

### Subjective day (3am boundary)

[src/rutix/time_utils.py](src/rutix/time_utils.py): `subjective_today()` returns yesterday if local time is before 03:00. Every handler that needs "today" should use this — not `date.today()`. This is why the daily flush runs at 03:00: by then, the subjective day has rolled over and the previous day is sealed.

### Dependency injection via aiogram Dispatcher dict

[src/rutix/__main__.py](src/rutix/__main__.py) stuffs `session_factory`, `github`, `claude`, `todoist`, `settings` into `dp[...]`; aiogram injects them by parameter name into handlers. New handlers should accept these as typed kwargs, not import singletons. Tests construct handlers by passing fakes directly — keep handlers easy to call this way.

### Markdown is parsed and surgically edited, not regenerated

[src/rutix/markdown/daily.py](src/rutix/markdown/daily.py) edits specific sections (`## Самочувствие`, `## Время (ч)`, `## Питание`, `## Привычки`, `## Что сделано`, `## Заметки`) inside daily files the user maintains in Obsidian. **Never rewrite a whole file** — preserve unrelated sections and trailing whitespace. `upsert_section` will replace an existing `## <title>` body or append the section at EOF if it's missing.

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
