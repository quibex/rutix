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
