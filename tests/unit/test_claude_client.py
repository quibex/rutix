import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from rutix.integrations.claude import ClaudeClient
from rutix.markdown.daily import MealItem


def _text_block(s: str) -> MagicMock:
    """Mock a content block with type='text' and the given text."""
    return MagicMock(type="text", text=s)


def _thinking_block(s: str) -> MagicMock:
    """Mock a thinking block (precedes text blocks when adaptive thinking is on)."""
    return MagicMock(type="thinking", thinking=s)


@pytest.fixture
def fake_anthropic():
    client = MagicMock()
    client.messages = MagicMock()
    client.messages.create = AsyncMock()
    return client


@pytest.fixture
def claude(tmp_path, fake_anthropic, monkeypatch):
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "eat.md").write_text("EAT_SYSTEM\n", encoding="utf-8")
    (prompts_dir / "classify_habits.md").write_text("CLASSIFY_SYSTEM\n", encoding="utf-8")
    return ClaudeClient(
        api_key="sk-ant-test",
        prompts_dir=prompts_dir,
        sdk_client=fake_anthropic,
    )


async def test_parse_eat_returns_meal_items(claude, fake_anthropic):
    payload = {
        "items": [
            {
                "name": "Шаурма",
                "kcal": 450,
                "protein": 22.0,
                "fat": 18.0,
                "carbs": 45.0,
                "source": "estimate",
            },
            {
                "name": "Кола 0.4л",
                "kcal": 170,
                "protein": 0,
                "fat": 0,
                "carbs": 42,
                "source": "reference",
            },
        ]
    }
    msg = MagicMock()
    # adaptive thinking → thinking blocks come first, then text
    msg.content = [_thinking_block("reasoning..."), _text_block(json.dumps(payload))]
    fake_anthropic.messages.create.return_value = msg

    items = await claude.parse_eat("шаурма + кола", reference_md="## ВкусВилл\n...")

    assert items == [
        MealItem("", "Шаурма", 450, 22.0, 18.0, 45.0, source="estimate"),
        MealItem("", "Кола 0.4л", 170, 0.0, 0.0, 42.0, source="reference"),
    ]
    fake_anthropic.messages.create.assert_awaited_once()
    call_kwargs = fake_anthropic.messages.create.call_args.kwargs
    # System is now a list of blocks with cache_control
    system_blocks = call_kwargs["system"]
    assert isinstance(system_blocks, list)
    assert "EAT_SYSTEM" in system_blocks[0]["text"]
    assert "## ВкусВилл" in system_blocks[0]["text"]
    assert system_blocks[0]["cache_control"] == {"type": "ephemeral"}
    # Adaptive thinking enabled
    assert call_kwargs["thinking"] == {"type": "adaptive"}
    assert call_kwargs["messages"][0]["content"] == "шаурма + кола"


async def test_parse_eat_with_current_items_prefixes_explicit_state(claude, fake_anthropic):
    """When current_items is given, the user message should embed an explicit state block."""
    payload = {
        "items": [
            {
                "name": "Котлета",
                "kcal": 392,
                "protein": 26,
                "fat": 19,
                "carbs": 30,
                "source": "reference",
            },
            {
                "name": "Чиабатта",
                "kcal": 603,
                "protein": 18.9,
                "fat": 33.8,
                "carbs": 55.9,
                "source": "reference",
            },
        ]
    }
    fake_anthropic.messages.create.return_value = MagicMock(
        content=[_text_block(json.dumps(payload))]
    )

    current = [
        {
            "name": "Котлета",
            "kcal": 392,
            "protein": 26,
            "fat": 19,
            "carbs": 30,
            "source": "reference",
        }
    ]
    await claude.parse_eat("ещё чиабатта", reference_md="", current_items=current)

    call_kwargs = fake_anthropic.messages.create.call_args.kwargs
    user_content = call_kwargs["messages"][0]["content"]
    assert isinstance(user_content, str)
    assert "ТЕКУЩИЙ СПИСОК" in user_content
    assert "Котлета" in user_content
    assert "ещё чиабатта" in user_content


async def test_parse_eat_no_current_items_sends_plain_text(claude, fake_anthropic):
    """Without current_items, user message is just the new input — no state prefix."""
    payload = {"items": []}
    fake_anthropic.messages.create.return_value = MagicMock(
        content=[_text_block(json.dumps(payload))]
    )

    await claude.parse_eat("шаурма", reference_md="")

    user_content = fake_anthropic.messages.create.call_args.kwargs["messages"][0]["content"]
    assert user_content == "шаурма"
    assert "ТЕКУЩИЙ СПИСОК" not in user_content


async def test_parse_eat_raises_on_malformed_json(claude, fake_anthropic):
    msg = MagicMock(content=[_text_block("not a json")])
    fake_anthropic.messages.create.return_value = msg

    with pytest.raises(ValueError, match="не-JSON"):
        await claude.parse_eat("eggs", reference_md="")


async def test_parse_eat_raises_on_missing_items_key(claude, fake_anthropic):
    msg = MagicMock(content=[_text_block('{"foo": "bar"}')])
    fake_anthropic.messages.create.return_value = msg

    with pytest.raises(ValueError, match="missing 'items'"):
        await claude.parse_eat("eggs", reference_md="")


async def test_parse_eat_strips_markdown_fences(claude, fake_anthropic):
    """Defense-in-depth: even with output_config.format, model occasionally wraps
    JSON in ```json fences. Strip them before json.loads."""
    payload = {
        "items": [
            {"name": "X", "kcal": 100, "protein": 0, "fat": 0, "carbs": 0, "source": "estimate"}
        ]
    }
    raw_with_fence = "```json\n" + json.dumps(payload) + "\n```"
    msg = MagicMock(content=[_text_block(raw_with_fence)])
    fake_anthropic.messages.create.return_value = msg

    items = await claude.parse_eat("x", reference_md="")
    assert len(items) == 1
    assert items[0].name == "X"


async def test_parse_eat_request_includes_json_schema(claude, fake_anthropic):
    """The API call must include output_config.format with json_schema —
    this is what enforces no-markdown-wrapping at the API layer."""
    payload = {"items": []}
    fake_anthropic.messages.create.return_value = MagicMock(
        content=[_text_block(json.dumps(payload))]
    )
    await claude.parse_eat("x", reference_md="")

    output_config = fake_anthropic.messages.create.call_args.kwargs["output_config"]
    assert "format" in output_config
    assert output_config["format"]["type"] == "json_schema"
    assert "items" in output_config["format"]["schema"]["properties"]


async def test_parse_eat_filters_out_thinking_blocks(claude, fake_anthropic):
    """Only text blocks are parsed — thinking blocks are ignored."""
    payload = {"items": []}
    msg = MagicMock()
    msg.content = [
        _thinking_block("Let me think about what they ate..."),
        _text_block(json.dumps(payload)),
    ]
    fake_anthropic.messages.create.return_value = msg

    items = await claude.parse_eat("nothing", reference_md="")
    assert items == []


# --- classify_completions ---


async def test_classify_completions_returns_matched_and_unmatched(claude, fake_anthropic):
    payload = {
        "matched_habits": ["🥤 Протеин", "🌅 Skincare AM"],
        "unmatched_completions": ["do the laundry", "Eng HW"],
    }
    fake_anthropic.messages.create.return_value = MagicMock(
        content=[_text_block(json.dumps(payload))]
    )

    matched, unmatched = await claude.classify_completions(
        habit_labels=["🥤 Протеин", "🌅 Skincare AM", "📚 Anki"],
        completions=["🥤 Protein", "🌅 Skincare AM", "do the laundry", "Eng HW"],
    )

    assert matched == {"🥤 Протеин", "🌅 Skincare AM"}
    assert unmatched == ["do the laundry", "Eng HW"]

    call_kwargs = fake_anthropic.messages.create.call_args.kwargs
    # Schema enforced at API layer
    assert call_kwargs["output_config"]["format"]["type"] == "json_schema"
    schema = call_kwargs["output_config"]["format"]["schema"]
    assert "matched_habits" in schema["properties"]
    assert "unmatched_completions" in schema["properties"]
    # System prompt loaded from prompts/classify_habits.md and embeds inputs
    system_text = call_kwargs["system"][0]["text"]
    assert "CLASSIFY_SYSTEM" in system_text


async def test_classify_completions_filters_hallucinated_habits(claude, fake_anthropic):
    """If the model returns a 'matched' label that wasn't in the input habit list,
    it's dropped — we don't want to write checkboxes for habits the user doesn't have."""
    payload = {
        "matched_habits": ["🌅 Skincare AM", "💪 Random hallucinated habit"],
        "unmatched_completions": [],
    }
    fake_anthropic.messages.create.return_value = MagicMock(
        content=[_text_block(json.dumps(payload))]
    )

    matched, unmatched = await claude.classify_completions(
        habit_labels=["🌅 Skincare AM", "📚 Anki"],
        completions=["🌅 Skincare AM"],
    )

    assert matched == {"🌅 Skincare AM"}
    assert unmatched == []


async def test_classify_completions_empty_inputs_skip_api(claude, fake_anthropic):
    """No habits or no completions → no API call, return empty result."""
    matched, unmatched = await claude.classify_completions(
        habit_labels=[],
        completions=["any task"],
    )
    assert matched == set()
    assert unmatched == ["any task"]
    fake_anthropic.messages.create.assert_not_called()

    matched, unmatched = await claude.classify_completions(
        habit_labels=["habit"],
        completions=[],
    )
    assert matched == set()
    assert unmatched == []
    fake_anthropic.messages.create.assert_not_called()


async def test_classify_completions_raises_on_malformed_json(claude, fake_anthropic):
    fake_anthropic.messages.create.return_value = MagicMock(content=[_text_block("garbage")])

    with pytest.raises(ValueError):
        await claude.classify_completions(
            habit_labels=["habit"],
            completions=["task"],
        )
