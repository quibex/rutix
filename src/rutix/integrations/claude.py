"""Anthropic Claude API client — used by /eat to parse free-form food text.

Loads the system prompt from prompts/eat.md on every call so the prompt can
be edited without redeploying the bot.
"""

import json
import logging
from pathlib import Path

from anthropic import AsyncAnthropic

from rutix.markdown.daily import MealItem

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_MAX_TOKENS = 2000


class ClaudeClient:
    def __init__(
        self,
        api_key: str,
        prompts_dir: Path | str = "prompts",
        model: str = DEFAULT_MODEL,
        sdk_client: AsyncAnthropic | None = None,
    ):
        self.prompts_dir = Path(prompts_dir)
        self.model = model
        self._sdk = sdk_client or AsyncAnthropic(api_key=api_key)

    async def parse_eat(self, text: str, reference_md: str) -> list[MealItem]:
        eat_prompt = (self.prompts_dir / "eat.md").read_text(encoding="utf-8")
        system = f"{eat_prompt}\n\n# Справочник КБЖУ:\n\n{reference_md}"

        response = await self._sdk.messages.create(
            model=self.model,
            max_tokens=DEFAULT_MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": text}],
        )
        raw = response.content[0].text.strip()

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.error("Claude returned invalid JSON: %r", raw[:500])
            raise ValueError(f"Claude returned invalid JSON: {e}") from e

        if "items" not in payload:
            raise ValueError("Claude response missing 'items' key")

        return [
            MealItem(
                slot="",
                name=str(it["name"]),
                kcal=int(it["kcal"]),
                protein=float(it["protein"]),
                fat=float(it["fat"]),
                carbs=float(it["carbs"]),
            )
            for it in payload["items"]
        ]
