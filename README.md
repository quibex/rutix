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

## Phase 3 — Production deployment

### Architecture

GitHub Actions on every `main` push → builds Docker image → pushes to
`ghcr.io/quibex/rutix:latest` → SSHes into the VPS → writes `.env` from
GitHub Secrets/Variables → `docker compose pull && up -d --force-recreate` →
verifies container is healthy.

Single container, single VPS. No reverse proxy, no Caddy, no inbound traffic
(bot uses long polling to Telegram).

### One-time VPS setup

#### 1. Provision

Any VPS with at least 1 GB RAM and Docker support works. Tested on
Hetzner CX22 (~€4/mo, Ubuntu 22.04).

#### 2. Install Docker

```bash
ssh root@<vps-ip>
apt update && apt upgrade -y
curl -fsSL https://get.docker.com | sh
```

#### 3. Create deploy user with SSH key access

```bash
adduser --disabled-password --gecos "" deploy
usermod -aG docker deploy
mkdir -p /home/deploy/.ssh
chmod 700 /home/deploy/.ssh
# Generate a deploy keypair locally:
#   ssh-keygen -t ed25519 -f ~/.ssh/rutix_deploy -C "rutix-deploy"
# Paste the .pub here:
echo "<paste contents of ~/.ssh/rutix_deploy.pub>" > /home/deploy/.ssh/authorized_keys
chmod 600 /home/deploy/.ssh/authorized_keys
chown -R deploy:deploy /home/deploy/.ssh
```

Verify SSH from your laptop: `ssh -i ~/.ssh/rutix_deploy deploy@<vps-ip>`

#### 4. Create the deploy directory

```bash
ssh deploy@<vps-ip>
sudo mkdir -p /opt/rutix
sudo chown deploy:deploy /opt/rutix
```

### GitHub repo setup

#### Secrets (Settings → Secrets and variables → Actions → New repository secret)

| Name | Value |
|------|-------|
| `SSH_HOST` | VPS IP or hostname |
| `SSH_USER` | `deploy` |
| `SSH_PORT` | `22` (or your custom port) |
| `SSH_PRIVATE_KEY` | Contents of `~/.ssh/rutix_deploy` (private key, including `-----BEGIN/END-----` lines) |
| `BOT_TOKEN` | From [@BotFather](https://t.me/botfather) |
| `ANTHROPIC_API_KEY` | From https://console.anthropic.com |
| `GITHUB_API_TOKEN` | Fine-grained PAT for `quibex/life`, scope `Contents: read+write` |
| `TODOIST_TOKEN` | From Todoist Settings → Integrations → Developer (Pro required for habit recurring task tracking) |

#### Variables (Settings → Secrets and variables → Actions → Variables tab)

| Name | Value |
|------|-------|
| `TELEGRAM_USER_ID` | Your numeric Telegram ID (use [@userinfobot](https://t.me/userinfobot)) |
| `LIFE_REPO` | `quibex/life` |
| `TZ` | `Europe/Moscow` |

#### Environment (Settings → Environments → New environment)

Create one called `prod`. No protection rules needed (single-user repo). The
`deploy` job in `prod.yml` is gated by `environment: prod` — it won't run
until this exists.

### First deployment

Once Secrets, Variables, and the `prod` environment exist:

```bash
git commit --allow-empty -m "trigger first prod deploy"
git push origin main
gh run watch
```

Pipeline runs:
1. **ci** — ruff + pytest + alembic validation (~1 min)
2. **build-and-push** — Docker build + push to GHCR (~2 min on cold cache, ~30s warm)
3. **deploy** — SCP `docker-compose.prod.yml` to `/opt/rutix`, write `.env`, `docker compose pull && up -d --force-recreate`, sleep 15s, verify health (~30s)

Total: ~3-4 min cold, ~1-2 min warm.

If `deploy` fails with "Bot unhealthy after deploy", the script dumps last
200 lines of `docker compose logs bot` — look there for the cause (usually
missing/wrong env var).

### Verifying the bot is alive

```bash
ssh deploy@<vps-ip>
cd /opt/rutix
docker compose ps                    # should show rutix-bot Up
docker compose logs --tail=50 bot    # JSON-formatted startup logs
```

In Telegram with your bot:
- `/track` — should walk through the FSM
- `/eat шаурма` — should reply within ~5s
- `/today` — should show what you logged

### Updating the bot

Every push to `main` deploys automatically. Cron jobs (`flush_day`,
`update_habits`, `flush_week`) re-register on every container restart — they
fire at 03:00 MSK regardless of when you redeploy.

### Backup (optional, not implemented)

The bot's persistent state lives in `/opt/rutix/data/bot.db`. Loss = mild
inconvenience (re-add `meds_active` via `/meds`, idempotent re-flush). To
add backup later:

```bash
# On VPS, daily cron:
0 4 * * * sqlite3 /opt/rutix/data/bot.db ".backup /opt/rutix/data/backup-$(date +\%F).db"
# Then rsync to a private remote, or push to a private gist via gh.
```

### Troubleshooting

- **`deploy` job fails with "Bot container was not created"** → `docker compose pull` likely failed. Check that the GHCR image is public (`gh api orgs/quibex/packages/container/rutix --jq .visibility` or repo Settings → Packages → make public).
- **Bot starts but `/track` does nothing** → `TELEGRAM_USER_ID` mismatch. Whitelist middleware silently drops everything else. Check `docker compose logs bot` for "rutix starting (user_id=...)".
- **`/eat` fails with FileNotFoundError on `prompts/eat.md`** → Image was built before Phase 3 fix. Trigger a rebuild: `gh workflow run prod.yml`.
- **Cron at 03:00 MSK didn't fire** → Container TZ. Verify with `docker compose exec bot date` — should print MSK time.
- **Activity Log returns 403 on habit update** → Todoist Pro not active. Either subscribe or accept that recurring task tracking won't work until you do.
