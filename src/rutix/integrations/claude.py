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
DEFAULT_MAX_TOKENS = 16000  # adaptive thinking + multimodal can use a lot of tokens


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
        self,
        new_input: str | list[dict],
        reference_md: str,
        current_items: list[dict] | None = None,
    ) -> list[MealItem]:
        """Parse food text into MealItems.

        `new_input` is either a string (text-only turn) or a list of content
        blocks (multimodal — text + image blocks).

        `current_items` is the list of already-parsed items from prior turns
        in the same session. When non-empty, it's prepended to the user message
        as a "ТЕКУЩИЙ СПИСОК:" block so Claude treats the new turn as an
        update to that explicit state (rather than inferring from chat history).
        This avoids the conversation-history ambiguity that was causing items
        to silently disappear on follow-up turns.
        """
        eat_prompt = (self.prompts_dir / "eat.md").read_text(encoding="utf-8")

        # Stable system prompt — eligible for prompt caching (Sonnet 4.6 min 2048 tok).
        # Reference rarely changes, so most /eat calls hit the cache.
        system_blocks = [
            {
                "type": "text",
                "text": f"{eat_prompt}\n\n# Справочник КБЖУ:\n\n{reference_md}",
                "cache_control": {"type": "ephemeral"},
            }
        ]

        # Build the user message: optionally prefix with explicit current state,
        # then the new input (text or multimodal blocks).
        state_prefix = ""
        if current_items:
            state_prefix = (
                "ТЕКУЩИЙ СПИСОК (что уже распарсено в этой сессии):\n"
                + json.dumps({"items": current_items}, ensure_ascii=False, indent=2)
                + "\n\nНОВОЕ СООБЩЕНИЕ ОТ ПОЛЬЗОВАТЕЛЯ:\n"
            )

        if isinstance(new_input, str):
            user_content = (state_prefix + new_input) if state_prefix else new_input
        else:
            # Multimodal: prepend state prefix as a text block before image blocks
            user_content = (
                ([{"type": "text", "text": state_prefix}] + new_input)
                if state_prefix
                else new_input
            )

        response = await self._sdk.messages.create(
            model=self.model,
            max_tokens=DEFAULT_MAX_TOKENS,
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
            system=system_blocks,
            messages=[{"role": "user", "content": user_content}],
        )

        # With adaptive thinking, response.content interleaves thinking + text blocks.
        # We only want the final text block(s).
        text_blocks = [b.text for b in response.content if b.type == "text"]
        raw = "\n".join(text_blocks).strip()

        if not raw:
            stop_reason = getattr(response, "stop_reason", None)
            logger.error(
                "Claude returned empty text. stop_reason=%s, blocks=%s",
                stop_reason,
                [getattr(b, "type", "?") for b in response.content],
            )
            if stop_reason == "max_tokens":
                raise ValueError(
                    "модель упёрлась в лимит токенов "
                    "(адаптивное мышление + большой ввод). Попробуйте короче."
                )
            raise ValueError(
                f"модель не вернула текст (stop_reason={stop_reason}). Попробуйте ещё раз."
            )

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.error("Claude returned invalid JSON: %r", raw[:1000])
            raise ValueError(f"Claude вернул не-JSON: {e}") from e

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
                source=str(it.get("source", "")),
            )
            for it in payload["items"]
        ]
