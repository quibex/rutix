from datetime import date

import httpx
import pytest
import respx

from rutix.integrations.todoist import TodoistClient


@pytest.fixture
def client():
    return TodoistClient(token="tod_test", tz="Europe/Moscow")


def _ev(content: str, event_date: str, event_type: str = "completed") -> dict:
    return {
        "event_type": event_type,
        "event_date": event_date,
        "extra_data": {"content": content},
    }


@respx.mock
async def test_completed_titles_filters_by_local_date(client):
    """Even if the API ignores date_from/date_to, the client filters event_date
    in MSK to the target day."""
    respx.get("https://api.todoist.com/api/v1/activities").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    # 2026-05-14 13:49 UTC = 16:49 MSK 2026-05-14 — IN
                    _ev("📚 Anki", "2026-05-14T13:49:39.050738Z"),
                    # 2026-05-14 23:30 UTC = 02:30 MSK 2026-05-15 — OUT
                    _ev("🌅 Skincare AM", "2026-05-14T23:30:00.000000Z"),
                    # 2026-05-13 22:00 UTC = 01:00 MSK 2026-05-14 — IN
                    _ev("🌙 Skincare PM", "2026-05-13T22:00:00.000000Z"),
                    # dedupe — second Anki completion same day
                    _ev("📚 Anki", "2026-05-14T05:00:00.000000Z"),
                    # different event type — OUT (defensive: API may return mixed types)
                    _ev("🥤 Protein", "2026-05-14T10:00:00.000000Z", event_type="updated"),
                ],
                "next_cursor": None,
            },
        )
    )

    titles = await client.completed_titles_for_day(date(2026, 5, 14))
    assert titles == {"📚 Anki", "🌙 Skincare PM"}
    await client.aclose()


@respx.mock
async def test_request_sends_correct_query_params(client):
    """Uses object_event_types (the param /api/v1/activities actually honors)
    + the day window in UTC for date_from/date_to + limit clamped to API max 100."""
    route = respx.get("https://api.todoist.com/api/v1/activities").mock(
        return_value=httpx.Response(200, json={"results": [], "next_cursor": None})
    )
    await client.completed_titles_for_day(date(2026, 5, 14))
    params = dict(route.calls[0].request.url.params)
    assert params["object_event_types"] == '["item:completed"]'
    assert params["limit"] == "100"
    # MSK is UTC+3 → day 2026-05-14 MSK = [2026-05-13T21:00Z, 2026-05-14T21:00Z)
    assert params["date_from"] == "2026-05-13T21:00:00Z"
    assert params["date_to"] == "2026-05-14T21:00:00Z"
    assert "cursor" not in params  # first page
    await client.aclose()


@respx.mock
async def test_paginates_via_next_cursor(client):
    """When next_cursor is returned, the client paginates and merges results."""
    route = respx.get("https://api.todoist.com/api/v1/activities").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "results": [_ev("📚 Anki", "2026-05-14T10:00:00Z")],
                    "next_cursor": "page2",
                },
            ),
            httpx.Response(
                200,
                json={
                    "results": [_ev("🌅 Skincare AM", "2026-05-14T07:00:00Z")],
                    "next_cursor": None,
                },
            ),
        ]
    )
    titles = await client.completed_titles_for_day(date(2026, 5, 14))
    assert titles == {"📚 Anki", "🌅 Skincare AM"}
    assert route.call_count == 2
    second_call_params = dict(route.calls[1].request.url.params)
    assert second_call_params["cursor"] == "page2"
    await client.aclose()


@respx.mock
async def test_pagination_stops_at_max_pages(client):
    """Defensive cap — don't loop forever if the API keeps returning cursors."""
    respx.get("https://api.todoist.com/api/v1/activities").mock(
        return_value=httpx.Response(
            200, json={"results": [_ev("x", "2026-05-14T10:00:00Z")], "next_cursor": "more"}
        )
    )
    titles = await client.completed_titles_for_day(date(2026, 5, 14))
    assert titles == {"x"}
    # MAX_PAGES sanity — we stop after the cap regardless of cursor
    assert respx.calls.call_count == client.MAX_PAGES
    await client.aclose()


@respx.mock
async def test_pagination_stops_when_results_empty(client):
    respx.get("https://api.todoist.com/api/v1/activities").mock(
        return_value=httpx.Response(200, json={"results": [], "next_cursor": "x"})
    )
    titles = await client.completed_titles_for_day(date(2026, 5, 14))
    assert titles == set()
    assert respx.calls.call_count == 1
    await client.aclose()


@respx.mock
async def test_completed_titles_returns_empty_set_on_403(client):
    """403 = Activity Log requires Pro. Don't crash — log and return empty."""
    respx.get("https://api.todoist.com/api/v1/activities").mock(
        return_value=httpx.Response(403, json={"error": "Pro required"})
    )
    titles = await client.completed_titles_for_day(date(2026, 5, 14))
    assert titles == set()
    await client.aclose()


@respx.mock
async def test_completed_titles_raises_on_5xx(client):
    respx.get("https://api.todoist.com/api/v1/activities").mock(return_value=httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        await client.completed_titles_for_day(date(2026, 5, 14))
    await client.aclose()


@respx.mock
async def test_skips_events_with_missing_extra_data(client):
    """Defensive: events without extra_data.content shouldn't crash."""
    respx.get("https://api.todoist.com/api/v1/activities").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {"event_type": "completed", "event_date": "2026-05-14T10:00:00Z"},
                    {
                        "event_type": "completed",
                        "event_date": "2026-05-14T11:00:00Z",
                        "extra_data": {},
                    },
                    _ev("📚 Anki", "2026-05-14T12:00:00Z"),
                ],
                "next_cursor": None,
            },
        )
    )
    titles = await client.completed_titles_for_day(date(2026, 5, 14))
    assert titles == {"📚 Anki"}
    await client.aclose()
