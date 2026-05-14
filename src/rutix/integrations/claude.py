"""Anthropic Claude API client — used by /eat to parse free-form food text.

Uses adaptive thinking (Sonnet 4.6+) so the model reasons about whether each
user-named item belongs to the reference or needs an estimate, without us
hardcoding a thinking budget.

Reference markdown is cached as part of the system prompt (5-min ephemeral
TTL, auto-extends on use) — for ~3KB of reference, this saves ~90% on input
tokens for repeated /eat calls within the cache window.
"""

import json
import logging
from pathlib import Path

from anthropic import AsyncAnthropic

from rutix.markdown.daily import MealItem

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_MAX_TOKENS = 8000


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

    async def parse_eat(
        self, text_or_messages: str | list[dict], reference_md: str
    ) -> list[MealItem]:
        """Parse food text into MealItems.

        `text_or_messages` is either a string (single user turn) or a list of
        `{role, content}` dicts (multi-turn — used by the refine flow so Claude
        can see prior parses and treat new turns as corrections, not additions).
        """
        eat_prompt = (self.prompts_dir / "eat.md").read_text(encoding="utf-8")

        if isinstance(text_or_messages, str):
            messages = [{"role": "user", "content": text_or_messages}]
        else:
            messages = text_or_messages

        # Stable system prompt — eligible for prompt caching (Sonnet 4.6 min 2048 tok).
        # Reference rarely changes, so most /eat calls hit the cache.
        system_blocks = [
            {
                "type": "text",
                "text": f"{eat_prompt}\n\n# Справочник КБЖУ:\n\n{reference_md}",
                "cache_control": {"type": "ephemeral"},
            }
        ]

        response = await self._sdk.messages.create(
            model=self.model,
            max_tokens=DEFAULT_MAX_TOKENS,
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
            system=system_blocks,
            messages=messages,
        )

        # With adaptive thinking, response.content interleaves thinking + text blocks.
        # We only want the final text block(s).
        text_blocks = [b.text for b in response.content if b.type == "text"]
        raw = "\n".join(text_blocks).strip()

        if not raw:
            raise ValueError("Claude returned empty text response")

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
