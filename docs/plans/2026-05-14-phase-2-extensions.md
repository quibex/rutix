# Phase 2: Extensions — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bot becomes the **single writer** for everything in `quibex/life` — adds `/eat` (Claude-parsed nutrition), `/note`, `/done`, `/today`, `/week`, `/meds` commands, daily 03:00 habits-update via Todoist Activity Log, and Sunday weekly + nutrition aggregation that wraps up the week and purges SQLite.

**Architecture:** Same long-poll process as Phase 1. New `claude` and `todoist` integration clients (httpx + anthropic SDK). New `markdown.daily` module owns daily/*.md section parsing/editing — used by `/eat`, `/note`, `/done`, `/today`, `update_habits`, and weekly aggregation. New `markdown.weekly` and `markdown.nutrition_weekly` generate Sunday rollups deterministically (no Claude in cron — only `/eat` uses the API). Two new APScheduler jobs join the existing 03:00 mood flush: `update_habits` (also 03:00 daily) and `flush_week` (03:00 only on Mondays for the just-finished week).

**Tech Stack additions:** `anthropic ≥0.40` (Claude SDK), `python-dateutil ≥2.9` (parsing dates from Todoist + Russian month strings).

**Spec:** `quibex/life:projects/mood-bot.md` (sections: Architecture, Команды, Cron 03:00, Промпты, SQLite-схема).

**Reference patterns from Phase 1:**
- `src/rutix/markdown/mood_tracker.py` — section/regex-based markdown editor template
- `src/rutix/integrations/github.py` — httpx client pattern for read/write+SHA
- `src/rutix/jobs/flush_day.py` — orchestrator pattern (DB read → render → GitHub write → FlushLog mark)
- `src/rutix/bot/handlers/track.py` — FSM handler pattern (for `/meds`)

---

## Out of scope for Phase 2

These belong to Phase 3 (deploy):
- `prod.yml` GitHub Actions, GHCR push, SSH deploy
- VPS provisioning
- GitHub Secrets / Variables setup

These are explicit non-goals (clarified during brainstorming):
- Photo-based food parsing (label OCR)
- Auto-pinging "не забыл /track?"
- Cross-week backfill (`/track 12.05`)
- Auto-update of `meds_active` from `health/medication.md`
- Showing past weeks via `/week` (only current week — past weeks live in git)

---

## File map

**Created:**
```
rutix/
├── prompts/
│   └── eat.md                                — system prompt for Claude /eat parser
├── src/rutix/
│   ├── integrations/
│   │   ├── claude.py                         — anthropic SDK wrapper
│   │   └── todoist.py                        — Todoist REST (Activity Log + tasks)
│   ├── markdown/
│   │   ├── daily.py                          — parse/edit daily/*.md sections
│   │   ├── weekly.py                         — generate weekly review template + metrics
│   │   └── nutrition_weekly.py               — generate weekly nutrition summary (data only)
│   ├── bot/handlers/
│   │   ├── eat.py                            — /eat command
│   │   ├── note_done.py                      — /note + /done (bundled — same shape)
│   │   ├── today.py                          — /today command
│   │   ├── week.py                           — /week (7 buttons → day report)
│   │   └── meds.py                           — /meds list/add/archive/dose (FSM)
│   └── jobs/
│       ├── update_habits.py                  — daily 03:00: Todoist → habits in daily
│       └── flush_week.py                     — Monday 03:00: weekly + nutrition + cleanup
└── tests/unit/
    ├── test_claude_client.py
    ├── test_todoist_client.py
    ├── test_daily_md.py                      — biggest single test file
    ├── test_weekly_md.py
    ├── test_nutrition_weekly_md.py
    ├── test_eat_handler.py
    ├── test_note_done_handlers.py
    ├── test_today_handler.py
    ├── test_week_handler.py
    ├── test_meds_handler.py
    ├── test_update_habits.py
    └── test_flush_week.py
```

**Modified:**
- `pyproject.toml` — add `anthropic`, `python-dateutil` to runtime deps
- `src/rutix/settings.py` — promote `ANTHROPIC_API_KEY` and `TODOIST_TOKEN` from optional defaults to required fields
- `src/rutix/jobs/scheduler.py` — register `update_habits` (daily 03:00, after flush_day) and `flush_week` (Monday 03:00)
- `src/rutix/bot/app.py` — include new routers (eat, note_done, today, week, meds)
- `src/rutix/__main__.py` — instantiate `ClaudeClient`, `TodoistClient`, store in `dp[...]` for handlers + scheduler

---

## Key design decisions (locked in)

1. **Claude is only used by `/eat`.** Cron jobs (`update_habits`, `flush_week`) are pure Python — no API calls. Habits matching uses string equality. Weekly aggregation reads daily files and SQLite, computes deterministically.

2. **`reference.md` is fetched once per day per process** — cached in `ClaudeClient` and stitched into the system prompt for `/eat`.

3. **Slot for `/eat` is determined by local time:** `8-11` → Завтрак, `12-16` → Обед, `17-21` → Ужин, otherwise → Перекус. No buttons to override (user can edit daily file directly if Claude-mistakes).

4. **`/eat` is fire-and-forget.** Bot writes to GitHub immediately and reports the parsed result. No "confirm before write" preview.

5. **`/today` reads from two sources:** mood/meds from SQLite, daily-file sections (Питание, Заметки, Что сделано) from GitHub raw. Slow path (network), so add a "loading…" placeholder message.

6. **`/week` shows current ISO week only** (Mon..Sun buttons). For past weeks the user goes to Obsidian — by then everything is flushed.

7. **`/meds` FSM** mirrors the `/track` pattern: inline-button menu → choose action (add/archive/dose) → step through.

8. **Sunday flush runs Monday 03:00** (not Sunday 23:59) — that way the day-of-week boundary is unambiguous and we use the same cron tick as `flush_day`. Trigger condition: `today.weekday() == 0` (Monday) AND `yesterday` (Sunday) was in a different ISO week than `last_flushed_week`.

9. **Habits update relies on Todoist Pro** (Activity Log endpoint). If the API returns 403/payment-required, log the error and skip — don't crash the scheduler.

10. **Weekly summary is a data dump** — bot fills the metrics + analytics tables with hard numbers and leaves editorial sections (Фокус, Что получилось, Инсайты, Оценка) as empty templates. The user finishes them in Obsidian / via Claude.ai later.

11. **Nutrition weekly is fully auto-generated** — Сводка table + Детали по дням. No editorial section.

---

## Task ordering rationale

Foundation libs first (1: deps + settings, 2: daily.py — most reused module), then independent integration clients (3: claude, 4: todoist). Markdown generators (5: weekly, 6: nutrition_weekly) and orchestrators (7: flush_week, 8: update_habits) follow. Then handlers (9-13: eat, note/done, today, week, meds), wiring (14: scheduler, 15: __main__/app), README (16). Each task can be merged independently because earlier ones don't touch handler code.

---

## Task 1: Bootstrap — anthropic + dateutil deps, promote settings

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/rutix/settings.py`
- Modify: `tests/unit/test_settings.py`

- [ ] **Step 1: Add deps to `pyproject.toml`**

In the `dependencies = [...]` list, add (keep alphabetical-ish, after `anthropic` group — easiest is to append before `python-json-logger`):

```toml
    "anthropic>=0.40.0",
    "python-dateutil>=2.9.0",
```

Final list after edit:
```toml
dependencies = [
    "aiogram>=3.25.0",
    "anthropic>=0.40.0",
    "sqlalchemy>=2.0.36",
    "aiosqlite>=0.20.0",
    "alembic>=1.14.0",
    "httpx>=0.28.1",
    "pydantic>=2.10.5",
    "pydantic-settings>=2.7.1",
    "apscheduler>=3.11.0",
    "python-dateutil>=2.9.0",
    "python-json-logger>=3.0.0",
]
```

- [ ] **Step 2: Promote ANTHROPIC + TODOIST in `settings.py`**

Replace the current `Settings` class body in `src/rutix/settings.py`. Both go from `default=""` to required (no default). Final body:

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
    anthropic_api_key: str = Field(...)
    todoist_token: str = Field(...)

    life_repo: str = Field(default="quibex/life")
    database_url: str = Field(default="sqlite+aiosqlite:///data/bot.db")
    tz: str = Field(default="Europe/Moscow")


def load_settings() -> Settings:
    return Settings()
```

- [ ] **Step 3: Update `test_settings.py`**

Replace the file with:

```python
import pytest
from pydantic import ValidationError

from rutix.settings import Settings


REQUIRED_VARS = [
    "BOT_TOKEN",
    "TELEGRAM_USER_ID",
    "GITHUB_API_TOKEN",
    "ANTHROPIC_API_KEY",
    "TODOIST_TOKEN",
]


def _set_all(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "test-bot")
    monkeypatch.setenv("TELEGRAM_USER_ID", "12345")
    monkeypatch.setenv("GITHUB_API_TOKEN", "ghp_test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("TODOIST_TOKEN", "tod_test")


def test_settings_loads_required_fields_from_env(monkeypatch):
    _set_all(monkeypatch)

    s = Settings(_env_file=None)

    assert s.bot_token == "test-bot"
    assert s.telegram_user_id == 12345
    assert s.github_api_token == "ghp_test"
    assert s.anthropic_api_key == "sk-ant-test"
    assert s.todoist_token == "tod_test"


def test_settings_defaults(monkeypatch):
    for var in ["LIFE_REPO", "TZ", "DATABASE_URL"]:
        monkeypatch.delenv(var, raising=False)
    _set_all(monkeypatch)

    s = Settings(_env_file=None)

    assert s.life_repo == "quibex/life"
    assert s.tz == "Europe/Moscow"
    assert s.database_url == "sqlite+aiosqlite:///data/bot.db"


@pytest.mark.parametrize("missing", REQUIRED_VARS)
def test_settings_missing_required_raises(monkeypatch, missing):
    _set_all(monkeypatch)
    monkeypatch.delenv(missing, raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)
```

- [ ] **Step 4: Install new deps**

Run: `cd /Users/elabdi/Desktop/rutix && .venv/bin/pip install -e ".[dev]"` (or `uv pip install -e ".[dev]"`).
Expected: `anthropic` and `python-dateutil` installed.

- [ ] **Step 5: Run tests**

Run: `cd /Users/elabdi/Desktop/rutix && .venv/bin/pytest tests/unit/test_settings.py -v`
Expected: 7 passed (1 + 1 + 5 parametrized).

- [ ] **Step 6: Commit (NO push — controller batches)**

```bash
git add pyproject.toml src/rutix/settings.py tests/unit/test_settings.py
git commit -m "feat: phase-2 deps + promote ANTHROPIC/TODOIST to required settings"
```

---

## Task 2: Daily markdown module — parse + edit sections

**Files:**
- Create: `src/rutix/markdown/daily.py`
- Create: `tests/unit/test_daily_md.py`

This is the foundational module. `/eat`, `/note`, `/done`, `/today`, `update_habits`, and weekly aggregation all use it. Keep the API tight.

The format of a daily file (verified against `quibex/life:daily/2026-05-14.md`):

```markdown
# <Weekday>, <day> <month_ru>

[[<weekly>|← Неделя N]]

## План на день

- ...

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
- [x] 🌅 Skincare AM
...

---

## Питание

| Приём | Что | Ккал | Б | Ж | У |
|-------|-----|------|---|---|---|
|  |  |  |  |  |  |
| **Итого** |  |  |  |  |  |

---

## Что сделано

- ...

## Заметки

- ...
```

- [ ] **Step 1: Write failing tests**

`tests/unit/test_daily_md.py`:

```python
import pytest

from rutix.markdown.daily import (
    MealItem,
    append_done,
    append_meal,
    append_note,
    parse_meals,
    update_habits_checked,
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


# --- update_habits_checked ---

def test_update_habits_checked_marks_matching_habits():
    result = update_habits_checked(SAMPLE, done={"📚 Anki", "🥤 Протеин"})
    habits_block = result.split("## Привычки", 1)[1].split("---", 1)[0]
    assert "- [x] 📚 Anki" in habits_block
    assert "- [x] 🥤 Протеин" in habits_block
    assert "- [ ] 🌅 Skincare AM" in habits_block  # untouched


def test_update_habits_preserves_already_checked():
    result = update_habits_checked(SAMPLE, done={"🌙 Skincare PM"})
    habits_block = result.split("## Привычки", 1)[1].split("---", 1)[0]
    assert "- [x] 🌙 Skincare PM" in habits_block


def test_update_habits_no_change_when_done_set_empty():
    assert update_habits_checked(SAMPLE, done=set()) == SAMPLE


# --- append_meal + parse_meals + totals ---

def test_append_meal_writes_row_and_recomputes_totals():
    item = MealItem(
        slot="Обед", name="Шаурма", kcal=450, protein=22.0, fat=18.0, carbs=45.0
    )
    result = append_meal(SAMPLE, item)
    food = result.split("## Питание", 1)[1].split("---", 1)[0]
    assert "| Обед | Шаурма | 450 | 22 | 18 | 45 |" in food
    # Totals row updated
    assert "| **Итого** |  | **450** | **22** | **18** | **45** |" in food


def test_append_meal_to_non_empty_table_sums_totals():
    pre = SAMPLE.replace(
        "|  |  |  |  |  |  |\n| **Итого** |  |  |  |  |  |",
        "| Завтрак | Яйца | 200 | 14 | 14 | 2 |\n| **Итого** |  | **200** | **14** | **14** | **2** |",
    )
    item = MealItem(
        slot="Обед", name="Бургер", kcal=500, protein=20.0, fat=25.0, carbs=40.0
    )
    result = append_meal(pre, item)
    food = result.split("## Питание", 1)[1].split("---", 1)[0]
    assert "| Завтрак | Яйца | 200 | 14 | 14 | 2 |" in food
    assert "| Обед | Бургер | 500 | 20 | 25 | 40 |" in food
    assert "| **Итого** |  | **700** | **34** | **39** | **42** |" in food


def test_append_meal_omits_slot_label_if_same_as_previous_row():
    pre = SAMPLE.replace(
        "|  |  |  |  |  |  |\n| **Итого** |  |  |  |  |  |",
        "| Обед | Плов | 400 | 17 | 12 | 56 |\n| **Итого** |  | **400** | **17** | **12** | **56** |",
    )
    item = MealItem(
        slot="Обед", name="Чиабатта", kcal=300, protein=10.0, fat=15.0, carbs=30.0
    )
    result = append_meal(pre, item)
    food = result.split("## Питание", 1)[1].split("---", 1)[0]
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
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `.venv/bin/pytest tests/unit/test_daily_md.py -v`
Expected: ImportError — module missing.

- [ ] **Step 3: Implement `daily.py`**

`src/rutix/markdown/daily.py`:

```python
"""Parse and edit sections of daily/*.md files.

A daily file has these sections (top-to-bottom): Сон, Время (ч), Привычки,
Питание (table), Что сделано, Заметки. We touch Питание / Привычки /
Что сделано / Заметки. The rest stays as the user wrote it.
"""
import re
from dataclasses import dataclass


@dataclass
class MealItem:
    slot: str       # "Завтрак" | "Обед" | "Ужин" | "Перекус" | etc.
    name: str
    kcal: int
    protein: float
    fat: float
    carbs: float


# --- Section helpers --------------------------------------------------------

_SECTION_RE = re.compile(
    r"^## (?P<title>[^\n]+)\n(?P<body>.*?)(?=\n## |\Z)",
    re.MULTILINE | re.DOTALL,
)


def _replace_section_body(md: str, title: str, new_body: str) -> str:
    """Replace the body of `## <title>` with new_body. Body excludes the
    horizontal-rule terminator if one is present below.

    Raises ValueError if the section is not found.
    """
    for match in _SECTION_RE.finditer(md):
        if match.group("title").strip() == title:
            old_body = match.group("body")
            return md[: match.start("body")] + new_body + md[match.start("body") + len(old_body):]
    raise ValueError(f"{title} section not found")


def _section_body(md: str, title: str) -> str:
    """Return the raw body of `## <title>` (text between header and next '## ')."""
    for match in _SECTION_RE.finditer(md):
        if match.group("title").strip() == title:
            return match.group("body")
    raise ValueError(f"{title} section not found")


# --- Notes / Done -----------------------------------------------------------

def _append_bullet(body: str, text: str) -> str:
    """Append `- text` as a new bullet to a section body.

    Replaces a single empty `- ` placeholder if present; otherwise appends.
    Trailing whitespace and the section's terminating `---` line (if any)
    are preserved as-is.
    """
    lines = body.splitlines(keepends=False)
    placeholder_idx = None
    for i, line in enumerate(lines):
        if line.strip() == "-":
            placeholder_idx = i
            break

    new_line = f"- {text}"
    if placeholder_idx is not None:
        lines[placeholder_idx] = new_line
    else:
        # Find last bullet line; insert after it. If no bullets, insert before
        # any '---' or trailing blanks.
        last_bullet = -1
        for i, line in enumerate(lines):
            if line.lstrip().startswith("- "):
                last_bullet = i
        if last_bullet >= 0:
            lines.insert(last_bullet + 1, new_line)
        else:
            # No bullets — append after first blank
            lines.insert(0, new_line)

    return "\n".join(lines)


def append_note(md: str, text: str) -> str:
    body = _section_body(md, "Заметки")
    return _replace_section_body(md, "Заметки", _append_bullet(body, text))


def append_done(md: str, text: str) -> str:
    body = _section_body(md, "Что сделано")
    return _replace_section_body(md, "Что сделано", _append_bullet(body, text))


# --- Habits -----------------------------------------------------------------

_HABIT_LINE_RE = re.compile(r"^(\s*-\s*\[)([ x])(\]\s*)(.+?)\s*$")


def update_habits_checked(md: str, done: set[str]) -> str:
    """Mark each habit whose label is in `done` as checked. Already-checked
    habits stay checked. Unmatched habits are untouched.
    """
    if not done:
        return md
    body = _section_body(md, "Привычки")
    new_lines = []
    for line in body.splitlines():
        m = _HABIT_LINE_RE.match(line)
        if m:
            label = m.group(4).strip()
            if label in done:
                new_lines.append(f"{m.group(1)}x{m.group(3)}{m.group(4)}")
                continue
        new_lines.append(line)
    return _replace_section_body(md, "Привычки", "\n".join(new_lines))


# --- Meals ------------------------------------------------------------------

_TOTAL_RE = re.compile(r"^\|\s*\*\*Итого\*\*\s*\|", re.MULTILINE)
_DATA_ROW_RE = re.compile(
    r"^\|\s*([^|]*?)\s*\|\s*([^|]+?)\s*\|\s*(\d+)\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)\s*\|\s*$"
)
_HEADER_OR_SEP_RE = re.compile(r"^\|.*Приём.*\||^\|[\s\-:|]+\|$")


def parse_meals(md: str) -> list[MealItem]:
    """Read all data rows in the Питание table.

    The 'Приём' column carries through empty rows: an empty slot cell inherits
    the slot from the previous data row. Returns items in row order.
    """
    body = _section_body(md, "Питание")
    items: list[MealItem] = []
    current_slot = ""
    for line in body.splitlines():
        if not line.strip().startswith("|"):
            continue
        if _HEADER_OR_SEP_RE.match(line):
            continue
        if _TOTAL_RE.match(line):
            continue
        m = _DATA_ROW_RE.match(line)
        if not m:
            continue
        slot, name, kcal, p, f, c = m.groups()
        slot = slot.strip()
        if slot:
            current_slot = slot
        if not name.strip() or name.strip() == "":
            continue
        items.append(
            MealItem(
                slot=current_slot,
                name=name.strip(),
                kcal=int(kcal),
                protein=float(p),
                fat=float(f),
                carbs=float(c),
            )
        )
    return items


def _format_num(v: float) -> str:
    """For totals — strip trailing zero on whole numbers."""
    if v == int(v):
        return str(int(v))
    return f"{v:g}"


def append_meal(md: str, item: MealItem) -> str:
    """Append a meal row to the Питание table and recompute the Итого row.

    If the previous data row has the same slot label, the new row's slot
    cell is left empty (matches the existing repo convention).
    """
    body = _section_body(md, "Питание")
    lines = body.splitlines()

    # Locate Итого line index; it must exist
    itogo_idx = None
    for i, line in enumerate(lines):
        if _TOTAL_RE.match(line):
            itogo_idx = i
            break
    if itogo_idx is None:
        raise ValueError("Питание Итого row not found")

    # Determine the previous slot label (last non-placeholder data row)
    prev_slot = ""
    for line in reversed(lines[:itogo_idx]):
        if not line.startswith("|"):
            continue
        if _HEADER_OR_SEP_RE.match(line):
            continue
        m = _DATA_ROW_RE.match(line)
        if not m:
            continue
        s = m.group(1).strip()
        if s:
            prev_slot = s
            break

    rendered_slot = "" if item.slot == prev_slot else item.slot
    new_row = (
        f"| {rendered_slot} | {item.name} | {item.kcal} | "
        f"{_format_num(item.protein)} | {_format_num(item.fat)} | {_format_num(item.carbs)} |"
    )

    # Drop the empty placeholder row if it's right above Итого
    place_idx = itogo_idx - 1
    if (
        place_idx >= 0
        and lines[place_idx].strip().replace("|", "").replace(" ", "") == ""
    ):
        del lines[place_idx]
        itogo_idx -= 1

    lines.insert(itogo_idx, new_row)
    itogo_idx += 1

    # Recompute totals from all data rows
    items_now = []
    for line in lines[:itogo_idx]:
        if not line.startswith("|"):
            continue
        if _HEADER_OR_SEP_RE.match(line):
            continue
        m = _DATA_ROW_RE.match(line)
        if not m:
            continue
        items_now.append(
            (int(m.group(3)), float(m.group(4)), float(m.group(5)), float(m.group(6)))
        )
    total_kcal = sum(i[0] for i in items_now)
    total_p = sum(i[1] for i in items_now)
    total_f = sum(i[2] for i in items_now)
    total_c = sum(i[3] for i in items_now)
    lines[itogo_idx] = (
        f"| **Итого** |  | **{total_kcal}** | **{_format_num(total_p)}** | "
        f"**{_format_num(total_f)}** | **{_format_num(total_c)}** |"
    )

    return _replace_section_body(md, "Питание", "\n".join(lines))
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `.venv/bin/pytest tests/unit/test_daily_md.py -v`
Expected: 12 passed.

- [ ] **Step 5: Commit (NO push)**

```bash
git add src/rutix/markdown/daily.py tests/unit/test_daily_md.py
git commit -m "feat(markdown): daily.py — section parse/edit (notes/done/habits/meals)"
```

---

## Task 3: Claude API client

**Files:**
- Create: `src/rutix/integrations/claude.py`
- Create: `tests/unit/test_claude_client.py`

The client is a thin wrapper over the `anthropic` SDK. One method:
`parse_eat(text, reference_md) -> list[MealItem]`. Returns parsed items or
raises `ValueError` on malformed JSON.

The system prompt loads from `prompts/eat.md` (read on each call so editing
the prompt doesn't need a redeploy). For tests we monkeypatch the prompt path.

- [ ] **Step 1: Create `prompts/eat.md`**

`prompts/eat.md`:
```markdown
Ты — нутрициолог. Получаешь две вещи на вход:

1. Текст пользователя «что съел» (свободная форма, может быть кратко: «шаурма + кола»)
2. Справочник КБЖУ ниже (список известных продуктов с точными значениями)

Твоя задача — вернуть **СТРОГО** JSON в формате:

```json
{
  "items": [
    {
      "name": "<краткое название>",
      "kcal": <integer>,
      "protein": <float>,
      "fat": <float>,
      "carbs": <float>,
      "source": "reference" | "estimate"
    }
  ]
}
```

Правила:
- Если продукт есть в справочнике — возьми оттуда цифры дословно. `source: "reference"`.
- Если нет — оцени по аналогии с известными продуктами того же типа. `source: "estimate"`.
- В `name` не повторяй «оценка»/«estimate» — это уже в `source`.
- Никакого текста кроме JSON. Никаких markdown-блоков. Никаких комментариев.
```

- [ ] **Step 2: Write failing tests**

`tests/unit/test_claude_client.py`:

```python
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from rutix.integrations.claude import ClaudeClient
from rutix.markdown.daily import MealItem


@pytest.fixture
def fake_anthropic():
    client = MagicMock()
    client.messages = MagicMock()
    client.messages.create = AsyncMock()
    return client


@pytest.fixture
def claude(tmp_path, fake_anthropic, monkeypatch):
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "eat.md").write_text("EAT_SYSTEM\n", encoding="utf-8")
    return ClaudeClient(
        api_key="sk-ant-test",
        prompts_dir=prompts_dir,
        sdk_client=fake_anthropic,
    )


async def test_parse_eat_returns_meal_items(claude, fake_anthropic):
    payload = {
        "items": [
            {"name": "Шаурма", "kcal": 450, "protein": 22.0, "fat": 18.0, "carbs": 45.0, "source": "estimate"},
            {"name": "Кола 0.4л", "kcal": 170, "protein": 0, "fat": 0, "carbs": 42, "source": "reference"},
        ]
    }
    msg = MagicMock()
    msg.content = [MagicMock(text=json.dumps(payload))]
    fake_anthropic.messages.create.return_value = msg

    items = await claude.parse_eat("шаурма + кола", reference_md="## ВкусВилл\n...")

    assert items == [
        MealItem("", "Шаурма", 450, 22.0, 18.0, 45.0),
        MealItem("", "Кола 0.4л", 170, 0.0, 0.0, 42.0),
    ]
    fake_anthropic.messages.create.assert_awaited_once()
    call_kwargs = fake_anthropic.messages.create.call_args.kwargs
    assert "EAT_SYSTEM" in call_kwargs["system"]
    assert "## ВкусВилл" in call_kwargs["system"]
    assert call_kwargs["messages"][0]["content"] == "шаурма + кола"


async def test_parse_eat_raises_on_malformed_json(claude, fake_anthropic):
    msg = MagicMock()
    msg.content = [MagicMock(text="not a json")]
    fake_anthropic.messages.create.return_value = msg

    with pytest.raises(ValueError, match="invalid JSON"):
        await claude.parse_eat("eggs", reference_md="")


async def test_parse_eat_raises_on_missing_items_key(claude, fake_anthropic):
    msg = MagicMock()
    msg.content = [MagicMock(text='{"foo": "bar"}')]
    fake_anthropic.messages.create.return_value = msg

    with pytest.raises(ValueError, match="missing 'items'"):
        await claude.parse_eat("eggs", reference_md="")
```

- [ ] **Step 3: Run tests, verify they fail**

Run: `.venv/bin/pytest tests/unit/test_claude_client.py -v`
Expected: ImportError — module missing.

- [ ] **Step 4: Implement `claude.py`**

`src/rutix/integrations/claude.py`:

```python
"""Anthropic Claude API client — used by /eat to parse free-form food text.

Loads the system prompt from prompts/eat.md on every call so the prompt can
be edited without redeploying the bot.
"""
import json
import logging
from pathlib import Path

from anthropic import AsyncAnthropic

from rutix.markdown.daily import MealItem

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_MAX_TOKENS = 2000


class ClaudeClient:
    def __init__(
        self,
        api_key: str,
        prompts_dir: Path | str = "prompts",
        model: str = DEFAULT_MODEL,
        sdk_client: AsyncAnthropic | None = None,
    ):
        self.prompts_dir = Path(prompts_dir)
        self.model = model
        self._sdk = sdk_client or AsyncAnthropic(api_key=api_key)

    async def parse_eat(self, text: str, reference_md: str) -> list[MealItem]:
        eat_prompt = (self.prompts_dir / "eat.md").read_text(encoding="utf-8")
        system = f"{eat_prompt}\n\n# Справочник КБЖУ:\n\n{reference_md}"

        response = await self._sdk.messages.create(
            model=self.model,
            max_tokens=DEFAULT_MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": text}],
        )
        raw = response.content[0].text.strip()

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.error("Claude returned invalid JSON: %r", raw[:500])
            raise ValueError(f"Claude returned invalid JSON: {e}") from e

        if "items" not in payload:
            raise ValueError("Claude response missing 'items' key")

        return [
            MealItem(
                slot="",
                name=str(it["name"]),
                kcal=int(it["kcal"]),
                protein=float(it["protein"]),
                fat=float(it["fat"]),
                carbs=float(it["carbs"]),
            )
            for it in payload["items"]
        ]
```

- [ ] **Step 5: Run tests, verify they pass**

Run: `.venv/bin/pytest tests/unit/test_claude_client.py -v`
Expected: 3 passed.

- [ ] **Step 6: Commit (NO push)**

```bash
git add prompts/eat.md src/rutix/integrations/claude.py tests/unit/test_claude_client.py
git commit -m "feat(claude): anthropic client + eat.md system prompt"
```

---

## Task 4: Todoist API client (Activity Log)

**Files:**
- Create: `src/rutix/integrations/todoist.py`
- Create: `tests/unit/test_todoist_client.py`

The client wraps two endpoints:

- `GET /api/v1/activity?event_type=completed&since=...&until=...` — for habits update (returns recurring + non-recurring completions)
- (later, not in MVP) `GET /api/v1/tasks?filter=today` — for `/today` if we want to enrich with the day plan

For Phase 2 we only need Activity Log.

- [ ] **Step 1: Write failing tests**

`tests/unit/test_todoist_client.py`:

```python
from datetime import date

import httpx
import pytest
import respx

from rutix.integrations.todoist import TodoistClient


@pytest.fixture
def client():
    return TodoistClient(token="tod_test")


@respx.mock
async def test_completed_titles_for_day_returns_set(client):
    respx.get("https://api.todoist.com/api/v1/activity").mock(
        return_value=httpx.Response(200, json={
            "events": [
                {"event_type": "completed", "extra_data": {"content": "📚 Anki"}},
                {"event_type": "completed", "extra_data": {"content": "🌅 Skincare AM"}},
                {"event_type": "completed", "extra_data": {"content": "📚 Anki"}},  # dedupe
            ],
        })
    )
    titles = await client.completed_titles_for_day(date(2026, 5, 14))
    assert titles == {"📚 Anki", "🌅 Skincare AM"}
    await client.aclose()


@respx.mock
async def test_completed_titles_request_uses_iso_window(client):
    route = respx.get("https://api.todoist.com/api/v1/activity").mock(
        return_value=httpx.Response(200, json={"events": []})
    )
    await client.completed_titles_for_day(date(2026, 5, 14))
    params = dict(route.calls[0].request.url.params)
    assert params["event_type"] == "completed"
    assert params["since"] == "2026-05-14T00:00:00"
    assert params["until"] == "2026-05-14T23:59:59"
    await client.aclose()


@respx.mock
async def test_completed_titles_returns_empty_set_on_403(client):
    """403 = Activity Log requires Pro. Don't crash — log and return empty."""
    respx.get("https://api.todoist.com/api/v1/activity").mock(
        return_value=httpx.Response(403, json={"error": "Pro required"})
    )
    titles = await client.completed_titles_for_day(date(2026, 5, 14))
    assert titles == set()
    await client.aclose()


@respx.mock
async def test_completed_titles_raises_on_5xx(client):
    respx.get("https://api.todoist.com/api/v1/activity").mock(
        return_value=httpx.Response(500)
    )
    with pytest.raises(httpx.HTTPStatusError):
        await client.completed_titles_for_day(date(2026, 5, 14))
    await client.aclose()
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `.venv/bin/pytest tests/unit/test_todoist_client.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `todoist.py`**

`src/rutix/integrations/todoist.py`:

```python
"""Todoist REST API client — Activity Log for habit completions.

Activity Log requires Todoist Pro. On 403 we log and return an empty set
so the scheduler doesn't crash.
"""
import logging
from datetime import date

import httpx

logger = logging.getLogger(__name__)


class TodoistClient:
    BASE_URL = "https://api.todoist.com"

    def __init__(self, token: str, http: httpx.AsyncClient | None = None):
        self.http = http or httpx.AsyncClient(
            base_url=self.BASE_URL,
            headers={"Authorization": f"Bearer {token}"},
            timeout=15.0,
        )

    async def completed_titles_for_day(self, day: date) -> set[str]:
        """Return the set of task titles completed on the given local date.

        Includes recurring tasks (via Activity Log). Dedupes if a recurring
        task was completed twice on the same day.
        """
        params = {
            "event_type": "completed",
            "since": f"{day.isoformat()}T00:00:00",
            "until": f"{day.isoformat()}T23:59:59",
        }
        r = await self.http.get("/api/v1/activity", params=params)
        if r.status_code == 403:
            logger.warning(
                "Todoist Activity Log returned 403 — likely Pro required. "
                "Returning empty habit set."
            )
            return set()
        r.raise_for_status()
        data = r.json()
        return {
            ev["extra_data"]["content"]
            for ev in data.get("events", [])
            if ev.get("event_type") == "completed"
        }

    async def aclose(self) -> None:
        await self.http.aclose()
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `.venv/bin/pytest tests/unit/test_todoist_client.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit (NO push)**

```bash
git add src/rutix/integrations/todoist.py tests/unit/test_todoist_client.py
git commit -m "feat(todoist): Activity Log client for habit completions"
```

---

## Task 5: Weekly markdown — metrics + analytics generator

**Files:**
- Create: `src/rutix/markdown/weekly.py`
- Create: `tests/unit/test_weekly_md.py`

Generates a weekly review file with:
- Title + week range (e.g. "# Неделя 19 (4 — 10 мая)")
- Empty editorial section templates (Фокус, Что получилось, Что не получилось, Прогресс, Инсайты, Оценка)
- Filled "Метрики" table — per-habit completion counts
- Filled "Аналитика → Ключевые цифры" table — comparing this week vs prior week (if data available)

The function takes:
- ISO week (year, week_num)
- Per-day mood entries (from SQLite — already harvested before purge)
- Per-day daily file contents (from GitHub)
- Habits config (parsed from `habits.md` — daily + scheduled lists)

Returns the full weekly file content as a string.

- [ ] **Step 1: Write failing tests**

`tests/unit/test_weekly_md.py`:

```python
from datetime import date

from rutix.markdown.weekly import (
    HabitsConfig,
    WeeklyDay,
    render_weekly,
    russian_date_range,
)


def test_russian_date_range_same_month():
    assert russian_date_range(date(2026, 5, 4), date(2026, 5, 10)) == "4 — 10 мая"


def test_russian_date_range_cross_month():
    assert russian_date_range(date(2026, 4, 27), date(2026, 5, 3)) == "27 апр — 3 мая"


def test_render_weekly_includes_header_and_week_label():
    result = render_weekly(
        year=2026,
        week_num=19,
        days=[],
        habits=HabitsConfig(daily=[], scheduled={}),
    )
    assert result.startswith("# Неделя 19")


def test_render_weekly_metrics_table_counts_habits():
    days = [
        WeeklyDay(
            date=date(2026, 5, 4),
            done_habits={"📚 Anki", "🌅 Skincare AM"},
            sleep_offh=None,
            sleep_onh=None,
            kcal=2200,
        ),
        WeeklyDay(
            date=date(2026, 5, 5),
            done_habits={"📚 Anki"},
            sleep_offh=None,
            sleep_onh=None,
            kcal=3000,
        ),
    ]
    result = render_weekly(
        year=2026, week_num=19, days=days,
        habits=HabitsConfig(
            daily=["📚 Anki", "🌅 Skincare AM"],
            scheduled={"🏋️ Strength": ["ВТ", "ЧТ", "СБ"]},
        ),
    )
    assert "| 📚 Anki" in result
    assert "| 2 |" in result  # Anki counted twice
    assert "| 🌅 Skincare AM" in result
    assert "| 1 |" in result  # Skincare AM only once
    assert "| 🏋️ Strength" in result


def test_render_weekly_includes_empty_editorial_templates():
    result = render_weekly(
        year=2026, week_num=19, days=[],
        habits=HabitsConfig(daily=[], scheduled={}),
    )
    for h in ("## 🎯 Фокус этой недели", "## ✅ Что получилось хорошо?",
              "## ❌ Что не получилось? Почему?", "## 📈 Прогресс",
              "## 💡 Инсайты", "## Оценка недели"):
        assert h in result


def test_render_weekly_avg_kcal_when_data_present():
    days = [
        WeeklyDay(date=date(2026, 5, 4), done_habits=set(), sleep_offh=None, sleep_onh=None, kcal=2000),
        WeeklyDay(date=date(2026, 5, 5), done_habits=set(), sleep_offh=None, sleep_onh=None, kcal=3000),
    ]
    result = render_weekly(
        year=2026, week_num=19, days=days,
        habits=HabitsConfig(daily=[], scheduled={}),
    )
    # Avg of 2 days with kcal data = (2000 + 3000) / 2 = 2500
    assert "Ср. ккал/день | **2500**" in result or "Ср. ккал/день | 2500" in result
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `.venv/bin/pytest tests/unit/test_weekly_md.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `weekly.py`**

`src/rutix/markdown/weekly.py`:

```python
"""Generate weekly/2026-Wxx.md — metrics-filled, editorial-templated.

Bot fills hard numbers (Метрики table, Аналитика). Editorial sections
(Фокус, Что получилось, Что не получилось, Прогресс, Инсайты, Оценка)
are left as empty templates for the user to fill in Obsidian / via Claude.ai.
"""
from dataclasses import dataclass, field
from datetime import date

RU_MONTHS_GENITIVE = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля",
    5: "мая", 6: "июня", 7: "июля", 8: "августа",
    9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
}
RU_MONTH_SHORT = {
    1: "янв", 2: "фев", 3: "мар", 4: "апр", 5: "мая", 6: "июн",
    7: "июл", 8: "авг", 9: "сен", 10: "окт", 11: "ноя", 12: "дек",
}


@dataclass
class WeeklyDay:
    date: date
    done_habits: set[str]
    sleep_offh: float | None    # bedtime hour (e.g. 1.5 for 01:30) — Phase 2 keeps None
    sleep_onh: float | None     # wakeup hour
    kcal: int | None            # total kcal for the day (from daily.py parse_meals)


@dataclass
class HabitsConfig:
    daily: list[str]                                  # ["📚 Anki", ...]
    scheduled: dict[str, list[str]] = field(default_factory=dict)  # {"🏋️ Strength": ["ВТ","ЧТ","СБ"]}


def russian_date_range(start: date, end: date) -> str:
    if start.month == end.month:
        return f"{start.day} — {end.day} {RU_MONTHS_GENITIVE[start.month]}"
    return f"{start.day} {RU_MONTH_SHORT[start.month]} — {end.day} {RU_MONTHS_GENITIVE[end.month]}"


def _count_habit(days: list[WeeklyDay], habit: str) -> int:
    return sum(1 for d in days if habit in d.done_habits)


def _avg_kcal(days: list[WeeklyDay]) -> int | None:
    vals = [d.kcal for d in days if d.kcal is not None]
    if not vals:
        return None
    return round(sum(vals) / len(vals))


def render_weekly(
    year: int, week_num: int, days: list[WeeklyDay], habits: HabitsConfig
) -> str:
    if days:
        date_range = russian_date_range(days[0].date, days[-1].date)
        title = f"# Неделя {week_num} ({date_range})"
    else:
        title = f"# Неделя {week_num}"

    metric_rows = []
    for h in habits.daily:
        count = _count_habit(days, h)
        metric_rows.append(f"| {h} | 7 | {count} |")
    for h, weekdays in habits.scheduled.items():
        count = _count_habit(days, h)
        metric_rows.append(f"| {h} | {len(weekdays)} | {count} |")

    avg_kcal = _avg_kcal(days)
    avg_kcal_str = str(avg_kcal) if avg_kcal is not None else "н/д"

    return f"""{title}

> Контекст:

## 🎯 Фокус этой недели

1.

---

## Метрики

| Привычка | План | Факт |
|----------|------|------|
{chr(10).join(metric_rows) if metric_rows else "|  |  |  |"}

---

## ✅ Что получилось хорошо?

-

## ❌ Что не получилось? Почему?

-

## 📈 Прогресс

-

## 💡 Инсайты

-

---

## Оценка недели: /10

---

## 📊 Аналитика

### Ключевые цифры

| Метрика | План | Факт |
|---------|------|------|
| Ср. ккал/день | — | **{avg_kcal_str}** |
"""
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `.venv/bin/pytest tests/unit/test_weekly_md.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit (NO push)**

```bash
git add src/rutix/markdown/weekly.py tests/unit/test_weekly_md.py
git commit -m "feat(markdown): weekly review generator (data-only, editorial templates empty)"
```

---

## Task 6: Nutrition weekly markdown — Сводка + per-day details

**Files:**
- Create: `src/rutix/markdown/nutrition_weekly.py`
- Create: `tests/unit/test_nutrition_weekly_md.py`

Pure aggregation from per-day daily file content. Format mirrors `quibex/life:nutrition/2026-W19.md`.

- [ ] **Step 1: Write failing tests**

`tests/unit/test_nutrition_weekly_md.py`:

```python
from datetime import date

from rutix.markdown.daily import MealItem
from rutix.markdown.nutrition_weekly import (
    NutritionDay,
    render_nutrition_weekly,
)


def test_render_with_one_day():
    days = [
        NutritionDay(
            date=date(2026, 5, 4),
            meals=[
                MealItem("Завтрак", "Яйца", 200, 14.0, 14.0, 2.0),
                MealItem("Обед", "Плов", 400, 17.0, 12.0, 56.0),
            ],
        )
    ]
    result = render_nutrition_weekly(year=2026, week_num=19, days=days)

    assert result.startswith("# КБЖУ — Неделя 19")
    # Сводка row for the day
    assert "| ПН 04 | 600 |" in result
    # Avg row (1 day, so equal to the day)
    assert "| **Ср.** | **600** |" in result
    # Per-day section header
    assert "### ПН 04" in result
    # Per-day table data + Итого
    assert "| Завтрак | Яйца | 200 | 14 | 14 | 2 |" in result
    assert "| **Итого** |  | **600** |" in result


def test_render_empty_day_shows_zeros_in_summary_only():
    days = [
        NutritionDay(date=date(2026, 5, 4), meals=[]),
        NutritionDay(date=date(2026, 5, 5), meals=[
            MealItem("Обед", "Плов", 400, 17.0, 12.0, 56.0),
        ]),
    ]
    result = render_nutrition_weekly(year=2026, week_num=19, days=days)
    # Empty day shows zeros in the table
    assert "| ПН 04 | 0 | 0 | 0 | 0 |" in result
    # Empty day per-day section is omitted (no point in empty table)
    assert "### ПН 04" not in result
    assert "### ВТ 05" in result


def test_render_avg_skips_zero_days():
    """Average should be over days with data (>= 1 meal), not all 7."""
    days = [
        NutritionDay(date=date(2026, 5, 4), meals=[]),                 # 0 kcal
        NutritionDay(date=date(2026, 5, 5), meals=[MealItem("Обед", "x", 1000, 0, 0, 0)]),
        NutritionDay(date=date(2026, 5, 6), meals=[MealItem("Обед", "y", 2000, 0, 0, 0)]),
    ]
    result = render_nutrition_weekly(year=2026, week_num=19, days=days)
    # Avg = (1000 + 2000) / 2 = 1500
    assert "| **Ср.** | **1500** |" in result
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `.venv/bin/pytest tests/unit/test_nutrition_weekly_md.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `nutrition_weekly.py`**

`src/rutix/markdown/nutrition_weekly.py`:

```python
"""Generate nutrition/2026-Wxx.md from per-day MealItem lists.

Pure aggregation — Сводка table + per-day full meal tables. Editorial
"Наблюдения" section is omitted (Phase 3 might add it via Claude).
"""
from dataclasses import dataclass
from datetime import date

from rutix.markdown.daily import MealItem
from rutix.markdown.weekly import RU_MONTHS_GENITIVE, russian_date_range

RU_WEEKDAYS_SHORT = {0: "ПН", 1: "ВТ", 2: "СР", 3: "ЧТ", 4: "ПТ", 5: "СБ", 6: "ВС"}


@dataclass
class NutritionDay:
    date: date
    meals: list[MealItem]


def _day_label(d: date) -> str:
    return f"{RU_WEEKDAYS_SHORT[d.weekday()]} {d.day:02d}"


def _totals(meals: list[MealItem]) -> tuple[int, float, float, float]:
    return (
        sum(m.kcal for m in meals),
        round(sum(m.protein for m in meals), 1),
        round(sum(m.fat for m in meals), 1),
        round(sum(m.carbs for m in meals), 1),
    )


def _format_num(v: float) -> str:
    if v == int(v):
        return str(int(v))
    return f"{v:g}"


def render_nutrition_weekly(year: int, week_num: int, days: list[NutritionDay]) -> str:
    if days:
        date_range = russian_date_range(days[0].date, days[-1].date)
        title = f"# КБЖУ — Неделя {week_num} ({date_range})"
    else:
        title = f"# КБЖУ — Неделя {week_num}"

    # --- Сводка table ---
    summary_rows = []
    days_with_data = []
    for d in days:
        kcal, p, f, c = _totals(d.meals)
        summary_rows.append(
            f"| {_day_label(d.date)} | {kcal} | {_format_num(p)} | {_format_num(f)} | {_format_num(c)} |"
        )
        if d.meals:
            days_with_data.append((kcal, p, f, c))

    if days_with_data:
        n = len(days_with_data)
        avg_kcal = round(sum(x[0] for x in days_with_data) / n)
        avg_p = round(sum(x[1] for x in days_with_data) / n, 1)
        avg_f = round(sum(x[2] for x in days_with_data) / n, 1)
        avg_c = round(sum(x[3] for x in days_with_data) / n, 1)
        avg_row = (
            f"| **Ср.** | **{avg_kcal}** | **{_format_num(avg_p)}** | "
            f"**{_format_num(avg_f)}** | **{_format_num(avg_c)}** |"
        )
    else:
        avg_row = "| **Ср.** | — | — | — | — |"

    # --- Per-day tables ---
    detail_blocks = []
    for d in days:
        if not d.meals:
            continue
        rows = []
        prev_slot = ""
        for m in d.meals:
            slot_cell = m.slot if m.slot != prev_slot else ""
            if m.slot:
                prev_slot = m.slot
            rows.append(
                f"| {slot_cell} | {m.name} | {m.kcal} | {_format_num(m.protein)} | "
                f"{_format_num(m.fat)} | {_format_num(m.carbs)} |"
            )
        kcal, p, f, c = _totals(d.meals)
        rows.append(
            f"| **Итого** |  | **{kcal}** | **{_format_num(p)}** | "
            f"**{_format_num(f)}** | **{_format_num(c)}** |"
        )
        detail_blocks.append(
            f"### {_day_label(d.date)}\n\n"
            "| Приём | Что | Ккал | Б | Ж | У |\n"
            "|-------|-----|------|---|---|---|\n"
            + "\n".join(rows)
        )

    return (
        f"{title}\n\n"
        f"## Сводка\n\n"
        f"| День | Ккал | Б | Ж | У |\n"
        f"|------|------|---|---|---|\n"
        + ("\n".join(summary_rows) + "\n" if summary_rows else "")
        + f"{avg_row}\n\n"
        + "## Детали по дням\n\n"
        + ("\n\n".join(detail_blocks) if detail_blocks else "")
        + ("\n" if detail_blocks else "")
    )
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `.venv/bin/pytest tests/unit/test_nutrition_weekly_md.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit (NO push)**

```bash
git add src/rutix/markdown/nutrition_weekly.py tests/unit/test_nutrition_weekly_md.py
git commit -m "feat(markdown): nutrition weekly aggregation"
```

---

## Task 7: flush_week orchestrator (Sunday → Monday morning)

**Files:**
- Create: `src/rutix/jobs/flush_week.py`
- Create: `tests/unit/test_flush_week.py`

Orchestrates the Sunday-end-of-week sync. Triggered every weekday morning at 03:00; runs only if `today.weekday() == 0` (Monday) AND the previous ISO week hasn't been flushed.

Steps:
1. Load week's daily files from GitHub (`daily/<Mon>.md` … `daily/<Sun>.md`)
2. Parse meals via `parse_meals` for each day → `NutritionDay` list
3. Load mood entries from SQLite for the week → for habits-counts in weekly metrics, parse habits from each daily file via a tiny helper
4. Read `habits.md` from GitHub → `HabitsConfig`
5. Render `weekly/2026-Wxx.md` and `nutrition/2026-Wxx.md`
6. Write both via GitHub API
7. Delete the 7 daily files via GitHub API
8. Purge SQLite mood_entries + medication_log for that week
9. Mark FlushLog `week:2026-Wxx`

- [ ] **Step 1: Write failing tests**

`tests/unit/test_flush_week.py`:

```python
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from rutix.db.models import FlushLog, MoodEntry
from rutix.integrations.github import FileContent
from rutix.jobs.flush_week import flush_week


HABITS_MD = """# Привычки

## Ежедневные

| Привычка | Prio | Зачем |
|----------|------|-------|
| 🥤 Protein | p2 | x |
| 📚 Anki | p3 | x |

## По расписанию

| Привычка | Prio | Дни | Зачем |
|----------|------|-----|-------|
| 🏋️ Strength | p2 | ВТ/ЧТ/СБ | x |
"""


def _daily(name: str = "test", with_meals: bool = False, habits_done: list[str] | None = None) -> str:
    habits_done = habits_done or []
    habit_lines = []
    for h in ("📚 Anki", "🥤 Protein", "🏋️ Strength"):
        marker = "x" if h in habits_done else " "
        habit_lines.append(f"- [{marker}] {h}")
    meals_block = "|  |  |  |  |  |  |\n| **Итого** |  |  |  |  |  |"
    if with_meals:
        meals_block = (
            "| Обед | Плов | 400 | 17 | 12 | 56 |\n"
            "| **Итого** |  | **400** | **17** | **12** | **56** |"
        )
    return f"""# {name}

## Сон
- Отбой:
- Подъём:

## Время (ч)
- VPN:
- Английский:

## Привычки

{chr(10).join(habit_lines)}

---

## Питание

| Приём | Что | Ккал | Б | Ж | У |
|-------|-----|------|---|---|---|
{meals_block}

---

## Что сделано

-

## Заметки

-
"""


@pytest.fixture
def fake_github():
    g = MagicMock()
    g.read = AsyncMock()
    g.write = AsyncMock(return_value="newsha")
    g.delete = AsyncMock(return_value="delsha")
    return g


async def test_flush_week_skips_when_not_monday(session, fake_github):
    sha = await flush_week(session, fake_github, today=date(2026, 5, 14))  # Thursday
    assert sha is None
    fake_github.read.assert_not_called()


async def test_flush_week_skips_when_already_flushed(session, fake_github):
    session.add(FlushLog(period_id="week:2026-W19", git_sha="x"))
    await session.commit()
    # Monday 2026-05-11 is in W20, but yesterday (Sun) was 2026-05-10 = W19
    sha = await flush_week(session, fake_github, today=date(2026, 5, 11))
    assert sha is None


async def test_flush_week_writes_files_and_deletes_daily(session, fake_github):
    # Monday 2026-05-11 → flush W19 (Mon 5-04 .. Sun 5-10)
    week_days = [date(2026, 5, d) for d in range(4, 11)]
    daily_contents = {
        f"daily/{d.isoformat()}.md": FileContent(text=_daily(str(d), with_meals=(i == 0), habits_done=["📚 Anki"]), sha=f"sha-{d}")
        for i, d in enumerate(week_days)
    }
    daily_contents["habits.md"] = FileContent(text=HABITS_MD, sha="habits-sha")

    async def fake_read(path):
        return daily_contents.get(path)

    fake_github.read.side_effect = fake_read

    # Add a MoodEntry that should get purged
    session.add(MoodEntry(day=date(2026, 5, 8), mood=1))
    await session.commit()

    sha = await flush_week(session, fake_github, today=date(2026, 5, 11))

    assert sha == "newsha"

    # Wrote weekly + nutrition
    write_paths = [c.args[0] if c.args else c.kwargs["path"] for c in fake_github.write.call_args_list]
    assert "weekly/2026-W19.md" in write_paths
    assert "nutrition/2026-W19.md" in write_paths

    # Deleted 7 daily files
    delete_paths = [c.args[0] if c.args else c.kwargs["path"] for c in fake_github.delete.call_args_list]
    for d in week_days:
        assert f"daily/{d.isoformat()}.md" in delete_paths
    assert len(delete_paths) == 7

    # Purged SQLite mood for that week
    remaining = await session.get(MoodEntry, date(2026, 5, 8))
    assert remaining is None

    # FlushLog marked
    log = await session.get(FlushLog, "week:2026-W19")
    assert log is not None
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `.venv/bin/pytest tests/unit/test_flush_week.py -v`
Expected: ImportError on `flush_week` (and possibly on github.delete — we add it next).

- [ ] **Step 3: Add `delete` method to GitHub client**

In `src/rutix/integrations/github.py`, add this method to `GitHubClient`:

```python
    async def delete(self, path: str, message: str, sha: str) -> str:
        """Delete a file. Returns the new commit SHA."""
        r = await self.http.request(
            "DELETE",
            f"/repos/{self.repo}/contents/{path}",
            json={"message": message, "sha": sha},
        )
        r.raise_for_status()
        return r.json()["commit"]["sha"]
```

Add a quick respx test in `tests/unit/test_github_client.py` (append to file):

```python
@respx.mock
async def test_delete_sends_sha(client):
    route = respx.delete("https://api.github.com/repos/quibex/life/contents/x.md").mock(
        return_value=httpx.Response(200, json={"commit": {"sha": "delsha"}})
    )
    result = await client.delete("x.md", "drop", sha="oldsha")
    assert result == "delsha"
    body = json.loads(route.calls[0].request.content)
    assert body["sha"] == "oldsha"
    await client.aclose()
```

Run: `.venv/bin/pytest tests/unit/test_github_client.py -v`
Expected: 6 passed.

- [ ] **Step 4: Implement `flush_week.py`**

`src/rutix/jobs/flush_week.py`:

```python
"""Weekly flush — runs Monday 03:00 to wrap up the just-finished week.

Idempotent via FlushLog `week:<id>`.
"""
import logging
import re
from datetime import date, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from rutix.db.models import FlushLog, MedicationLog, MoodEntry
from rutix.integrations.github import GitHubClient
from rutix.markdown.daily import parse_meals
from rutix.markdown.nutrition_weekly import NutritionDay, render_nutrition_weekly
from rutix.markdown.weekly import HabitsConfig, WeeklyDay, render_weekly
from rutix.time_utils import days_of_week, week_id, yesterday_of

logger = logging.getLogger(__name__)


_HABITS_DAILY_TABLE_RE = re.compile(
    r"## Ежедневные\s*\n\s*\n\| Привычка \|.*?\n\|[\s\-:|]+\|\n((?:\|.*\n)+)",
    re.DOTALL,
)
_HABITS_SCHED_TABLE_RE = re.compile(
    r"## По расписанию\s*\n\s*\n\| Привычка \|.*?\n\|[\s\-:|]+\|\n((?:\|.*\n)+)",
    re.DOTALL,
)


def _parse_habits_md(habits_md: str) -> HabitsConfig:
    daily_match = _HABITS_DAILY_TABLE_RE.search(habits_md)
    daily = []
    if daily_match:
        for row in daily_match.group(1).splitlines():
            cells = [c.strip() for c in row.split("|")]
            if len(cells) > 2 and cells[1]:
                daily.append(cells[1])

    scheduled = {}
    sched_match = _HABITS_SCHED_TABLE_RE.search(habits_md)
    if sched_match:
        for row in sched_match.group(1).splitlines():
            cells = [c.strip() for c in row.split("|")]
            if len(cells) > 4 and cells[1]:
                scheduled[cells[1]] = [d.strip() for d in cells[3].split("/")]

    return HabitsConfig(daily=daily, scheduled=scheduled)


_HABIT_LINE_RE = re.compile(r"^\s*-\s*\[([ x])\]\s*(.+?)\s*$")


def _parse_done_habits(daily_md: str) -> set[str]:
    done = set()
    in_habits = False
    for line in daily_md.splitlines():
        if line.startswith("## Привычки"):
            in_habits = True
            continue
        if in_habits and line.startswith("## "):
            break
        if in_habits:
            m = _HABIT_LINE_RE.match(line)
            if m and m.group(1) == "x":
                done.add(m.group(2))
    return done


async def flush_week(
    session: AsyncSession,
    github: GitHubClient,
    today: date,
) -> str | None:
    if today.weekday() != 0:  # Monday
        return None

    sunday = yesterday_of(today)
    wid = week_id(sunday)
    period_id = f"week:{wid}"

    if await session.get(FlushLog, period_id):
        logger.info("flush_week skipped — %s already flushed", period_id)
        return None

    week_dates = days_of_week(sunday)

    # Read daily files (some may be missing if user didn't have them)
    daily_contents: dict[date, str | None] = {}
    for d in week_dates:
        file = await github.read(f"daily/{d.isoformat()}.md")
        daily_contents[d] = file.text if file else None
        # We need SHA for delete later — re-fetch later or stash now:
    # Re-read with SHAs (one fetch each, simple)
    daily_files = {}
    for d in week_dates:
        f = await github.read(f"daily/{d.isoformat()}.md")
        daily_files[d] = f

    # Parse habits.md for the config
    habits_file = await github.read("habits.md")
    habits = _parse_habits_md(habits_file.text) if habits_file else HabitsConfig(daily=[], scheduled={})

    # Build WeeklyDay + NutritionDay arrays
    weekly_days: list[WeeklyDay] = []
    nutrition_days: list[NutritionDay] = []
    for d in week_dates:
        f = daily_files.get(d)
        if f is None:
            weekly_days.append(WeeklyDay(date=d, done_habits=set(), sleep_offh=None, sleep_onh=None, kcal=None))
            nutrition_days.append(NutritionDay(date=d, meals=[]))
            continue
        meals = parse_meals(f.text)
        kcal_total = sum(m.kcal for m in meals) if meals else None
        weekly_days.append(WeeklyDay(
            date=d, done_habits=_parse_done_habits(f.text),
            sleep_offh=None, sleep_onh=None, kcal=kcal_total,
        ))
        nutrition_days.append(NutritionDay(date=d, meals=meals))

    weekly_md = render_weekly(
        year=sunday.isocalendar().year, week_num=sunday.isocalendar().week,
        days=weekly_days, habits=habits,
    )
    nutrition_md = render_nutrition_weekly(
        year=sunday.isocalendar().year, week_num=sunday.isocalendar().week,
        days=nutrition_days,
    )

    weekly_path = f"weekly/{wid}.md"
    nutrition_path = f"nutrition/{wid}.md"

    weekly_existing = await github.read(weekly_path)
    weekly_sha = await github.write(
        weekly_path, weekly_md, f"weekly({wid}): авто-запись из rutix-bot",
        sha=weekly_existing.sha if weekly_existing else None,
    )
    nutrition_existing = await github.read(nutrition_path)
    await github.write(
        nutrition_path, nutrition_md, f"nutrition({wid}): авто-запись из rutix-bot",
        sha=nutrition_existing.sha if nutrition_existing else None,
    )

    # Delete daily files
    for d in week_dates:
        f = daily_files.get(d)
        if f is None:
            continue
        await github.delete(
            f"daily/{d.isoformat()}.md",
            f"daily({d.isoformat()}): cleanup after weekly flush",
            sha=f.sha,
        )

    # Purge SQLite for the week
    week_set = set(week_dates)
    await session.execute(delete(MoodEntry).where(MoodEntry.day.in_(week_set)))
    await session.execute(delete(MedicationLog).where(MedicationLog.day.in_(week_set)))

    session.add(FlushLog(period_id=period_id, git_sha=weekly_sha))
    await session.commit()
    logger.info("flush_week committed %s as %s", wid, weekly_sha)
    return weekly_sha
```

- [ ] **Step 5: Run tests, verify pass**

Run: `.venv/bin/pytest tests/unit/test_flush_week.py tests/unit/test_github_client.py -v`
Expected: all green.

- [ ] **Step 6: Commit (NO push)**

```bash
git add src/rutix/integrations/github.py src/rutix/jobs/flush_week.py tests/unit/test_flush_week.py tests/unit/test_github_client.py
git commit -m "feat(jobs): weekly flush — weekly + nutrition + cleanup; github delete()"
```

---

## Task 8: update_habits cron job

**Files:**
- Create: `src/rutix/jobs/update_habits.py`
- Create: `tests/unit/test_update_habits.py`

Daily 03:00: read Todoist Activity Log for yesterday → update yesterday's daily file Привычки section.

- [ ] **Step 1: Write failing tests**

`tests/unit/test_update_habits.py`:

```python
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from rutix.integrations.github import FileContent
from rutix.jobs.update_habits import update_habits


DAILY = """# 13 мая

## Привычки

- [ ] 📚 Anki
- [ ] 🌅 Skincare AM

---

## Питание

| Приём | Что | Ккал | Б | Ж | У |
|-------|-----|------|---|---|---|
|  |  |  |  |  |  |
| **Итого** |  |  |  |  |  |

## Заметки

-
"""


@pytest.fixture
def fake_github():
    g = MagicMock()
    g.read = AsyncMock(return_value=FileContent(text=DAILY, sha="oldsha"))
    g.write = AsyncMock(return_value="newsha")
    return g


@pytest.fixture
def fake_todoist():
    t = MagicMock()
    t.completed_titles_for_day = AsyncMock()
    return t


async def test_update_habits_marks_done_in_yesterday(fake_github, fake_todoist):
    fake_todoist.completed_titles_for_day.return_value = {"📚 Anki"}

    sha = await update_habits(fake_github, fake_todoist, day=date(2026, 5, 13))

    assert sha == "newsha"
    fake_github.read.assert_awaited_once_with("daily/2026-05-13.md")
    written_text = fake_github.write.call_args.args[1]
    assert "- [x] 📚 Anki" in written_text
    assert "- [ ] 🌅 Skincare AM" in written_text


async def test_update_habits_skips_when_no_completions(fake_github, fake_todoist):
    fake_todoist.completed_titles_for_day.return_value = set()

    sha = await update_habits(fake_github, fake_todoist, day=date(2026, 5, 13))
    assert sha is None
    fake_github.write.assert_not_called()


async def test_update_habits_skips_when_no_change(fake_github, fake_todoist):
    """If all matching habits are already checked, no write."""
    pre = DAILY.replace("- [ ] 📚 Anki", "- [x] 📚 Anki")
    fake_github.read = AsyncMock(return_value=FileContent(text=pre, sha="x"))
    fake_todoist.completed_titles_for_day.return_value = {"📚 Anki"}

    sha = await update_habits(fake_github, fake_todoist, day=date(2026, 5, 13))
    assert sha is None
    fake_github.write.assert_not_called()


async def test_update_habits_skips_when_daily_missing(fake_github, fake_todoist):
    fake_github.read = AsyncMock(return_value=None)

    sha = await update_habits(fake_github, fake_todoist, day=date(2026, 5, 13))
    assert sha is None
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `.venv/bin/pytest tests/unit/test_update_habits.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `update_habits.py`**

`src/rutix/jobs/update_habits.py`:

```python
"""Daily 03:00 cron — fetch yesterday's Todoist completions, mark matching
habits in yesterday's daily/*.md."""
import logging
from datetime import date

from rutix.integrations.github import GitHubClient
from rutix.integrations.todoist import TodoistClient
from rutix.markdown.daily import update_habits_checked

logger = logging.getLogger(__name__)


async def update_habits(
    github: GitHubClient,
    todoist: TodoistClient,
    day: date,
) -> str | None:
    """Returns commit SHA if a write happened, None otherwise."""
    done = await todoist.completed_titles_for_day(day)
    if not done:
        logger.info("update_habits skipped — no completions for %s", day)
        return None

    path = f"daily/{day.isoformat()}.md"
    file = await github.read(path)
    if file is None:
        logger.warning("update_habits skipped — no daily file for %s", day)
        return None

    new_text = update_habits_checked(file.text, done)
    if new_text == file.text:
        logger.info("update_habits no-op — habits already checked for %s", day)
        return None

    sha = await github.write(
        path, new_text,
        f"habits({day.isoformat()}): авто-запись из rutix-bot (Todoist)",
        sha=file.sha,
    )
    logger.info("update_habits committed %s as %s", day, sha)
    return sha
```

- [ ] **Step 4: Run tests, verify pass**

Run: `.venv/bin/pytest tests/unit/test_update_habits.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit (NO push)**

```bash
git add src/rutix/jobs/update_habits.py tests/unit/test_update_habits.py
git commit -m "feat(jobs): update_habits — daily Todoist → daily.md checkboxes"
```

---

## Task 9: /eat handler

**Files:**
- Create: `src/rutix/bot/handlers/eat.py`
- Create: `tests/unit/test_eat_handler.py`

`/eat <text>` → Claude parse → write to today's daily Питание (creating items
with the slot determined by current local time) → reply with summary + Итого.

- [ ] **Step 1: Write failing tests**

`tests/unit/test_eat_handler.py`:

```python
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

from rutix.bot.handlers.eat import _slot_for_time, cmd_eat
from rutix.integrations.github import FileContent
from rutix.markdown.daily import MealItem


MSK = ZoneInfo("Europe/Moscow")
DAILY = """# x

## Привычки
- [ ]
---
## Питание

| Приём | Что | Ккал | Б | Ж | У |
|-------|-----|------|---|---|---|
|  |  |  |  |  |  |
| **Итого** |  |  |  |  |  |

---

## Что сделано
-
## Заметки
-
"""


def test_slot_for_time_breakfast():
    assert _slot_for_time(datetime(2026, 5, 14, 9, 0, tzinfo=MSK)) == "Завтрак"


def test_slot_for_time_lunch():
    assert _slot_for_time(datetime(2026, 5, 14, 13, 0, tzinfo=MSK)) == "Обед"


def test_slot_for_time_dinner():
    assert _slot_for_time(datetime(2026, 5, 14, 19, 0, tzinfo=MSK)) == "Ужин"


def test_slot_for_time_snack():
    assert _slot_for_time(datetime(2026, 5, 14, 23, 30, tzinfo=MSK)) == "Перекус"


@pytest.fixture
def fake_settings():
    s = MagicMock()
    s.tz = "Europe/Moscow"
    return s


@pytest.fixture
def fake_github():
    g = MagicMock()
    g.read = AsyncMock()
    g.write = AsyncMock(return_value="newsha")
    return g


@pytest.fixture
def fake_claude():
    c = MagicMock()
    c.parse_eat = AsyncMock()
    return c


@pytest.fixture
def fake_message():
    m = MagicMock()
    m.text = "/eat шаурма"
    m.reply = AsyncMock()
    m.answer = AsyncMock()
    return m


async def test_cmd_eat_writes_to_daily_and_replies(
    fake_message, fake_github, fake_claude, fake_settings, monkeypatch
):
    # Reference is fetched on first call
    fake_github.read.side_effect = [
        FileContent(text="ref content", sha="rsha"),  # nutrition/reference.md
        FileContent(text=DAILY, sha="dsha"),           # daily/<today>.md
    ]
    fake_claude.parse_eat.return_value = [
        MealItem("", "Шаурма", 450, 22.0, 18.0, 45.0)
    ]

    fake_message.text = "/eat шаурма"

    await cmd_eat(
        fake_message, settings=fake_settings, github=fake_github, claude=fake_claude
    )

    # GitHub write called with updated Питание containing the row
    write_args = fake_github.write.call_args
    written_text = write_args.args[1]
    assert "Шаурма" in written_text
    assert "450" in written_text

    # Reply mentions added items + total
    fake_message.answer.assert_awaited()
    reply_text = fake_message.answer.call_args.args[0]
    assert "Шаурма" in reply_text
    assert "450" in reply_text


async def test_cmd_eat_replies_with_error_if_claude_fails(
    fake_message, fake_github, fake_claude, fake_settings
):
    fake_github.read.return_value = FileContent(text="ref", sha="x")
    fake_claude.parse_eat.side_effect = ValueError("bad json")

    fake_message.text = "/eat что-то непонятное"

    await cmd_eat(
        fake_message, settings=fake_settings, github=fake_github, claude=fake_claude
    )

    fake_github.write.assert_not_called()
    reply_text = fake_message.answer.call_args.args[0]
    assert "не смог" in reply_text.lower() or "ошибка" in reply_text.lower()


async def test_cmd_eat_replies_with_help_when_no_args(
    fake_message, fake_github, fake_claude, fake_settings
):
    fake_message.text = "/eat"

    await cmd_eat(
        fake_message, settings=fake_settings, github=fake_github, claude=fake_claude
    )

    fake_github.write.assert_not_called()
    fake_claude.parse_eat.assert_not_called()
    reply_text = fake_message.answer.call_args.args[0]
    assert "/eat" in reply_text
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `.venv/bin/pytest tests/unit/test_eat_handler.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `eat.py`**

`src/rutix/bot/handlers/eat.py`:

```python
"""/eat <text> — Claude parses, bot writes to today's daily Питание."""
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from rutix.integrations.claude import ClaudeClient
from rutix.integrations.github import GitHubClient
from rutix.markdown.daily import append_meal
from rutix.settings import Settings
from rutix.time_utils import subjective_today

logger = logging.getLogger(__name__)

router = Router(name="eat")

REFERENCE_PATH = "nutrition/reference.md"


def _slot_for_time(now: datetime) -> str:
    h = now.hour
    if 8 <= h <= 11:
        return "Завтрак"
    if 12 <= h <= 16:
        return "Обед"
    if 17 <= h <= 21:
        return "Ужин"
    return "Перекус"


def _format_kbju(kcal: int, p: float, f: float, c: float) -> str:
    return f"{kcal} ккал · Б{p:g} Ж{f:g} У{c:g}"


@router.message(Command("eat"))
async def cmd_eat(
    message: Message,
    settings: Settings,
    github: GitHubClient,
    claude: ClaudeClient,
):
    raw = (message.text or "").split(maxsplit=1)
    if len(raw) < 2 or not raw[1].strip():
        await message.answer(
            "Использование: /eat <что съел>\nПример: /eat шаурма + кола"
        )
        return

    food_text = raw[1].strip()
    now = datetime.now(ZoneInfo(settings.tz))
    day = subjective_today(now, settings.tz)
    slot = _slot_for_time(now)

    # Fetch reference + daily file
    reference = await github.read(REFERENCE_PATH)
    reference_text = reference.text if reference else ""

    daily_path = f"daily/{day.isoformat()}.md"
    daily_file = await github.read(daily_path)
    if daily_file is None:
        await message.answer(f"❌ Нет файла {daily_path}. Создай его сначала в Obsidian.")
        return

    # Parse via Claude
    try:
        items = await claude.parse_eat(food_text, reference_md=reference_text)
    except ValueError as e:
        logger.exception("Claude parse failed")
        await message.answer(f"❌ Не смог распарсить: {e}. Попробуй переписать.")
        return

    if not items:
        await message.answer("⚠️ Claude вернул пустой список. Попробуй уточнить.")
        return

    # Apply slot to all items + append to daily
    new_text = daily_file.text
    for item in items:
        item.slot = slot
        new_text = append_meal(new_text, item)

    sha = await github.write(
        daily_path, new_text,
        f"eat({day.isoformat()}): {food_text[:60]}",
        sha=daily_file.sha,
    )

    # Build reply
    added_lines = [
        f"• {it.name} — {_format_kbju(it.kcal, it.protein, it.fat, it.carbs)}"
        for it in items
    ]
    total_kcal = sum(it.kcal for it in items)
    total_p = sum(it.protein for it in items)
    total_f = sum(it.fat for it in items)
    total_c = sum(it.carbs for it in items)
    reply = (
        f"✅ Добавил в {slot}:\n" + "\n".join(added_lines) +
        f"\n\nИтого добавлено: {_format_kbju(total_kcal, total_p, total_f, total_c)}\n"
        f"Файл: {sha[:7]}"
    )
    await message.answer(reply)
```

- [ ] **Step 4: Run tests, verify pass**

Run: `.venv/bin/pytest tests/unit/test_eat_handler.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit (NO push)**

```bash
git add src/rutix/bot/handlers/eat.py tests/unit/test_eat_handler.py
git commit -m "feat(bot): /eat — Claude parse + write to daily Питание"
```

---

## Task 10: /note + /done handlers (bundled)

**Files:**
- Create: `src/rutix/bot/handlers/note_done.py`
- Create: `tests/unit/test_note_done_handlers.py`

`/note <text>` → append to today's daily Заметки. `/done <text>` → append to today's daily "Что сделано". Same shape — bundled in one file with one router.

- [ ] **Step 1: Write failing tests**

`tests/unit/test_note_done_handlers.py`:

```python
from unittest.mock import AsyncMock, MagicMock

import pytest

from rutix.bot.handlers.note_done import cmd_done, cmd_note
from rutix.integrations.github import FileContent


DAILY = """# x

## Привычки
- [ ]

## Питание

| Приём | Что | Ккал | Б | Ж | У |
|-------|-----|------|---|---|---|
|  |  |  |  |  |  |
| **Итого** |  |  |  |  |  |

---

## Что сделано

- existing done

## Заметки

- existing note
"""


@pytest.fixture
def fake_settings():
    s = MagicMock(); s.tz = "Europe/Moscow"; return s


@pytest.fixture
def fake_github():
    g = MagicMock()
    g.read = AsyncMock(return_value=FileContent(text=DAILY, sha="x"))
    g.write = AsyncMock(return_value="newsha")
    return g


@pytest.fixture
def fake_message():
    m = MagicMock()
    m.answer = AsyncMock()
    return m


async def test_cmd_note_appends_to_notes(fake_message, fake_settings, fake_github):
    fake_message.text = "/note важная мысль"
    await cmd_note(fake_message, settings=fake_settings, github=fake_github)

    written = fake_github.write.call_args.args[1]
    notes = written.split("## Заметки", 1)[1]
    assert "- existing note" in notes
    assert "- важная мысль" in notes
    fake_message.answer.assert_awaited()


async def test_cmd_done_appends_to_done(fake_message, fake_settings, fake_github):
    fake_message.text = "/done закрыл задачу"
    await cmd_done(fake_message, settings=fake_settings, github=fake_github)

    written = fake_github.write.call_args.args[1]
    done = written.split("## Что сделано", 1)[1].split("## Заметки", 1)[0]
    assert "- existing done" in done
    assert "- закрыл задачу" in done


async def test_cmd_note_no_args_shows_usage(fake_message, fake_settings, fake_github):
    fake_message.text = "/note"
    await cmd_note(fake_message, settings=fake_settings, github=fake_github)
    fake_github.write.assert_not_called()
    assert "/note" in fake_message.answer.call_args.args[0]


async def test_cmd_done_no_args_shows_usage(fake_message, fake_settings, fake_github):
    fake_message.text = "/done"
    await cmd_done(fake_message, settings=fake_settings, github=fake_github)
    fake_github.write.assert_not_called()


async def test_cmd_note_when_daily_missing(fake_message, fake_settings, fake_github):
    fake_github.read = AsyncMock(return_value=None)
    fake_message.text = "/note hi"
    await cmd_note(fake_message, settings=fake_settings, github=fake_github)
    fake_github.write.assert_not_called()
    assert "нет файла" in fake_message.answer.call_args.args[0].lower()
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `.venv/bin/pytest tests/unit/test_note_done_handlers.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `note_done.py`**

`src/rutix/bot/handlers/note_done.py`:

```python
"""/note and /done — append a bullet to today's daily Заметки / Что сделано."""
import logging
from datetime import datetime
from typing import Callable
from zoneinfo import ZoneInfo

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from rutix.integrations.github import GitHubClient
from rutix.markdown.daily import append_done, append_note
from rutix.settings import Settings
from rutix.time_utils import subjective_today

logger = logging.getLogger(__name__)

router = Router(name="note_done")


async def _append_to_daily(
    message: Message,
    settings: Settings,
    github: GitHubClient,
    cmd_name: str,
    section_label: str,
    appender: Callable[[str, str], str],
):
    raw = (message.text or "").split(maxsplit=1)
    if len(raw) < 2 or not raw[1].strip():
        await message.answer(f"Использование: /{cmd_name} <текст>")
        return

    text = raw[1].strip()
    day = subjective_today(datetime.now(ZoneInfo(settings.tz)), settings.tz)
    path = f"daily/{day.isoformat()}.md"

    file = await github.read(path)
    if file is None:
        await message.answer(f"❌ Нет файла {path}. Создай его сначала в Obsidian.")
        return

    new_text = appender(file.text, text)
    if new_text == file.text:
        await message.answer("⏭ Без изменений")
        return

    sha = await github.write(
        path, new_text,
        f"{cmd_name}({day.isoformat()}): {text[:60]}",
        sha=file.sha,
    )
    await message.answer(f"✅ Добавил в «{section_label}» ({sha[:7]})")


@router.message(Command("note"))
async def cmd_note(message: Message, settings: Settings, github: GitHubClient):
    await _append_to_daily(message, settings, github, "note", "Заметки", append_note)


@router.message(Command("done"))
async def cmd_done(message: Message, settings: Settings, github: GitHubClient):
    await _append_to_daily(message, settings, github, "done", "Что сделано", append_done)
```

- [ ] **Step 4: Run tests, verify pass**

Run: `.venv/bin/pytest tests/unit/test_note_done_handlers.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit (NO push)**

```bash
git add src/rutix/bot/handlers/note_done.py tests/unit/test_note_done_handlers.py
git commit -m "feat(bot): /note + /done — append bullets to daily file"
```

---

## Task 11: /today handler

**Files:**
- Create: `src/rutix/bot/handlers/today.py`
- Create: `tests/unit/test_today_handler.py`

`/today` reads SQLite mood entry + today's daily-file Питание section, replies with a compact summary.

- [ ] **Step 1: Write failing tests**

`tests/unit/test_today_handler.py`:

```python
from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest
from freezegun import freeze_time

from rutix.bot.handlers.today import cmd_today
from rutix.db.models import MoodEntry
from rutix.integrations.github import FileContent


@pytest.fixture
def fake_settings():
    s = MagicMock(); s.tz = "Europe/Moscow"; return s


@pytest.fixture
def fake_github():
    g = MagicMock()
    g.read = AsyncMock()
    return g


@pytest.fixture
def fake_message():
    m = MagicMock(); m.answer = AsyncMock(); return m


@freeze_time("2026-05-14 12:00:00", tz_offset=3)
async def test_today_shows_mood_and_meals(fake_message, fake_settings, fake_github, session):
    session.add(MoodEntry(
        day=date(2026, 5, 14), mood=1, anxiety=0, irritability=0, sleep_hours=7.5,
    ))
    await session.commit()

    daily = """# x

## Питание

| Приём | Что | Ккал | Б | Ж | У |
|-------|-----|------|---|---|---|
| Завтрак | Яйца | 200 | 14 | 14 | 2 |
| **Итого** |  | **200** | **14** | **14** | **2** |

## Что сделано
-
## Заметки
-
"""
    fake_github.read.return_value = FileContent(text=daily, sha="x")

    async def session_factory_call():
        class CM:
            async def __aenter__(self_inner):
                return session
            async def __aexit__(self_inner, *a):
                pass
        return CM()
    sf = MagicMock(side_effect=lambda: session_factory_call())

    await cmd_today(
        fake_message,
        settings=fake_settings,
        github=fake_github,
        session_factory=sf,
    )

    reply = fake_message.answer.call_args.args[0]
    assert "+1" in reply or "1" in reply  # mood
    assert "7.5" in reply
    assert "200" in reply  # kcal


@freeze_time("2026-05-14 12:00:00", tz_offset=3)
async def test_today_when_no_mood_entry(fake_message, fake_settings, fake_github, session):
    daily = "## Питание\n\n| Приём | Что | Ккал | Б | Ж | У |\n|---|---|---|---|---|---|\n|  |  |  |  |  |  |\n| **Итого** |  |  |  |  |  |\n\n## Что сделано\n-\n## Заметки\n-\n"
    fake_github.read.return_value = FileContent(text=daily, sha="x")

    async def session_factory_call():
        class CM:
            async def __aenter__(self_inner):
                return session
            async def __aexit__(self_inner, *a):
                pass
        return CM()
    sf = MagicMock(side_effect=lambda: session_factory_call())

    await cmd_today(
        fake_message,
        settings=fake_settings,
        github=fake_github,
        session_factory=sf,
    )

    reply = fake_message.answer.call_args.args[0]
    assert "не делал" in reply.lower() or "/track" in reply.lower()
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `.venv/bin/pytest tests/unit/test_today_handler.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `today.py`**

`src/rutix/bot/handlers/today.py`:

```python
"""/today — show today's mood (SQLite) + meals (GitHub) summary."""
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rutix.db.models import MoodEntry
from rutix.integrations.github import GitHubClient
from rutix.markdown.daily import parse_meals
from rutix.settings import Settings
from rutix.time_utils import subjective_today

logger = logging.getLogger(__name__)

router = Router(name="today")


@router.message(Command("today"))
async def cmd_today(
    message: Message,
    settings: Settings,
    github: GitHubClient,
    session_factory: async_sessionmaker[AsyncSession],
):
    day = subjective_today(datetime.now(ZoneInfo(settings.tz)), settings.tz)

    async with session_factory() as session:
        mood = await session.get(MoodEntry, day)

    file = await github.read(f"daily/{day.isoformat()}.md")
    meals = parse_meals(file.text) if file else []

    lines = [f"📆 {day.isoformat()}\n"]

    if mood is None:
        lines.append("📊 Трек ещё не делал — /track")
    else:
        mood_str = f"+{mood.mood}" if mood.mood and mood.mood > 0 else str(mood.mood) if mood.mood is not None else "—"
        lines.append(
            f"📊 Настр. {mood_str} · тревога {mood.anxiety} · "
            f"раздр. {mood.irritability} · сон {mood.sleep_hours}ч"
        )

    if meals:
        kcal = sum(m.kcal for m in meals)
        p = sum(m.protein for m in meals)
        f = sum(m.fat for m in meals)
        c = sum(m.carbs for m in meals)
        lines.append(
            f"\n🍽 Итого за день: {kcal} ккал · Б{p:g} Ж{f:g} У{c:g}\n"
            + "\n".join(f"• {m.name} — {m.kcal}" for m in meals)
        )
    else:
        lines.append("\n🍽 Ничего не ел сегодня — /eat <что>")

    await message.answer("\n".join(lines))
```

- [ ] **Step 4: Run tests, verify pass**

Run: `.venv/bin/pytest tests/unit/test_today_handler.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit (NO push)**

```bash
git add src/rutix/bot/handlers/today.py tests/unit/test_today_handler.py
git commit -m "feat(bot): /today — mood + meals summary"
```

---

## Task 12: /week handler — 7-day buttons + per-day report

**Files:**
- Create: `src/rutix/bot/handlers/week.py`
- Create: `tests/unit/test_week_handler.py`

`/week` shows 7 inline buttons (current ISO week, Mon..Sun, with date labels). Tap a day → bot shows the same content as `/today` for that day.

Callback data: `week_day:YYYY-MM-DD`.

- [ ] **Step 1: Write failing tests**

`tests/unit/test_week_handler.py`:

```python
from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest
from freezegun import freeze_time

from rutix.bot.handlers.week import cb_week_day, cmd_week
from rutix.db.models import MoodEntry
from rutix.integrations.github import FileContent


@pytest.fixture
def fake_settings():
    s = MagicMock(); s.tz = "Europe/Moscow"; return s


@pytest.fixture
def fake_github():
    g = MagicMock(); g.read = AsyncMock(); return g


@pytest.fixture
def fake_message():
    m = MagicMock(); m.answer = AsyncMock(); return m


@freeze_time("2026-05-14 12:00:00", tz_offset=3)
async def test_cmd_week_shows_7_buttons(fake_message, fake_settings):
    await cmd_week(fake_message, settings=fake_settings)

    fake_message.answer.assert_awaited()
    kw = fake_message.answer.call_args.kwargs
    kb = kw["reply_markup"]
    flat = [b for row in kb.inline_keyboard for b in row]
    assert len(flat) == 7
    callback_dates = [b.callback_data.split(":")[1] for b in flat]
    # Week 20 of 2026: Mon May 11 .. Sun May 17
    assert callback_dates == [
        "2026-05-11", "2026-05-12", "2026-05-13", "2026-05-14",
        "2026-05-15", "2026-05-16", "2026-05-17",
    ]


@freeze_time("2026-05-14 12:00:00", tz_offset=3)
async def test_cb_week_day_replies_with_day_summary(
    fake_settings, fake_github, session,
):
    session.add(MoodEntry(day=date(2026, 5, 13), mood=2, anxiety=0, irritability=1, sleep_hours=8))
    await session.commit()

    daily = "## Питание\n\n| Приём | Что | Ккал | Б | Ж | У |\n|---|---|---|---|---|---|\n| Обед | Плов | 400 | 17 | 12 | 56 |\n| **Итого** |  | **400** | **17** | **12** | **56** |\n"
    fake_github.read.return_value = FileContent(text=daily, sha="x")

    cb = MagicMock()
    cb.data = "week_day:2026-05-13"
    cb.message = MagicMock()
    cb.message.edit_text = AsyncMock()
    cb.answer = AsyncMock()

    async def session_factory_call():
        class CM:
            async def __aenter__(self_inner): return session
            async def __aexit__(self_inner, *a): pass
        return CM()
    sf = MagicMock(side_effect=lambda: session_factory_call())

    await cb_week_day(cb, settings=fake_settings, github=fake_github, session_factory=sf)

    cb.message.edit_text.assert_awaited()
    text = cb.message.edit_text.call_args.args[0]
    assert "2026-05-13" in text
    assert "+2" in text or "2" in text
    assert "400" in text
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `.venv/bin/pytest tests/unit/test_week_handler.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `week.py`**

`src/rutix/bot/handlers/week.py`:

```python
"""/week — 7 buttons (current ISO week) → day summary on tap."""
import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rutix.db.models import MoodEntry
from rutix.integrations.github import GitHubClient
from rutix.markdown.daily import parse_meals
from rutix.settings import Settings
from rutix.time_utils import days_of_week, subjective_today

logger = logging.getLogger(__name__)

router = Router(name="week")

DAY_LABELS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


def _build_keyboard(days: list[date]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text=f"{DAY_LABELS[d.weekday()]} {d.day}",
                callback_data=f"week_day:{d.isoformat()}",
            )
            for d in days
        ]]
    )


@router.message(Command("week"))
async def cmd_week(message: Message, settings: Settings):
    today = subjective_today(datetime.now(ZoneInfo(settings.tz)), settings.tz)
    week = days_of_week(today)
    await message.answer(
        f"📅 Неделя {week[0].isoformat()} — {week[-1].isoformat()}",
        reply_markup=_build_keyboard(week),
    )


@router.callback_query(F.data.startswith("week_day:"))
async def cb_week_day(
    cb: CallbackQuery,
    settings: Settings,
    github: GitHubClient,
    session_factory: async_sessionmaker[AsyncSession],
):
    day = date.fromisoformat(cb.data.split(":", 1)[1])

    async with session_factory() as session:
        mood = await session.get(MoodEntry, day)

    file = await github.read(f"daily/{day.isoformat()}.md")
    meals = parse_meals(file.text) if file else []

    lines = [f"📆 {day.isoformat()}\n"]

    if mood is None:
        lines.append("📊 Трек не сделан")
    else:
        mood_str = f"+{mood.mood}" if mood.mood and mood.mood > 0 else str(mood.mood) if mood.mood is not None else "—"
        lines.append(
            f"📊 {mood_str} · трев {mood.anxiety} · разд {mood.irritability} · сон {mood.sleep_hours}ч"
        )

    if meals:
        kcal = sum(m.kcal for m in meals)
        lines.append(f"\n🍽 {kcal} ккал — {len(meals)} приёмов")
    else:
        lines.append("\n🍽 Пусто")

    await cb.message.edit_text("\n".join(lines))
    await cb.answer()
```

- [ ] **Step 4: Run tests, verify pass**

Run: `.venv/bin/pytest tests/unit/test_week_handler.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit (NO push)**

```bash
git add src/rutix/bot/handlers/week.py tests/unit/test_week_handler.py
git commit -m "feat(bot): /week — 7 day buttons + per-day summary"
```

---

## Task 13: /meds handler — list/add/archive/dose (FSM)

**Files:**
- Create: `src/rutix/bot/handlers/meds.py`
- Create: `tests/unit/test_meds_handler.py`

`/meds` shows the current active protocol with three action buttons:
- ➕ Добавить → FSM: key (slug) → name → column_label → dose → started_at default today → save
- 📦 Архивировать <key> → set archived_at = today
- ✏️ Доза <key> → ask new dose → save

For Phase 2 we keep this minimal — just enough to manage the protocol from the bot. Test the happy paths; deep edge cases can wait.

- [ ] **Step 1: Write failing tests**

`tests/unit/test_meds_handler.py`:

```python
from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from freezegun import freeze_time

from rutix.bot.handlers.meds import cmd_meds, MedsStates
from rutix.db.models import MedActive


@pytest.fixture
def fake_settings():
    s = MagicMock(); s.tz = "Europe/Moscow"; return s


@pytest.fixture
def fake_message():
    m = MagicMock(); m.answer = AsyncMock(); return m


async def test_cmd_meds_lists_active(fake_message, fake_settings, session):
    session.add(MedActive(
        key="seizar", name="Сейзар", column_label="Сейзар",
        current_dose="25", started_at=date(2026, 4, 26),
    ))
    await session.commit()

    async def session_factory_call():
        class CM:
            async def __aenter__(self_inner): return session
            async def __aexit__(self_inner, *a): pass
        return CM()
    sf = MagicMock(side_effect=lambda: session_factory_call())

    await cmd_meds(fake_message, settings=fake_settings, session_factory=sf)

    text = fake_message.answer.call_args.args[0]
    assert "Сейзар" in text
    assert "25" in text
    # Buttons present
    kb = fake_message.answer.call_args.kwargs["reply_markup"]
    labels = [b.text for row in kb.inline_keyboard for b in row]
    assert any("Добавить" in l for l in labels)
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `.venv/bin/pytest tests/unit/test_meds_handler.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `meds.py`**

`src/rutix/bot/handlers/meds.py`:

```python
"""/meds — list/add/archive/change-dose for active medication protocol."""
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

from rutix.db.models import MedActive
from rutix.settings import Settings

logger = logging.getLogger(__name__)

router = Router(name="meds")


class MedsStates(StatesGroup):
    add_key = State()
    add_name = State()
    add_label = State()
    add_dose = State()
    edit_dose_value = State()


def _menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="➕ Добавить", callback_data="meds:add"),
        InlineKeyboardButton(text="📦 Архив", callback_data="meds:archive_pick"),
        InlineKeyboardButton(text="✏️ Доза", callback_data="meds:dose_pick"),
    ]])


def _picklist_kb(meds: list[MedActive], action: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"{m.name} ({m.current_dose})", callback_data=f"meds:{action}:{m.key}")]
        for m in meds
    ]
    rows.append([InlineKeyboardButton(text="← Отмена", callback_data="meds:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _format_list(meds: list[MedActive]) -> str:
    if not meds:
        return "🩺 Активных препаратов нет"
    rows = [f"• {m.name} — {m.current_dose} мг (с {m.started_at.isoformat()})" for m in meds]
    return "🩺 Активные:\n" + "\n".join(rows)


@router.message(Command("meds"))
async def cmd_meds(
    message: Message,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
):
    async with session_factory() as session:
        meds = (await session.scalars(
            select(MedActive).where(MedActive.archived_at.is_(None)).order_by(MedActive.started_at)
        )).all()
    await message.answer(_format_list(meds), reply_markup=_menu_kb())


@router.callback_query(F.data == "meds:cancel")
async def cb_cancel(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.edit_text("Отменено")
    await cb.answer()


# --- Add flow ---

@router.callback_query(F.data == "meds:add")
async def cb_add(cb: CallbackQuery, state: FSMContext):
    await state.set_state(MedsStates.add_key)
    await cb.message.edit_text("Введи короткий ключ (slug, например `seizar`):")
    await cb.answer()


@router.message(MedsStates.add_key, F.text)
async def msg_add_key(message: Message, state: FSMContext):
    await state.update_data(key=message.text.strip())
    await state.set_state(MedsStates.add_name)
    await message.answer("Полное название (например `Сейзар`):")


@router.message(MedsStates.add_name, F.text)
async def msg_add_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await state.set_state(MedsStates.add_label)
    await message.answer("Заголовок колонки в mood_tracker (например `Сейзар` или `Гидр.К`):")


@router.message(MedsStates.add_label, F.text)
async def msg_add_label(message: Message, state: FSMContext):
    await state.update_data(label=message.text.strip())
    await state.set_state(MedsStates.add_dose)
    await message.answer("Текущая доза (например `25` или `12.5`):")


@router.message(MedsStates.add_dose, F.text)
async def msg_add_dose(
    message: Message, state: FSMContext, settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
):
    data = await state.get_data()
    today = datetime.now(ZoneInfo(settings.tz)).date()
    async with session_factory() as session:
        session.add(MedActive(
            key=data["key"], name=data["name"], column_label=data["label"],
            current_dose=message.text.strip(), started_at=today,
        ))
        await session.commit()
    await state.clear()
    await message.answer(f"✅ Добавил {data['name']} ({message.text.strip()})")


# --- Archive flow ---

@router.callback_query(F.data == "meds:archive_pick")
async def cb_archive_pick(
    cb: CallbackQuery, session_factory: async_sessionmaker[AsyncSession],
):
    async with session_factory() as session:
        meds = (await session.scalars(
            select(MedActive).where(MedActive.archived_at.is_(None))
        )).all()
    if not meds:
        await cb.message.edit_text("Нет активных")
        await cb.answer()
        return
    await cb.message.edit_text("Какой архивировать?", reply_markup=_picklist_kb(meds, "archive"))
    await cb.answer()


@router.callback_query(F.data.startswith("meds:archive:"))
async def cb_archive_apply(
    cb: CallbackQuery, settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
):
    key = cb.data.split(":", 2)[2]
    today = datetime.now(ZoneInfo(settings.tz)).date()
    async with session_factory() as session:
        med = await session.get(MedActive, key)
        if med:
            med.archived_at = today
            await session.commit()
            await cb.message.edit_text(f"📦 Архивировал {med.name}")
        else:
            await cb.message.edit_text("Не нашёл")
    await cb.answer()


# --- Dose flow ---

@router.callback_query(F.data == "meds:dose_pick")
async def cb_dose_pick(
    cb: CallbackQuery, session_factory: async_sessionmaker[AsyncSession],
):
    async with session_factory() as session:
        meds = (await session.scalars(
            select(MedActive).where(MedActive.archived_at.is_(None))
        )).all()
    if not meds:
        await cb.message.edit_text("Нет активных")
        await cb.answer()
        return
    await cb.message.edit_text("Кому менять дозу?", reply_markup=_picklist_kb(meds, "dose"))
    await cb.answer()


@router.callback_query(F.data.startswith("meds:dose:"))
async def cb_dose_pick_med(cb: CallbackQuery, state: FSMContext):
    key = cb.data.split(":", 2)[2]
    await state.update_data(dose_key=key)
    await state.set_state(MedsStates.edit_dose_value)
    await cb.message.edit_text("Новая доза:")
    await cb.answer()


@router.message(MedsStates.edit_dose_value, F.text)
async def msg_dose_value(
    message: Message, state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
):
    data = await state.get_data()
    async with session_factory() as session:
        med = await session.get(MedActive, data["dose_key"])
        if med:
            med.current_dose = message.text.strip()
            await session.commit()
            await message.answer(f"✅ {med.name}: {med.current_dose}")
        else:
            await message.answer("Не нашёл")
    await state.clear()
```

- [ ] **Step 4: Run tests, verify pass**

Run: `.venv/bin/pytest tests/unit/test_meds_handler.py -v`
Expected: 1 passed (the list smoke test). Add-flow / archive-flow / dose-flow tests intentionally minimal — exercise via real bot in smoke test.

- [ ] **Step 5: Commit (NO push)**

```bash
git add src/rutix/bot/handlers/meds.py tests/unit/test_meds_handler.py
git commit -m "feat(bot): /meds — list/add/archive/dose FSM"
```

---

## Task 14: Wire schedulers — add update_habits + flush_week

**Files:**
- Modify: `src/rutix/jobs/scheduler.py`

- [ ] **Step 1: Replace `scheduler.py`**

`src/rutix/jobs/scheduler.py`:

```python
"""APScheduler — daily 03:00 jobs:
- flush_day(yesterday)        — Phase 1
- update_habits(yesterday)    — Phase 2
- flush_week(today)           — Phase 2 (Monday-only check inside)
"""
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rutix.integrations.github import GitHubClient
from rutix.integrations.todoist import TodoistClient
from rutix.jobs.flush_day import flush_day
from rutix.jobs.flush_week import flush_week
from rutix.jobs.update_habits import update_habits
from rutix.time_utils import subjective_today, yesterday_of

logger = logging.getLogger(__name__)


def make_scheduler(
    session_factory: async_sessionmaker[AsyncSession],
    github: GitHubClient,
    todoist: TodoistClient,
    tz: str,
) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=ZoneInfo(tz))

    async def daily_3am():
        today = subjective_today(datetime.now(ZoneInfo(tz)), tz)
        target = yesterday_of(today)
        logger.info("3am job running for target=%s today=%s", target, today)

        async with session_factory() as session:
            try:
                sha = await flush_day(session, github, target)
                logger.info("flush_day result: %s", sha)
            except Exception:
                logger.exception("flush_day failed")

        try:
            sha = await update_habits(github, todoist, target)
            logger.info("update_habits result: %s", sha)
        except Exception:
            logger.exception("update_habits failed")

        async with session_factory() as session:
            try:
                sha = await flush_week(session, github, today)
                logger.info("flush_week result: %s", sha)
            except Exception:
                logger.exception("flush_week failed")

    scheduler.add_job(
        daily_3am,
        trigger=CronTrigger(hour=3, minute=0, timezone=ZoneInfo(tz)),
        id="daily_3am",
        replace_existing=True,
    )
    return scheduler
```

- [ ] **Step 2: Verify import**

Run: `.venv/bin/python -c "from rutix.jobs.scheduler import make_scheduler; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit (NO push)**

```bash
git add src/rutix/jobs/scheduler.py
git commit -m "feat(jobs): scheduler — chain flush_day + update_habits + flush_week at 3am"
```

---

## Task 15: Wire __main__ + bot/app.py for new handlers and clients

**Files:**
- Modify: `src/rutix/__main__.py`
- Modify: `src/rutix/bot/app.py`

- [ ] **Step 1: Replace `__main__.py`**

`src/rutix/__main__.py`:

```python
"""rutix entry point — long-poll bot + APScheduler in one process."""
import asyncio
import logging

from pythonjsonlogger import jsonlogger

from rutix.bot.app import build_bot, build_dispatcher
from rutix.db.engine import make_engine, make_session_factory
from rutix.integrations.claude import ClaudeClient
from rutix.integrations.github import GitHubClient
from rutix.integrations.todoist import TodoistClient
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
    claude = ClaudeClient(api_key=settings.anthropic_api_key)
    todoist = TodoistClient(token=settings.todoist_token)

    bot = build_bot(settings.bot_token)
    dp = build_dispatcher(allowed_user_id=settings.telegram_user_id)
    dp["session_factory"] = session_factory
    dp["github"] = github
    dp["claude"] = claude
    dp["todoist"] = todoist
    dp["settings"] = settings

    scheduler = make_scheduler(session_factory, github, todoist, settings.tz)
    scheduler.start()

    try:
        await dp.start_polling(bot)
    finally:
        log.info("rutix shutting down")
        scheduler.shutdown(wait=False)
        await github.aclose()
        await todoist.aclose()
        await bot.session.close()
        await engine.dispose()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Replace `bot/app.py`**

`src/rutix/bot/app.py`:

```python
"""Build aiogram Bot + Dispatcher with all routers wired."""
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from rutix.bot.auth import WhitelistMiddleware
from rutix.bot.handlers import eat as eat_handler
from rutix.bot.handlers import meds as meds_handler
from rutix.bot.handlers import note_done as note_done_handler
from rutix.bot.handlers import sync as sync_handler
from rutix.bot.handlers import today as today_handler
from rutix.bot.handlers import track as track_handler
from rutix.bot.handlers import week as week_handler


def build_bot(token: str) -> Bot:
    return Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


def build_dispatcher(allowed_user_id: int) -> Dispatcher:
    dp = Dispatcher()
    dp.update.middleware(WhitelistMiddleware(allowed_user_id))
    dp.include_router(track_handler.router)
    dp.include_router(sync_handler.router)
    dp.include_router(eat_handler.router)
    dp.include_router(note_done_handler.router)
    dp.include_router(today_handler.router)
    dp.include_router(week_handler.router)
    dp.include_router(meds_handler.router)
    return dp
```

- [ ] **Step 3: Verify**

Run: `.venv/bin/python -c "import rutix.__main__; print('imports ok')"`
Expected: `imports ok`

Run: `.venv/bin/ruff check src/`
Expected: no output.

Run: `.venv/bin/ruff format --check src/`
Expected: no output (run `ruff format src/` first if it complains).

- [ ] **Step 4: Commit (NO push)**

```bash
git add src/rutix/__main__.py src/rutix/bot/app.py
git commit -m "feat: wire claude+todoist clients, register all phase-2 routers"
```

---

## Task 16: Append Phase 2 smoke test to README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Append section**

Add to the end of `README.md`:

```markdown
## Phase 2 smoke test

Pre-reqs (in addition to Phase 1):
- Anthropic API key (https://console.anthropic.com) → `ANTHROPIC_API_KEY`
- Todoist Personal API Token from Settings → Integrations → Developer → `TODOIST_TOKEN`
- Todoist Pro (Activity Log endpoint requires it for recurring tasks)

### Update .env

```bash
echo "ANTHROPIC_API_KEY=sk-ant-..." >> .env
echo "TODOIST_TOKEN=..." >> .env
docker compose up -d --force-recreate
```

### /eat

```
/eat шаурма + кола
```

Bot should reply within ~5s with parsed items + Итого. Verify in
`quibex/life:daily/<today>.md` Питание section.

### /note + /done

```
/note важная мысль про сегодня
/done закрыл задачу X
```

Verify in daily file Заметки / Что сделано sections.

### /today

```
/today
```

Shows mood (from /track) + meals total (from /eat).

### /week

```
/week
```

7 buttons appear. Tap any day → bot edits message to show that day's summary.

### /meds

```
/meds
```

Shows current meds. Tap ➕ Добавить, walk through key/name/label/dose flow.
Verify SQLite: `docker compose exec bot sqlite3 /app/data/bot.db "SELECT * FROM meds_active;"`

### Habits update (manual trigger)

```bash
docker compose exec bot python -c "
import asyncio
from datetime import date
from rutix.integrations.github import GitHubClient
from rutix.integrations.todoist import TodoistClient
from rutix.jobs.update_habits import update_habits
from rutix.settings import load_settings
from rutix.time_utils import yesterday_of, subjective_today
from datetime import datetime
from zoneinfo import ZoneInfo

async def go():
    s = load_settings()
    gh = GitHubClient(s.github_api_token, s.life_repo)
    td = TodoistClient(s.todoist_token)
    target = yesterday_of(subjective_today(datetime.now(ZoneInfo(s.tz)), s.tz))
    sha = await update_habits(gh, td, target)
    print('result:', sha)
    await gh.aclose(); await td.aclose()

asyncio.run(go())
"
```

Verify checked habits in yesterday's `daily/<...>.md` match what you completed in Todoist.

### Weekly flush (manual trigger, only meaningful on a Monday)

```bash
docker compose exec bot python -c "
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from rutix.db.engine import make_engine, make_session_factory
from rutix.integrations.github import GitHubClient
from rutix.jobs.flush_week import flush_week
from rutix.settings import load_settings
from rutix.time_utils import subjective_today

async def go():
    s = load_settings()
    engine = make_engine(s.database_url)
    Session = make_session_factory(engine)
    gh = GitHubClient(s.github_api_token, s.life_repo)
    today = subjective_today(datetime.now(ZoneInfo(s.tz)), s.tz)
    async with Session() as sess:
        sha = await flush_week(sess, gh, today)
        print('result:', sha)
    await gh.aclose()

asyncio.run(go())
"
```

If today isn't Monday, returns None (expected). On a Monday, generates
`weekly/2026-Wxx.md`, `nutrition/2026-Wxx.md`, deletes the 7 daily files,
and purges SQLite.
```

- [ ] **Step 2: Commit + push everything**

```bash
git add README.md
git commit -m "docs: phase 2 smoke test runbook"
git push origin main
gh run watch
```

Expected: CI green.

---

## Phase 2 Done When

1. `pytest tests/` — all green (~70+ tests including phase-1 retained)
2. `ruff check src/ && ruff format --check src/` — clean
3. `python -c "import rutix.__main__"` — imports cleanly
4. `docker compose up` — starts; logs show "rutix starting"
5. CI green on `main`
6. Manual smoke (in actual Telegram, with real tokens):
   - `/eat шаурма` → parsed + written to daily Питание
   - `/note X`, `/done Y` → bullets appear in daily
   - `/today` shows mood + meals
   - `/week` shows 7 buttons; tap → day report
   - `/meds` lists active; add/archive/dose flows work
   - Tomorrow morning at 03:00: yesterday's habits get checked from Todoist
   - Following Monday at 03:00: weekly + nutrition rollups appear, daily files for the just-finished week are gone

---

## Self-Review Notes

**Spec coverage (against `quibex/life:projects/mood-bot.md`):**
- ✅ `/eat <текст>` — Task 9
- ✅ `/note <текст>` — Task 10
- ✅ `/done <текст>` — Task 10
- ✅ `/today` — Task 11
- ✅ `/week` 7 кнопок — Task 12
- ✅ `/meds` add / archive / change dose — Task 13
- ✅ Cron 03:00 — habits via Todoist Activity Log — Task 8 + Task 14
- ✅ Cron 03:00 Sunday — weekly + nutrition flush + cleanup daily/ + purge SQLite — Task 7 + Task 14
- ✅ Promptы как файлы (`prompts/eat.md`) — Task 3
- ✅ Бот = единственный writer — handlers append/replace sections deterministically; no Claude in cron
- ✅ Activity Log fallback to empty set on 403 (Pro required) — Task 4

**Type consistency check:**
- `MealItem(slot, name, kcal, protein, fat, carbs)` — used in `daily.py` (T2), `claude.py` (T3, returns with empty slot), `eat.py` (T9, sets slot before append), `nutrition_weekly.py` (T6) — consistent ✓
- `WeeklyDay(date, done_habits, sleep_offh, sleep_onh, kcal)` — used only in T5 + T7 — consistent ✓
- `NutritionDay(date, meals)` — used only in T6 + T7 — consistent ✓
- `HabitsConfig(daily, scheduled)` — used only in T5 + T7 — consistent ✓
- `period_id` format `"week:YYYY-Wxx"` — produced by T7, no other writers ✓
- `session_factory: async_sessionmaker[AsyncSession]`, `github: GitHubClient`, `claude: ClaudeClient`, `todoist: TodoistClient` — uniform handler/job kwarg types ✓

**Placeholder scan:** None.
