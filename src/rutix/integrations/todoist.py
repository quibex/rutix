"""Todoist REST API client — Activity Log for habit completions.

Activity Log requires Todoist Pro. On 403 we log and return an empty set
so the scheduler doesn't crash.

The v1 API's `since`/`until` params on /api/v1/activities are silently
ignored, so we fetch the latest page (up to PAGE_LIMIT events) and filter
client-side by `event_date` mapped to the user's local TZ.
"""

import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

import httpx

logger = logging.getLogger(__name__)


class TodoistClient:
    BASE_URL = "https://api.todoist.com"
    PAGE_LIMIT = 200

    def __init__(
        self,
        token: str,
        tz: str = "Europe/Moscow",
        http: httpx.AsyncClient | None = None,
    ):
        self.tz = tz
        self.http = http or httpx.AsyncClient(
            base_url=self.BASE_URL,
            headers={"Authorization": f"Bearer {token}"},
            timeout=15.0,
        )

    async def completed_titles_for_day(self, day: date) -> set[str]:
        """Return task titles completed on the given local date (in self.tz).

        Includes recurring tasks via Activity Log. Dedupes if a recurring
        task was completed twice on the same day.
        """
        params = {"event_type": "completed", "limit": self.PAGE_LIMIT}
        r = await self.http.get("/api/v1/activities", params=params)
        if r.status_code == 403:
            logger.warning(
                "Todoist Activity Log returned 403 — likely Pro required. "
                "Returning empty habit set."
            )
            return set()
        r.raise_for_status()
        data = r.json()

        target_tz = ZoneInfo(self.tz)
        titles: set[str] = set()
        for ev in data.get("results", []):
            if ev.get("event_type") != "completed":
                continue
            content = ev.get("extra_data", {}).get("content")
            if not content:
                continue
            event_date = ev.get("event_date")
            if not event_date:
                continue
            try:
                dt_utc = datetime.fromisoformat(event_date.replace("Z", "+00:00"))
            except ValueError:
                continue
            if dt_utc.astimezone(target_tz).date() == day:
                titles.add(content)
        return titles

    async def aclose(self) -> None:
        await self.http.aclose()
