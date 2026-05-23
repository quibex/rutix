"""Todoist REST API client — Activity Log for habit completions.

Activity Log requires Todoist Pro. On 403 we log and return an empty set
so the scheduler doesn't crash.

5xx responses are retried with exponential backoff (default 3 attempts at
1s/3s/9s) per request. The 03:00 cron then has an additional catch-up
schedule for longer outages — see scheduler.py.

The endpoint `/api/v1/activities` filters via `object_event_types=["item:completed"]`
(the older `event_type=completed` query param is silently ignored). It also caps
`limit` at 100, so we paginate with `next_cursor` to cover heavy days.
"""

import asyncio
import logging
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

logger = logging.getLogger(__name__)


class TodoistClient:
    BASE_URL = "https://api.todoist.com"
    PAGE_LIMIT = 100  # API hard max per /api/v1/activities docs
    MAX_PAGES = 20  # safety cap → up to 2000 events scanned for one day

    def __init__(
        self,
        token: str,
        tz: str = "Europe/Moscow",
        http: httpx.AsyncClient | None = None,
        retry_5xx_attempts: int = 3,
        retry_backoff_base: float = 1.0,
    ):
        self.tz = tz
        self.http = http or httpx.AsyncClient(
            base_url=self.BASE_URL,
            headers={"Authorization": f"Bearer {token}"},
            timeout=15.0,
        )
        self.retry_5xx_attempts = retry_5xx_attempts
        self.retry_backoff_base = retry_backoff_base

    async def _get_with_retry(self, url: str, params: dict[str, Any]) -> httpx.Response:
        """GET with exponential backoff on 5xx. 4xx (incl. 403) returns the
        response unretried — the caller decides what to do with it."""
        last_exc: httpx.HTTPStatusError | None = None
        for attempt in range(self.retry_5xx_attempts):
            r = await self.http.get(url, params=params)
            if r.status_code < 500:
                return r
            try:
                r.raise_for_status()
            except httpx.HTTPStatusError as e:
                last_exc = e
            if attempt + 1 < self.retry_5xx_attempts:
                delay = self.retry_backoff_base * (3**attempt)
                logger.warning(
                    "Todoist %s returned %d (attempt %d/%d) — retrying in %.1fs",
                    url,
                    r.status_code,
                    attempt + 1,
                    self.retry_5xx_attempts,
                    delay,
                )
                if delay > 0:
                    await asyncio.sleep(delay)
        assert last_exc is not None
        raise last_exc

    async def completed_titles_for_day(self, day: date) -> set[str]:
        """Return task titles completed on the given local date (in self.tz).

        Includes recurring tasks via Activity Log. Dedupes if a recurring
        task was completed twice on the same day.
        """
        target_tz = ZoneInfo(self.tz)
        utc = ZoneInfo("UTC")
        day_start_utc = datetime.combine(day, time.min, tzinfo=target_tz).astimezone(utc)
        day_end_utc = (
            datetime.combine(day + timedelta(days=1), time.min, tzinfo=target_tz)
        ).astimezone(utc)

        titles: set[str] = set()
        cursor: str | None = None
        pages_fetched = 0
        events_seen = 0
        events_in_window = 0

        for _ in range(self.MAX_PAGES):
            params: dict[str, Any] = {
                "object_event_types": '["item:completed"]',
                "limit": self.PAGE_LIMIT,
                "date_from": day_start_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "date_to": day_end_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            if cursor:
                params["cursor"] = cursor

            r = await self._get_with_retry("/api/v1/activities", params)
            if r.status_code == 403:
                logger.warning(
                    "Todoist Activity Log returned 403 — likely Pro required. "
                    "Returning empty habit set."
                )
                return set()
            r.raise_for_status()
            data = r.json()
            pages_fetched += 1

            events = data.get("results", [])
            if not events:
                break

            for ev in events:
                events_seen += 1
                if ev.get("event_type") != "completed":
                    continue
                event_date = ev.get("event_date")
                if not event_date:
                    continue
                try:
                    dt_utc = datetime.fromisoformat(event_date.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if dt_utc.astimezone(target_tz).date() != day:
                    continue
                events_in_window += 1
                content = ev.get("extra_data", {}).get("content")
                if content:
                    titles.add(content)

            cursor = data.get("next_cursor")
            if not cursor:
                break

        logger.info(
            "todoist: %s — %d pages, %d events scanned, %d in window, %d unique titles",
            day,
            pages_fetched,
            events_seen,
            events_in_window,
            len(titles),
        )
        return titles

    async def aclose(self) -> None:
        await self.http.aclose()
