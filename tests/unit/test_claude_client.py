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
        MealItem("", "Шаурма", 450, 22.0, 18.0, 45.0),
        MealItem("", "Кола 0.4л", 170, 0.0, 0.0, 42.0),
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


async def test_parse_eat_accepts_messages_history(claude, fake_anthropic):
    payload = {
        "items": [
            {
                "name": "Картошка",
                "kcal": 250,
                "protein": 5,
                "fat": 8,
                "carbs": 40,
                "source": "estimate",
            }
        ]
    }
    fake_anthropic.messages.create.return_value = MagicMock(
        content=[_text_block(json.dumps(payload))]
    )

    history = [
        {"role": "user", "content": "шаурма"},
        {"role": "assistant", "content": '{"items": [...]}'},
        {"role": "user", "content": "нет, картошка"},
    ]
    items = await claude.parse_eat(history, reference_md="")
    assert items == [MealItem("", "Картошка", 250, 5.0, 8.0, 40.0)]
    # Messages were forwarded as-is
    assert fake_anthropic.messages.create.call_args.kwargs["messages"] == history


async def test_parse_eat_raises_on_malformed_json(claude, fake_anthropic):
    msg = MagicMock(content=[_text_block("not a json")])
    fake_anthropic.messages.create.return_value = msg

    with pytest.raises(ValueError, match="invalid JSON"):
        await claude.parse_eat("eggs", reference_md="")


async def test_parse_eat_raises_on_missing_items_key(claude, fake_anthropic):
    msg = MagicMock(content=[_text_block('{"foo": "bar"}')])
    fake_anthropic.messages.create.return_value = msg

    with pytest.raises(ValueError, match="missing 'items'"):
        await claude.parse_eat("eggs", reference_md="")


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
