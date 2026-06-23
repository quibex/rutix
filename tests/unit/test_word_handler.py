from unittest.mock import AsyncMock, MagicMock

import pytest

from rutix.bot.handlers.word import (
    WordStates,
    _split_word_difficulty,
    cb_difficulty,
    msg_difficulty,
    msg_word,
)
from rutix.integrations.github import FileContent
from rutix.markdown.daily import append_word, parse_words

DAILY = """# x

## Что сделано

- existing done

## Заметки

- existing note
"""

DAILY_WITH_WORDS = """# x

## Слова

- кот (2)

## Заметки

- existing note
"""


@pytest.fixture
def fake_settings():
    s = MagicMock()
    s.tz = "Europe/Moscow"
    return s


@pytest.fixture
def fake_github():
    g = MagicMock()
    g.read = AsyncMock(return_value=FileContent(text=DAILY, sha="x"))
    g.write = AsyncMock(return_value="newsha0")
    return g


@pytest.fixture
def fake_message():
    m = MagicMock()
    m.answer = AsyncMock()
    return m


@pytest.fixture
def fake_state():
    s = MagicMock()
    s.set_state = AsyncMock()
    s.update_data = AsyncMock()
    s.get_data = AsyncMock(return_value={})
    s.clear = AsyncMock()
    return s


# --- _split_word_difficulty -------------------------------------------------


def test_split_plain_word():
    assert _split_word_difficulty("паллиатив") == ("паллиатив", None)


def test_split_word_with_difficulty():
    assert _split_word_difficulty("паллиатив 3") == ("паллиатив", 3)


def test_split_multiword_with_difficulty():
    assert _split_word_difficulty("когнитивный диссонанс 2") == ("когнитивный диссонанс", 2)


def test_split_trailing_non_difficulty_digit():
    # 5 is out of 1–3 range — whole text stays the word
    assert _split_word_difficulty("слово 5") == ("слово 5", None)


# --- append_word / parse_words ----------------------------------------------


def test_append_word_creates_section():
    out = append_word(DAILY, "паллиатив", 3)
    assert "## Слова" in out
    assert "- паллиатив (3)" in out
    assert parse_words(out) == ["паллиатив (3)"]
    # untouched sections preserved
    assert "- existing note" in out
    assert "- existing done" in out


def test_append_word_existing_section_appends():
    out = append_word(DAILY_WITH_WORDS, "собака", 1)
    assert parse_words(out) == ["кот (2)", "собака (1)"]


def test_append_word_allows_duplicates():
    out = append_word(DAILY_WITH_WORDS, "кот", 3)
    assert parse_words(out) == ["кот (2)", "кот (3)"]


# --- msg_word ---------------------------------------------------------------


async def test_msg_word_with_number_writes(fake_message, fake_state, fake_settings, fake_github):
    fake_message.text = "паллиатив 3"
    await msg_word(fake_message, state=fake_state, settings=fake_settings, github=fake_github)

    written = fake_github.write.call_args.args[1]
    assert "- паллиатив (3)" in written
    fake_state.set_state.assert_not_awaited()


async def test_msg_word_without_number_asks(fake_message, fake_state, fake_settings, fake_github):
    fake_message.text = "паллиатив"
    await msg_word(fake_message, state=fake_state, settings=fake_settings, github=fake_github)

    fake_github.write.assert_not_called()
    fake_state.set_state.assert_awaited_once_with(WordStates.await_difficulty)
    fake_state.update_data.assert_awaited_once_with(word="паллиатив")
    answer = fake_message.answer.call_args
    assert "паллиатив" in answer.args[0]
    assert answer.kwargs.get("reply_markup") is not None


async def test_msg_word_ignores_slash(fake_message, fake_state, fake_settings, fake_github):
    fake_message.text = "/unknowncmd"
    await msg_word(fake_message, state=fake_state, settings=fake_settings, github=fake_github)

    fake_github.write.assert_not_called()
    fake_state.set_state.assert_not_awaited()
    fake_message.answer.assert_not_awaited()


async def test_msg_word_ignores_empty(fake_message, fake_state, fake_settings, fake_github):
    fake_message.text = "   "
    await msg_word(fake_message, state=fake_state, settings=fake_settings, github=fake_github)

    fake_github.write.assert_not_called()
    fake_message.answer.assert_not_awaited()


async def test_msg_word_scaffolds_when_daily_missing(
    fake_message, fake_state, fake_settings, fake_github
):
    """Missing daily file is created from a template and the word is written."""
    fake_github.read = AsyncMock(return_value=None)
    fake_message.text = "слово 2"
    await msg_word(fake_message, state=fake_state, settings=fake_settings, github=fake_github)

    fake_github.write.assert_awaited_once()
    # sha=None → create (not update) on the remote.
    assert fake_github.write.call_args.kwargs.get("sha") is None
    written = fake_github.write.call_args.args[1]
    assert "слово (2)" in written


# --- cb_difficulty ----------------------------------------------------------


async def test_cb_difficulty_writes(fake_state, fake_settings, fake_github):
    fake_state.get_data = AsyncMock(return_value={"word": "паллиатив"})
    cb = MagicMock()
    cb.data = "word:2"
    cb.message = MagicMock()
    cb.message.answer = AsyncMock()
    cb.message.edit_reply_markup = AsyncMock()
    cb.answer = AsyncMock()

    await cb_difficulty(cb, state=fake_state, settings=fake_settings, github=fake_github)

    fake_state.clear.assert_awaited()
    cb.message.edit_reply_markup.assert_awaited_with(reply_markup=None)
    written = fake_github.write.call_args.args[1]
    assert "- паллиатив (2)" in written
    cb.answer.assert_awaited()


async def test_cb_difficulty_cancel(fake_state, fake_settings, fake_github):
    cb = MagicMock()
    cb.data = "word:cancel"
    cb.message = MagicMock()
    cb.message.edit_text = AsyncMock()
    cb.answer = AsyncMock()

    await cb_difficulty(cb, state=fake_state, settings=fake_settings, github=fake_github)

    fake_state.clear.assert_awaited()
    cb.message.edit_text.assert_awaited_with("Отменено.")
    fake_github.write.assert_not_called()


# --- msg_difficulty (typed number while awaiting) ---------------------------


async def test_msg_difficulty_writes(fake_message, fake_state, fake_settings, fake_github):
    fake_state.get_data = AsyncMock(return_value={"word": "паллиатив"})
    fake_message.text = "1"

    await msg_difficulty(fake_message, state=fake_state, settings=fake_settings, github=fake_github)

    fake_state.clear.assert_awaited()
    written = fake_github.write.call_args.args[1]
    assert "- паллиатив (1)" in written


async def test_msg_difficulty_invalid(fake_message, fake_state, fake_settings, fake_github):
    fake_state.get_data = AsyncMock(return_value={"word": "паллиатив"})
    fake_message.text = "семь"

    await msg_difficulty(fake_message, state=fake_state, settings=fake_settings, github=fake_github)

    fake_github.write.assert_not_called()
    fake_state.clear.assert_not_awaited()
    assert "1, 2 или 3" in fake_message.answer.call_args.args[0]
