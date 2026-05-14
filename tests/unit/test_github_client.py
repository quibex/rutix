import base64
import json

import httpx
import pytest
import respx

from rutix.integrations.github import FileContent, GitHubClient


@pytest.fixture
def client():
    c = GitHubClient(token="ghp_test", repo="quibex/life")
    yield c


@respx.mock
async def test_read_returns_decoded_text_and_sha(client):
    content = "hello world\n"
    respx.get("https://api.github.com/repos/quibex/life/contents/test.md").mock(
        return_value=httpx.Response(200, json={
            "content": base64.b64encode(content.encode()).decode(),
            "sha": "abc123",
            "encoding": "base64",
        })
    )
    result = await client.read("test.md")
    assert result == FileContent(text="hello world\n", sha="abc123")
    await client.aclose()


@respx.mock
async def test_read_returns_none_when_404(client):
    respx.get("https://api.github.com/repos/quibex/life/contents/missing.md").mock(
        return_value=httpx.Response(404)
    )
    result = await client.read("missing.md")
    assert result is None
    await client.aclose()


@respx.mock
async def test_write_create_file_no_sha(client):
    route = respx.put("https://api.github.com/repos/quibex/life/contents/new.md").mock(
        return_value=httpx.Response(201, json={"commit": {"sha": "newsha"}})
    )
    result = await client.write("new.md", "content", "create new.md")
    assert result == "newsha"
    body = json.loads(route.calls[0].request.content)
    assert "sha" not in body
    assert body["message"] == "create new.md"
    assert base64.b64decode(body["content"]).decode() == "content"
    await client.aclose()


@respx.mock
async def test_write_update_file_with_sha(client):
    route = respx.put("https://api.github.com/repos/quibex/life/contents/existing.md").mock(
        return_value=httpx.Response(200, json={"commit": {"sha": "updated"}})
    )
    result = await client.write("existing.md", "new content", "update", sha="oldsha")
    assert result == "updated"
    body = json.loads(route.calls[0].request.content)
    assert body["sha"] == "oldsha"
    await client.aclose()


@respx.mock
async def test_read_raises_on_5xx(client):
    respx.get("https://api.github.com/repos/quibex/life/contents/x.md").mock(
        return_value=httpx.Response(500)
    )
    with pytest.raises(httpx.HTTPStatusError):
        await client.read("x.md")
    await client.aclose()
