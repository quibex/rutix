"""GitHub Contents API client.

Atomic per-file read/write: GET → modify → PUT with SHA.
SHA-mismatch (concurrent edit) → caller decides whether to retry.
"""

import base64
from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class FileContent:
    """Decoded text + sha needed to update the file.

    `sha` is `None` for an in-memory file that doesn't exist on the remote yet
    (a freshly scaffolded daily file). Passing `sha=None` to `write` creates the
    file instead of updating it.
    """

    text: str
    sha: str | None


class GitHubClient:
    BASE_URL = "https://api.github.com"

    def __init__(self, token: str, repo: str, http: httpx.AsyncClient | None = None):
        self.repo = repo
        self.http = http or httpx.AsyncClient(
            base_url=self.BASE_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=15.0,
        )

    async def read(self, path: str) -> FileContent | None:
        """Return None if file doesn't exist (404). Raises on other errors."""
        r = await self.http.get(f"/repos/{self.repo}/contents/{path}")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        data = r.json()
        text = base64.b64decode(data["content"]).decode("utf-8")
        return FileContent(text=text, sha=data["sha"])

    async def write(self, path: str, text: str, message: str, sha: str | None = None) -> str:
        """Create or update the file. Returns the new commit SHA."""
        body: dict = {
            "message": message,
            "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
        }
        if sha:
            body["sha"] = sha
        r = await self.http.put(f"/repos/{self.repo}/contents/{path}", json=body)
        r.raise_for_status()
        return r.json()["commit"]["sha"]

    async def delete(self, path: str, message: str, sha: str) -> str:
        """Delete a file. Returns the new commit SHA."""
        r = await self.http.request(
            "DELETE",
            f"/repos/{self.repo}/contents/{path}",
            json={"message": message, "sha": sha},
        )
        r.raise_for_status()
        return r.json()["commit"]["sha"]

    async def aclose(self) -> None:
        await self.http.aclose()
