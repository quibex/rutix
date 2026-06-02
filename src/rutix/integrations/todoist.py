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
import json
import logging
import uuid
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from rutix.time_utils import EARLY_MORNING_BOUNDARY

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

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """HTTP request with exponential backoff on 5xx. 4xx returns the response
        unretried — the caller decides what to do with it."""
        last_exc: httpx.HTTPStatusError | None = None
        for attempt in range(self.retry_5xx_attempts):
            r = await self.http.request(method, url, params=params, json=json, data=data)
            if r.status_code < 500:
                return r
            try:
                r.raise_for_status()
            except httpx.HTTPStatusError as e:
                last_exc = e
            if attempt + 1 < self.retry_5xx_attempts:
                delay = self.retry_backoff_base * (3**attempt)
                logger.warning(
                    "Todoist %s %s returned %d (attempt %d/%d) — retrying in %.1fs",
                    method,
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

    async def _get_with_retry(self, url: str, params: dict[str, Any]) -> httpx.Response:
        return await self._request_with_retry("GET", url, params=params)

    def _subjective_day_utc_window(self, day: date) -> tuple[datetime, datetime]:
        """[day 03:00 local, day+1 03:00 local) expressed in UTC."""
        target_tz = ZoneInfo(self.tz)
        utc = ZoneInfo("UTC")
        start = datetime.combine(day, EARLY_MORNING_BOUNDARY, tzinfo=target_tz).astimezone(utc)
        end = datetime.combine(
            day + timedelta(days=1), EARLY_MORNING_BOUNDARY, tzinfo=target_tz
        ).astimezone(utc)
        return start, end

    async def _completion_events_in_window(
        self, start_utc: datetime, end_utc: datetime
    ) -> list[dict[str, Any]]:
        """Return raw `item:completed` events from the Activity Log whose
        event_date lies within [start_utc, end_utc). Paginated via next_cursor.
        Returns [] on 403 (Pro required) so callers can no-op gracefully."""
        out: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(self.MAX_PAGES):
            params: dict[str, Any] = {
                "object_event_types": '["item:completed"]',
                "limit": self.PAGE_LIMIT,
                "date_from": start_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "date_to": end_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            if cursor:
                params["cursor"] = cursor

            r = await self._get_with_retry("/api/v1/activities", params)
            if r.status_code == 403:
                logger.warning(
                    "Todoist Activity Log returned 403 — likely Pro required. Returning no events."
                )
                return []
            r.raise_for_status()
            data = r.json()
            events = data.get("results", [])
            if not events:
                break
            for ev in events:
                if ev.get("event_type") != "completed":
                    continue
                event_date = ev.get("event_date")
                if not event_date:
                    continue
                try:
                    dt_utc = datetime.fromisoformat(event_date.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if not (start_utc <= dt_utc < end_utc):
                    continue
                out.append(ev)
            cursor = data.get("next_cursor")
            if not cursor:
                break
        return out

    async def completed_titles_for_day(self, day: date) -> set[str]:
        """Return task titles completed during the user's *subjective* day in
        self.tz — the window [day 03:00 local, day+1 03:00 local), matching
        the same 3am boundary used by `subjective_today` elsewhere in the bot.

        Without this, a habit checked between 00:00 and 03:00 gets bucketed
        into the next calendar day in Todoist's log and the 03:00 flush
        misses it for the day it actually belongs to.

        Dedupes if a recurring task was completed twice on the same day.
        """
        start_utc, end_utc = self._subjective_day_utc_window(day)
        events = await self._completion_events_in_window(start_utc, end_utc)
        titles: set[str] = set()
        for ev in events:
            content = ev.get("extra_data", {}).get("content")
            if content:
                titles.add(content)
        logger.info(
            "todoist: %s — %d events in subjective day, %d unique titles",
            day,
            len(events),
            len(titles),
        )
        return titles

    async def completed_task_ids_between(
        self, start_local: datetime, end_local: datetime
    ) -> set[str]:
        """Distinct task IDs (object_id) completed between two LOCAL-tz datetimes.
        Used by the post-midnight rescheduler to find recurring tasks that fired
        their recurrence one day too early."""
        target_tz = ZoneInfo(self.tz)
        if start_local.tzinfo is None:
            start_local = start_local.replace(tzinfo=target_tz)
        if end_local.tzinfo is None:
            end_local = end_local.replace(tzinfo=target_tz)
        utc = ZoneInfo("UTC")
        events = await self._completion_events_in_window(
            start_local.astimezone(utc), end_local.astimezone(utc)
        )
        return {ev["object_id"] for ev in events if ev.get("object_id")}

    async def fetch_task(self, task_id: str) -> dict[str, Any] | None:
        """GET /api/v1/tasks/{id}. None on 404."""
        r = await self._request_with_retry("GET", f"/api/v1/tasks/{task_id}")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    async def fetch_active_tasks(self) -> list[dict[str, Any]]:
        """GET /api/v1/tasks — all active (not-yet-completed) tasks. Paginates."""
        out: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(self.MAX_PAGES):
            params: dict[str, Any] = {"limit": self.PAGE_LIMIT}
            if cursor:
                params["cursor"] = cursor
            r = await self._get_with_retry("/api/v1/tasks", params)
            r.raise_for_status()
            data = r.json()
            # Defensive: tolerate both paginated envelope and bare list.
            if isinstance(data, list):
                out.extend(data)
                break
            out.extend(data.get("results", []))
            cursor = data.get("next_cursor")
            if not cursor:
                break
        return out

    async def update_task_due_date(
        self, task_id: str, new_date: date, *, due: dict[str, Any] | None = None
    ) -> None:
        """Move a task's due date to ``new_date``.

        For a **recurring** task we must go through the Sync API's ``item_update``
        with a full ``due`` object — copying the existing recurrence ``string`` /
        ``is_recurring`` and only swapping the anchor date. The flat REST
        ``due_date`` field on ``POST /api/v1/tasks/{id}`` *silently drops* the
        recurrence (the task turns into a one-shot and vanishes on the next
        completion), which is wrong for a habit. This mirrors what the Todoist UI's
        "reschedule" does: only the date moves, the cadence stays.

        Non-recurring tasks take the simple REST path — there's no recurrence to
        preserve, so ``due_date`` is correct.
        """
        if due and due.get("is_recurring"):
            await self._reschedule_recurring(task_id, new_date, due)
            return
        r = await self._request_with_retry(
            "POST",
            f"/api/v1/tasks/{task_id}",
            json={"due_date": new_date.isoformat()},
        )
        r.raise_for_status()

    async def _reschedule_recurring(
        self, task_id: str, new_date: date, due: dict[str, Any]
    ) -> None:
        """Re-anchor a recurring task to ``new_date`` while keeping its cadence,
        via the Sync API ``item_update`` command. We resend the existing ``due``
        fields and replace only the calendar date (keeping any time-of-day /
        timezone suffix so a timed recurrence doesn't silently become all-day).

        The command ``uuid`` is generated once and reused across 5xx retries, so a
        retried request is deduped by Todoist rather than applied twice.
        """
        old = due.get("date") or ""
        # Keep a "T09:00:00[.ffffff][Z]" suffix if the original due was timed.
        date_str = new_date.isoformat() + old[10:] if "T" in old else new_date.isoformat()
        new_due: dict[str, Any] = {"date": date_str, "is_recurring": True}
        for key in ("string", "lang", "timezone"):
            if due.get(key) is not None:
                new_due[key] = due[key]
        command = {
            "type": "item_update",
            "uuid": str(uuid.uuid4()),
            "args": {"id": task_id, "due": new_due},
        }
        r = await self._request_with_retry(
            "POST",
            "/api/v1/sync",
            data={"commands": json.dumps([command])},
        )
        r.raise_for_status()
        status = r.json().get("sync_status", {}).get(command["uuid"])
        if status != "ok":
            raise RuntimeError(f"Todoist item_update failed for {task_id}: {status!r}")

    async def aclose(self) -> None:
        await self.http.aclose()
