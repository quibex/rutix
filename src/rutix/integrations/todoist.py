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
        r = await self.http.get("/api/v1/activities", params=params)
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
            for ev in data.get("results", [])
            if ev.get("event_type") == "completed"
            and ev.get("extra_data", {}).get("content")
        }

    async def aclose(self) -> None:
        await self.http.aclose()
