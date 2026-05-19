"""Anthropic Claude API client — used by /eat to parse free-form food text.

Uses adaptive thinking (Sonnet 4.6+) so the model reasons about whether each
user-named item belongs to the reference or needs an estimate.

JSON output is enforced via Anthropic's structured outputs feature
(`output_config.format` with json_schema). This is a hard, API-level
constraint — unlike soft prompt instructions ("return JSON only"), which
the model has been observed to ignore by wrapping JSON in ```json fences.

Reference markdown is cached as part of the system prompt (5-min ephemeral
TTL, auto-extends on use) — for ~3KB of reference, this saves ~90% on input
tokens for repeated /eat calls within the cache window.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from anthropic import AsyncAnthropic

from rutix.markdown.daily import MealItem

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_MAX_TOKENS = 16000  # adaptive thinking + multimodal can use a lot of tokens

# JSON schema for /eat parser output. The Anthropic API enforces this at the
# response layer when passed via output_config.format — model is forced to
# emit valid JSON matching this shape (no markdown wrapping, no extra fields).
EAT_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Название блюда с уточнениями"},
                    "kcal": {"type": "integer", "description": "Калорий"},
                    "protein": {"type": "number", "description": "Белков, г"},
                    "fat": {"type": "number", "description": "Жиров, г"},
                    "carbs": {"type": "number", "description": "Углеводов, г"},
                    "source": {
                        "type": "string",
                        "enum": ["reference", "estimate"],
                        "description": (
                            "reference — взято из справочника; estimate — оценка модели"
                        ),
                    },
                },
                "required": ["name", "kcal", "protein", "fat", "carbs", "source"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}


WEEK_CLOSE_SCHEMA = {
    "type": "object",
    "properties": {
        "habits_aggregate": {
            "type": "object",
            "description": (
                "Map of habit label (exact string from habits.md) → count of days "
                "the habit was completed this week."
            ),
            "additionalProperties": {"type": "integer"},
        },
        "totals": {
            "type": "object",
            "properties": {
                "avg_kcal": {"type": ["integer", "null"]},
                "days_with_food_data": {"type": "integer"},
            },
            "required": ["avg_kcal", "days_with_food_data"],
            "additionalProperties": False,
        },
        "trends": {
            "type": "object",
            "properties": {
                "kcal": {"type": ["string", "null"], "enum": ["↑", "↓", "=", None]},
                "habits_completion": {
                    "type": ["string", "null"],
                    "enum": ["↑", "↓", "=", None],
                },
            },
            "required": ["kcal", "habits_completion"],
            "additionalProperties": False,
        },
        "score": {"type": "integer", "minimum": 1, "maximum": 10},
        "what_worked": {"type": "array", "items": {"type": "string"}},
        "what_failed": {"type": "array", "items": {"type": "string"}},
        "focus_next_week": {"type": "array", "items": {"type": "string"}},
        "next_week_daily": {
            "type": "object",
            "description": (
                "Map of YYYY-MM-DD → full markdown content for new daily file. "
                "Keys must be exactly the 7 dates passed in next_week_dates."
            ),
            "additionalProperties": {"type": "string"},
        },
        "user_message": {"type": "string"},
    },
    "required": [
        "habits_aggregate",
        "totals",
        "trends",
        "score",
        "what_worked",
        "what_failed",
        "focus_next_week",
        "next_week_daily",
        "user_message",
    ],
    "additionalProperties": False,
}


@dataclass
class WeekClose:
    habits_aggregate: dict[str, int]
    avg_kcal: int | None
    days_with_food_data: int
    trend_kcal: str | None
    trend_habits: str | None
    score: int
    what_worked: list[str]
    what_failed: list[str]
    focus_next_week: list[str]
    next_week_daily: dict[str, str]
    user_message: str
    raw: dict = field(default_factory=dict)


CLASSIFY_HABITS_SCHEMA = {
    "type": "object",
    "properties": {
        "matched_habits": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Точные лейблы привычек, для которых нашлось совпадение",
        },
        "unmatched_completions": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Точные титлы Todoist-задач, не совпавших ни с одной привычкой",
        },
    },
    "required": ["matched_habits", "unmatched_completions"],
    "additionalProperties": False,
}


def _strip_markdown_fences(raw: str) -> str:
    """Defense-in-depth: even with output_config.format enforcement, occasionally
    a model can wrap JSON in ```json fences. Strip them if present.
    """
    raw = raw.strip()
    # ```json\n{...}\n```  or  ```\n{...}\n```
    m = re.match(r"^```(?:[a-zA-Z]+)?\s*\n(.*?)\n\s*```\s*$", raw, re.DOTALL)
    if m:
        return m.group(1).strip()
    return raw


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
        update to that explicit state.
        """
        eat_prompt = (self.prompts_dir / "eat.md").read_text(encoding="utf-8")

        # Stable system prompt — eligible for prompt caching (Sonnet 4.6 min 2048 tok).
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
            user_content = (
                ([{"type": "text", "text": state_prefix}] + new_input)
                if state_prefix
                else new_input
            )

        response = await self._sdk.messages.create(
            model=self.model,
            max_tokens=DEFAULT_MAX_TOKENS,
            thinking={"type": "adaptive"},
            output_config={
                "effort": "high",
                "format": {"type": "json_schema", "schema": EAT_SCHEMA},
            },
            system=system_blocks,
            messages=[{"role": "user", "content": user_content}],
        )

        # With adaptive thinking, response.content interleaves thinking + text blocks.
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

        # Defense in depth: strip any markdown wrapping the model might have
        # produced despite output_config.format. Should be a no-op in normal cases.
        raw = _strip_markdown_fences(raw)

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

    async def classify_completions(
        self,
        habit_labels: list[str],
        completions: list[str] | set[str],
    ) -> tuple[set[str], list[str]]:
        """Semantic match Todoist completions against habit labels.

        Returns (matched, unmatched):
        - `matched`: subset of `habit_labels` for which a Todoist completion
          was found. Hallucinated labels (not present in input) are filtered out.
        - `unmatched`: Todoist titles that didn't match any habit, preserving
          model-emitted order.

        Short-circuits without an API call when either input is empty:
        - no habits → everything unmatched
        - no completions → nothing matched, nothing unmatched
        """
        completions_list = list(completions)
        if not habit_labels or not completions_list:
            return set(), completions_list

        prompt = (self.prompts_dir / "classify_habits.md").read_text(encoding="utf-8")

        user_payload = json.dumps(
            {"habits": habit_labels, "completions": completions_list},
            ensure_ascii=False,
            indent=2,
        )

        response = await self._sdk.messages.create(
            model=self.model,
            max_tokens=2048,
            output_config={
                "format": {"type": "json_schema", "schema": CLASSIFY_HABITS_SCHEMA},
            },
            system=[{"type": "text", "text": prompt}],
            messages=[{"role": "user", "content": user_payload}],
        )

        text_blocks = [b.text for b in response.content if b.type == "text"]
        raw = _strip_markdown_fences("\n".join(text_blocks).strip())
        if not raw:
            raise ValueError("Claude classify returned empty text")

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.error("Claude classify returned invalid JSON: %r", raw[:500])
            raise ValueError(f"Claude classify не-JSON: {e}") from e

        habit_set = set(habit_labels)
        matched = {h for h in payload.get("matched_habits", []) if h in habit_set}
        unmatched = [str(c) for c in payload.get("unmatched_completions", [])]
        return matched, unmatched

    async def close_week(
        self,
        week_id: str,
        dates: list[str],
        next_week_dates: list[str],
        habits_md: str,
        goals_md: str,
        prev_weekly_md: str,
        daily_files: dict[str, str],
        todoist_completions: dict[str, list[str]],
    ) -> WeekClose:
        """Run end-of-week closure through Claude.

        Returns a WeekClose dataclass with semantically-matched habit aggregates,
        editorial sections, 7 daily-file templates for the next week, and a
        short Telegram message for the user.
        """
        prompt = (self.prompts_dir / "close_week.md").read_text(encoding="utf-8")

        user_payload = json.dumps(
            {
                "week_id": week_id,
                "dates": dates,
                "next_week_dates": next_week_dates,
                "habits_md": habits_md,
                "goals_md": goals_md,
                "prev_weekly_md": prev_weekly_md,
                "daily_files": daily_files,
                "todoist_completions": todoist_completions,
            },
            ensure_ascii=False,
            indent=2,
        )

        response = await self._sdk.messages.create(
            model=self.model,
            max_tokens=DEFAULT_MAX_TOKENS,
            thinking={"type": "adaptive"},
            output_config={
                "effort": "high",
                "format": {"type": "json_schema", "schema": WEEK_CLOSE_SCHEMA},
            },
            system=[
                {
                    "type": "text",
                    "text": prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_payload}],
        )

        text_blocks = [b.text for b in response.content if b.type == "text"]
        raw = _strip_markdown_fences("\n".join(text_blocks).strip())
        if not raw:
            raise ValueError("Claude close_week returned empty text")

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.error("Claude close_week invalid JSON: %r", raw[:1000])
            raise ValueError(f"Claude close_week не-JSON: {e}") from e

        # Validate keys of next_week_daily — must match exactly the requested dates.
        # If the model hallucinated or skipped a date, fail loudly: better to
        # let the cron error and retry than write a partially correct week.
        next_keys = set(payload["next_week_daily"].keys())
        expected_keys = set(next_week_dates)
        if next_keys != expected_keys:
            missing = expected_keys - next_keys
            extra = next_keys - expected_keys
            raise ValueError(
                f"close_week next_week_daily keys mismatch: missing={missing}, extra={extra}"
            )

        return WeekClose(
            habits_aggregate={str(k): int(v) for k, v in payload["habits_aggregate"].items()},
            avg_kcal=payload["totals"]["avg_kcal"],
            days_with_food_data=int(payload["totals"]["days_with_food_data"]),
            trend_kcal=payload["trends"]["kcal"],
            trend_habits=payload["trends"]["habits_completion"],
            score=int(payload["score"]),
            what_worked=[str(s) for s in payload["what_worked"]],
            what_failed=[str(s) for s in payload["what_failed"]],
            focus_next_week=[str(s) for s in payload["focus_next_week"]],
            next_week_daily={str(k): str(v) for k, v in payload["next_week_daily"].items()},
            user_message=str(payload["user_message"]),
            raw=payload,
        )
