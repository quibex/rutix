# rutix

Personal Telegram bot — mental state & nutrition tracker.

Single-user (whitelisted by `TELEGRAM_USER_ID`). Writes structured daily data
into a private Obsidian-backed GitHub repo.

Design spec lives in a private repo (`life/projects/mood-bot.md`).

## Stack

Python 3.12, aiogram 3, SQLAlchemy 2 + Alembic, APScheduler, anthropic SDK,
httpx. Deployed via Docker + GHCR + GitHub Actions to a personal VPS.

Same pattern as [kurut-pie](https://github.com/quibex/kurut-pie).

## Local dev

```bash
cp .env.example .env  # fill in tokens
docker compose up
```

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
