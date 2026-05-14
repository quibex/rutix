# Phase 3: Prod Deploy — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bot lives on a personal VPS as a Docker container, deployed via GitHub Actions on every `main` push. Pattern mirrors `kurut-pie`'s prod pipeline: build → push GHCR → SSH deploy → health check.

**Architecture:** GitHub Actions builds the Docker image and pushes it to `ghcr.io/quibex/rutix:latest`. A second job SSHes into the VPS, writes `.env` from repo Secrets/Variables, runs `docker compose pull && up -d --force-recreate`, verifies the container is healthy (running + RestartCount == 0 after 15s), and aborts on failure with logs.

**Tech Stack additions:** GitHub Actions (no new Python deps).

**Spec:** `quibex/life:projects/mood-bot.md` — Деплой section.

**Reference:** `/Users/elabdi/Desktop/kurut/kurut-pie/.github/workflows/prod.yml` — proven pattern for the same stack.

---

## Out of scope for Phase 3

- Backups (rsync `bot.db` to private git): nice-to-have, defer until needed. Current state: data loss = re-add `meds_active` manually + idempotent re-flush. Mild inconvenience, not data loss in any meaningful sense.
- Reverse proxy / Caddy: not needed (long polling = no inbound traffic).
- Monitoring / alerting / Grafana annotations: nope, personal bot.
- Blue-green / staging environment: single instance is fine.
- TLS certificates: N/A.
- Log shipping: journald + `docker compose logs` is sufficient.

---

## What's broken right now (will be fixed by this phase)

1. **`Dockerfile` doesn't COPY `prompts/`** — Phase 2 added `prompts/eat.md`, the bot reads it on each `/eat` call. In production the file won't exist → FileNotFoundError on first `/eat`. **Task 1 fixes this.**
2. **No production compose file** — Phase 1 only created the local-dev `docker-compose.yml` (build context, no image). Prod needs to pull from GHCR. **Task 1 creates it.**
3. **No prod workflow** — only `ci.yml` exists. **Task 2 creates `prod.yml`.**
4. **No deployment instructions** — user doesn't know what GH Secrets/Variables to set, or what to do on the VPS. **Task 3 documents in README.**

---

## File map

**Modified:**
- `Dockerfile` — add `COPY prompts ./prompts`

**Created:**
- `docker-compose.prod.yml` — image-based service, mounts `./data`, env_file `.env`
- `.github/workflows/prod.yml` — build+push GHCR → SSH deploy → health check
- `README.md` — append "Phase 3 — Production deployment" section (VPS setup, Secrets/Variables, first-deploy walkthrough, troubleshooting)

---

## Task 1: Fix Dockerfile + create docker-compose.prod.yml

**Files:**
- Modify: `Dockerfile`
- Create: `docker-compose.prod.yml`

- [ ] **Step 1: Update `Dockerfile` to COPY prompts**

Replace `Dockerfile` content with:

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
COPY prompts ./prompts
ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src
CMD ["sh", "-c", "alembic upgrade head && python -m rutix"]
```

(Only the new line is `COPY prompts ./prompts`.)

- [ ] **Step 2: Verify the image still builds**

Run: `cd /Users/elabdi/Desktop/rutix && docker build -t rutix:phase3-test . 2>&1 | tail -3`
Expected: `naming to docker.io/library/rutix:phase3-test`

- [ ] **Step 3: Verify prompts/eat.md is in the image**

Run: `docker run --rm --entrypoint sh rutix:phase3-test -c "ls /app/prompts/"`
Expected: `eat.md`

- [ ] **Step 4: Clean up the test image**

Run: `docker rmi rutix:phase3-test`

- [ ] **Step 5: Create `docker-compose.prod.yml`**

```yaml
services:
  bot:
    image: ghcr.io/quibex/rutix:latest
    container_name: rutix-bot
    restart: unless-stopped
    volumes:
      - ./data:/app/data
    env_file:
      - .env
    environment:
      - DATABASE_URL=sqlite+aiosqlite:///data/bot.db
```

Differences from `docker-compose.yml` (local dev):
- No `build:` — pulls pre-built image from GHCR
- Identical volumes / env_file / environment (so local SQLite is portable to prod via `docker cp`)

- [ ] **Step 6: Commit (NO push — Task 2 will push the bundle)**

```bash
git add Dockerfile docker-compose.prod.yml
git commit -m "fix(docker): copy prompts/ into image; add docker-compose.prod.yml"
```

---

## Task 2: Create prod.yml workflow (build → push GHCR → SSH deploy → health check)

**Files:**
- Create: `.github/workflows/prod.yml`

- [ ] **Step 1: Create the workflow file**

`.github/workflows/prod.yml`:

```yaml
name: Prod Pipeline

on:
  push:
    branches: [main]
  workflow_dispatch:

env:
  REGISTRY: ghcr.io
  IMAGE_NAME: ${{ github.repository }}

jobs:
  ci:
    uses: ./.github/workflows/ci.yml

  build-and-push:
    needs: ci
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    steps:
      - uses: actions/checkout@v4

      - name: Log in to GHCR
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Extract metadata
        id: meta
        uses: docker/metadata-action@v5
        with:
          images: ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}
          tags: |
            type=raw,value=latest
            type=sha,prefix=prod-

      - name: Build and push
        uses: docker/build-push-action@v6
        with:
          context: .
          push: true
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}

  deploy:
    needs: build-and-push
    runs-on: ubuntu-latest
    environment: prod
    permissions:
      contents: read
      packages: read
    steps:
      - uses: actions/checkout@v4

      - name: Copy compose file to server
        uses: appleboy/scp-action@v0.1.7
        with:
          host: ${{ secrets.SSH_HOST }}
          username: ${{ secrets.SSH_USER }}
          key: ${{ secrets.SSH_PRIVATE_KEY }}
          port: ${{ secrets.SSH_PORT }}
          source: "docker-compose.prod.yml"
          target: /opt/rutix
          overwrite: true

      - name: Deploy
        uses: appleboy/ssh-action@v1
        with:
          host: ${{ secrets.SSH_HOST }}
          username: ${{ secrets.SSH_USER }}
          key: ${{ secrets.SSH_PRIVATE_KEY }}
          port: ${{ secrets.SSH_PORT }}
          script: |
            set -e
            cd /opt/rutix
            mkdir -p data

            # Write .env from individual secrets and variables
            cat > .env << 'ENVEOF'
            BOT_TOKEN=${{ secrets.BOT_TOKEN }}
            TELEGRAM_USER_ID=${{ vars.TELEGRAM_USER_ID }}
            ANTHROPIC_API_KEY=${{ secrets.ANTHROPIC_API_KEY }}
            GITHUB_API_TOKEN=${{ secrets.GITHUB_API_TOKEN }}
            LIFE_REPO=${{ vars.LIFE_REPO }}
            TODOIST_TOKEN=${{ secrets.TODOIST_TOKEN }}
            DATABASE_URL=sqlite+aiosqlite:///data/bot.db
            TZ=${{ vars.TZ }}
            ENVEOF
            chmod 600 .env

            # Rename compose file
            mv -f docker-compose.prod.yml docker-compose.yml 2>/dev/null || true

            # Login to GHCR and deploy
            echo '${{ secrets.GITHUB_TOKEN }}' | docker login ghcr.io -u ${{ github.actor }} --password-stdin
            docker compose pull bot
            docker compose up -d --force-recreate

            # Fail pipeline if bot container was not created
            BOT_CID=$(docker compose ps -q bot)
            if [ -z "$BOT_CID" ]; then
              echo "Bot container was not created"
              docker compose ps
              docker compose logs --tail=200 bot || true
              exit 1
            fi

            sleep 15
            BOT_STATUS=$(docker inspect -f '{{.State.Status}}' "$BOT_CID")
            BOT_RESTARTS=$(docker inspect -f '{{.RestartCount}}' "$BOT_CID")
            if [ "$BOT_STATUS" != "running" ] || [ "$BOT_RESTARTS" -gt 0 ]; then
              echo "Bot unhealthy after deploy: status=$BOT_STATUS restarts=$BOT_RESTARTS"
              docker compose ps
              docker compose logs --tail=200 bot || true
              exit 1
            fi

            docker image prune -f
```

Notes:
- `environment: prod` — gates the `deploy` job behind a GitHub deployment environment. The user creates the `prod` environment in repo Settings → Environments only after VPS is ready. Until then, the job is skipped or fails fast — won't crash a green CI.
- `secrets.GITHUB_TOKEN` is auto-provided by GHA — no setup needed.
- All other secrets/variables are set by the user in repo Settings → Secrets and variables → Actions.

- [ ] **Step 2: Validate YAML syntax**

Run: `python3 -c "import yaml; yaml.safe_load(open('/Users/elabdi/Desktop/rutix/.github/workflows/prod.yml'))" && echo "yaml ok"`
Expected: `yaml ok`

- [ ] **Step 3: Commit (NO push yet — push happens after Task 3 README is done)**

```bash
git add .github/workflows/prod.yml
git commit -m "feat(ci): prod pipeline — build GHCR + SSH deploy + health check"
```

---

## Task 3: README — Phase 3 deployment runbook

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Append the deployment section to README**

Add the following to the end of `/Users/elabdi/Desktop/rutix/README.md`:

```markdown
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
```

- [ ] **Step 2: Commit + push the bundle (Tasks 1 + 2 + 3 in one push)**

```bash
git add README.md
git commit -m "docs: phase 3 production deployment runbook"
git push origin main
```

- [ ] **Step 3: Watch CI**

Run: `gh run list --workflow=ci.yml --limit 1 -R quibex/rutix` — find the new run ID.
Run: `gh run watch <id> -R quibex/rutix`
Expected: `lint` and `test` jobs pass (CI gets re-run on every push).

`prod.yml` will also kick off — it'll succeed at `ci` and `build-and-push`,
then either skip `deploy` (if `prod` environment doesn't exist yet) or fail
`deploy` (if `prod` exists but secrets are missing). That's the expected
state of an unconfigured deploy.

---

## Phase 3 Done When

1. Image builds locally and contains `/app/prompts/eat.md`
2. `docker-compose.prod.yml` exists and references `ghcr.io/quibex/rutix:latest`
3. `.github/workflows/prod.yml` exists, valid YAML, build-and-push step works (verifiable by inspecting the image at `ghcr.io/quibex/rutix:latest` after first push)
4. README has the full Phase 3 deployment runbook
5. CI is still green on `main`
6. (User-action, separate from this plan): VPS provisioned, Secrets/Variables added, `prod` environment created, first deploy succeeds, bot answers `/track` from prod

Steps 1-5 are this plan's deliverables. Step 6 is the user's setup work — documented step-by-step in README.

---

## Self-Review Notes

**Spec coverage (against `quibex/life:projects/mood-bot.md` Деплой section):**
- ✅ GitHub Actions → GHCR → SSH в VPS → `docker compose pull/up` — Task 2
- ✅ Multi-stage Dockerfile — already exists, fixed in Task 1
- ✅ docker-compose.prod.yml — Task 1
- ✅ Health check (container running + RestartCount == 0) — Task 2
- ✅ GitHub Secrets list — Task 3 documents
- ✅ GitHub Variables list — Task 3 documents
- ✅ `environment: prod` gate — Task 2

**Type consistency check:** No code/types in this phase — only YAML, Dockerfile, markdown.

**Placeholder scan:** None. Every step has actual content. The "(separate from this plan)" Step 6 in Done When is intentional — that's the user-action handoff, not work for this plan.

**Risks acknowledged:**
- The `deploy` job will likely fail or be skipped on the first push — that's expected and documented in README troubleshooting.
- GHCR images default to private. If `docker compose pull` fails on the VPS for auth reasons, the deploy script logs in via `${{ secrets.GITHUB_TOKEN }}` — should work, but if user makes the package public via repo Settings → Packages, it's even simpler.
