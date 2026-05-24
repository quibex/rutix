from datetime import date

import httpx
import pytest
import respx

from rutix.integrations.todoist import TodoistClient


@pytest.fixture
def client():
    # retry_backoff_base=0 so retry tests don't actually sleep
    return TodoistClient(token="tod_test", tz="Europe/Moscow", retry_backoff_base=0)


def _ev(content: str, event_date: str, event_type: str = "completed") -> dict:
    return {
        "event_type": event_type,
        "event_date": event_date,
        "extra_data": {"content": content},
    }


@respx.mock
async def test_completed_titles_filters_by_subjective_day(client):
    """Filters event_date by the *subjective* day in self.tz — the 3am-boundary
    window matching subjective_today() elsewhere in the bot. A habit ticked at
    02:30 MSK on 2026-05-15 still belongs to subjective day 2026-05-14."""
    respx.get("https://api.todoist.com/api/v1/activities").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    # 2026-05-14 13:49 UTC = 16:49 MSK 2026-05-14 — IN
                    _ev("📚 Anki", "2026-05-14T13:49:39.050738Z"),
                    # 2026-05-14 23:30 UTC = 02:30 MSK 2026-05-15 — IN (subjective 2026-05-14)
                    _ev("🌅 Skincare AM", "2026-05-14T23:30:00.000000Z"),
                    # 2026-05-13 22:00 UTC = 01:00 MSK 2026-05-14 — OUT (subjective 2026-05-13)
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
    assert titles == {"📚 Anki", "🌅 Skincare AM"}
    await client.aclose()


@respx.mock
async def test_request_sends_correct_query_params(client):
    """Uses object_event_types (the param /api/v1/activities actually honors)
    + the *subjective* day window in UTC for date_from/date_to + limit at 100."""
    route = respx.get("https://api.todoist.com/api/v1/activities").mock(
        return_value=httpx.Response(200, json={"results": [], "next_cursor": None})
    )
    await client.completed_titles_for_day(date(2026, 5, 14))
    params = dict(route.calls[0].request.url.params)
    assert params["object_event_types"] == '["item:completed"]'
    assert params["limit"] == "100"
    # Subjective day 2026-05-14 in MSK = [03:00 MSK 2026-05-14, 03:00 MSK 2026-05-15)
    # = [2026-05-14T00:00Z, 2026-05-15T00:00Z) (MSK is UTC+3)
    assert params["date_from"] == "2026-05-14T00:00:00Z"
    assert params["date_to"] == "2026-05-15T00:00:00Z"
    assert "cursor" not in params  # first page
    await client.aclose()


@respx.mock
async def test_event_at_subjective_boundary_3am_belongs_to_new_day(client):
    """A completion at exactly 03:00:00 MSK 2026-05-15 = subjective 2026-05-15,
    NOT 2026-05-14 — the boundary is half-open."""
    respx.get("https://api.todoist.com/api/v1/activities").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    # 2026-05-15 00:00 UTC = 03:00 MSK 2026-05-15 — subjective 2026-05-15
                    _ev("📚 Anki", "2026-05-15T00:00:00.000000Z"),
                ],
                "next_cursor": None,
            },
        )
    )
    titles = await client.completed_titles_for_day(date(2026, 5, 14))
    assert titles == set()
    await client.aclose()


@respx.mock
async def test_event_just_before_3am_still_belongs_to_previous_subjective_day(client):
    """02:59 MSK 2026-05-15 = subjective 2026-05-14 — the user hasn't slept yet."""
    respx.get("https://api.todoist.com/api/v1/activities").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    # 2026-05-14 23:59 UTC = 02:59 MSK 2026-05-15
                    _ev("📚 Anki", "2026-05-14T23:59:00.000000Z"),
                ],
                "next_cursor": None,
            },
        )
    )
    titles = await client.completed_titles_for_day(date(2026, 5, 14))
    assert titles == {"📚 Anki"}
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
async def test_completed_titles_raises_on_5xx_after_exhausting_retries(client):
    """All retry attempts return 5xx → eventually raise so the cron sees the failure."""
    route = respx.get("https://api.todoist.com/api/v1/activities").mock(
        return_value=httpx.Response(503)
    )
    with pytest.raises(httpx.HTTPStatusError):
        await client.completed_titles_for_day(date(2026, 5, 14))
    # default 3 attempts (initial + 2 retries)
    assert route.call_count == 3
    await client.aclose()


@respx.mock
async def test_5xx_then_200_succeeds_via_retry(client):
    """Transient 5xx → retry → 200 should succeed without raising."""
    route = respx.get("https://api.todoist.com/api/v1/activities").mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(502),
            httpx.Response(
                200,
                json={
                    "results": [_ev("📚 Anki", "2026-05-14T10:00:00Z")],
                    "next_cursor": None,
                },
            ),
        ]
    )
    titles = await client.completed_titles_for_day(date(2026, 5, 14))
    assert titles == {"📚 Anki"}
    assert route.call_count == 3
    await client.aclose()


@respx.mock
async def test_5xx_retry_resets_after_successful_page(client):
    """Retry budget is per-request, not per-call — second page failure should
    also get its own retries."""
    route = respx.get("https://api.todoist.com/api/v1/activities").mock(
        side_effect=[
            # page 1 succeeds with cursor
            httpx.Response(
                200,
                json={
                    "results": [_ev("📚 Anki", "2026-05-14T10:00:00Z")],
                    "next_cursor": "page2",
                },
            ),
            # page 2 fails twice then succeeds
            httpx.Response(503),
            httpx.Response(503),
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
    assert route.call_count == 4
    await client.aclose()


@respx.mock
async def test_4xx_not_retried(client):
    """4xx other than 403 should not be retried — it's a client bug, not transient."""
    route = respx.get("https://api.todoist.com/api/v1/activities").mock(
        return_value=httpx.Response(401)
    )
    with pytest.raises(httpx.HTTPStatusError):
        await client.completed_titles_for_day(date(2026, 5, 14))
    assert route.call_count == 1
    await client.aclose()


@respx.mock
async def test_403_not_retried(client):
    """403 = Pro required, retrying won't help; should short-circuit on first call."""
    route = respx.get("https://api.todoist.com/api/v1/activities").mock(
        return_value=httpx.Response(403, json={"error": "Pro required"})
    )
    titles = await client.completed_titles_for_day(date(2026, 5, 14))
    assert titles == set()
    assert route.call_count == 1
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
