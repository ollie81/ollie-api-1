# ============================================================
# Tests for _is_crisis_message -- duplicated identically in
# chat.py and event_scheduler.py. Both are tested here so a
# future edit to one copy that isn't mirrored in the other
# shows up as a failure instead of silently diverging.
# ============================================================

import pytest

from memory import CRISIS_KEYWORDS
from chat import _is_crisis_message as chat_is_crisis
from event_scheduler import _is_crisis_message as scheduler_is_crisis

IMPLEMENTATIONS = [chat_is_crisis, scheduler_is_crisis]


@pytest.mark.parametrize("is_crisis_message", IMPLEMENTATIONS)
def test_matches_every_crisis_keyword(is_crisis_message):
    for keyword in CRISIS_KEYWORDS:
        assert is_crisis_message(f"honestly {keyword} lately") is True, keyword


@pytest.mark.parametrize("is_crisis_message", IMPLEMENTATIONS)
def test_case_insensitive(is_crisis_message):
    assert is_crisis_message("I WANT TO DIE") is True


@pytest.mark.parametrize("is_crisis_message", IMPLEMENTATIONS)
def test_routine_message_is_not_flagged(is_crisis_message):
    assert is_crisis_message("what time works for you tomorrow") is False


@pytest.mark.parametrize("is_crisis_message", IMPLEMENTATIONS)
def test_empty_is_not_flagged(is_crisis_message):
    assert is_crisis_message("") is False
    assert is_crisis_message(None) is False
