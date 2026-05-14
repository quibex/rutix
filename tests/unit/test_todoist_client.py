from datetime import date

import httpx
import pytest
import respx

from rutix.integrations.todoist import TodoistClient


@pytest.fixture
def client():
    return TodoistClient(token="tod_test")


@respx.mock
async def test_completed_titles_for_day_returns_set(client):
    respx.get("https://api.todoist.com/api/v1/activities").mock(
        return_value=httpx.Response(200, json={
            "results": [
                {"event_type": "completed", "extra_data": {"content": "📚 Anki"}},
                {"event_type": "completed", "extra_data": {"content": "🌅 Skincare AM"}},
                {"event_type": "completed", "extra_data": {"content": "📚 Anki"}},  # dedupe
            ],
        })
    )
    titles = await client.completed_titles_for_day(date(2026, 5, 14))
    assert titles == {"📚 Anki", "🌅 Skincare AM"}
    await client.aclose()


@respx.mock
async def test_completed_titles_request_uses_iso_window(client):
    route = respx.get("https://api.todoist.com/api/v1/activities").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    await client.completed_titles_for_day(date(2026, 5, 14))
    params = dict(route.calls[0].request.url.params)
    assert params["event_type"] == "completed"
    assert params["since"] == "2026-05-14T00:00:00"
    assert params["until"] == "2026-05-14T23:59:59"
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
    respx.get("https://api.todoist.com/api/v1/activities").mock(
        return_value=httpx.Response(500)
    )
    with pytest.raises(httpx.HTTPStatusError):
        await client.completed_titles_for_day(date(2026, 5, 14))
    await client.aclose()
