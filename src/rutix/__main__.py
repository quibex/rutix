"""rutix entry point — long-poll bot + APScheduler in one process."""

import asyncio
import logging

from pythonjsonlogger import jsonlogger

from rutix.bot.app import BOT_COMMANDS, build_bot, build_dispatcher
from rutix.bot.notify import Notifier
from rutix.db.engine import make_engine, make_session_factory
from rutix.integrations.claude import ClaudeClient
from rutix.integrations.github import GitHubClient
from rutix.integrations.todoist import TodoistClient
from rutix.jobs.job_prefs import JobPrefsManager
from rutix.jobs.scheduler import make_scheduler
from rutix.settings import load_settings
from rutix.tz_manager import TimezoneManager


def _setup_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(jsonlogger.JsonFormatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
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
    todoist = TodoistClient(token=settings.todoist_token, tz=settings.tz)

    # Resolve the timezone before anything schedules against it: `load()` reads
    # the stored zone (seeding it from TZ on first run) and points settings.tz
    # at it, so handlers and cron triggers start in the user's current zone
    # rather than the env default they were deployed with.
    tz_manager = TimezoneManager(session_factory, settings, todoist)
    await tz_manager.load()
    log.info("rutix timezone: %s", tz_manager.tz)

    # Same deal for the schedule itself: the cron times in JOB_SPECS are only
    # defaults, and whatever the user set from /schedule has to be in memory
    # before the jobs are registered.
    job_prefs = JobPrefsManager(session_factory)
    await job_prefs.load()

    bot = build_bot(settings.bot_token)
    dp = build_dispatcher(allowed_user_id=settings.telegram_user_id)
    dp["session_factory"] = session_factory
    dp["github"] = github
    dp["claude"] = claude
    dp["todoist"] = todoist
    dp["settings"] = settings
    dp["job_prefs"] = job_prefs

    # Cron jobs send via a Notifier so any standalone message (med reminder,
    # evening ping, …) cancels an in-progress /report (or /state) step — the
    # user's next reply then reaches its intended handler (e.g. a med snooze),
    # and /report resumes from the first unanswered step.
    notifier = Notifier(bot, dp.storage, settings.telegram_user_id)
    scheduler = make_scheduler(
        session_factory,
        github,
        todoist,
        claude,
        notifier,
        settings.telegram_user_id,
        tz_manager,
        job_prefs,
    )
    scheduler.start()

    # Catch a move that happened while the bot was down — the hourly cron would
    # otherwise leave the schedule wrong until the next :07.
    try:
        await tz_manager.sync_from_todoist()
    except Exception:
        log.exception("initial timezone sync failed")

    await bot.set_my_commands(BOT_COMMANDS)
    log.info("registered %d bot commands in /-menu", len(BOT_COMMANDS))

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
