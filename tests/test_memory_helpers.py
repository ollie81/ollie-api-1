# ============================================================
# Tests for the pure (no network call) helpers in memory.py.
# ============================================================

from memory import (
    clean_history,
    extract_memory_worthy,
    is_high_emotional_intensity,
    build_memory_context,
    pick_chat_model,
    FAST_MODEL,
    FLAGSHIP_MODEL,
)


# ---- clean_history ----

def test_clean_history_filters_invalid_roles():
    raw = [
        {"role": "user", "content": "hi"},
        {"role": "system", "content": "should be dropped"},
        {"role": "assistant", "content": "hello"},
    ]
    assert clean_history(raw) == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]


def test_clean_history_dedups():
    raw = [{"role": "user", "content": "hi"}, {"role": "user", "content": "hi"}]
    assert len(clean_history(raw)) == 1


def test_clean_history_skips_empty_content():
    raw = [{"role": "user", "content": ""}, {"role": "user", "content": "  "}]
    assert clean_history(raw) == []


def test_clean_history_skips_malformed_entries():
    raw = ["not a dict", {"role": "user"}, {"content": "no role"}, None]
    assert clean_history(raw) == []


def test_clean_history_caps_at_ten_keeping_the_most_recent():
    raw = [{"role": "user", "content": f"message {i}"} for i in range(15)]
    result = clean_history(raw)
    assert len(result) == 10
    assert result[0]["content"] == "message 5"
    assert result[-1]["content"] == "message 14"


def test_clean_history_handles_none_input():
    assert clean_history(None) == []


# ---- extract_memory_worthy ----

def test_extract_memory_worthy_identity_trigger():
    text, importance = extract_memory_worthy("my name is Alex")
    assert text is not None
    assert importance == 3


def test_extract_memory_worthy_preference_trigger():
    text, importance = extract_memory_worthy("i love hiking on weekends")
    assert text is not None
    assert importance == 2


def test_extract_memory_worthy_situational_trigger():
    text, importance = extract_memory_worthy("my boss is being difficult")
    assert text is not None
    assert importance == 1


def test_extract_memory_worthy_no_trigger():
    assert extract_memory_worthy("what's up") == (None, 0)


def test_extract_memory_worthy_too_short():
    assert extract_memory_worthy("hi") == (None, 0)


def test_extract_memory_worthy_empty():
    assert extract_memory_worthy("") == (None, 0)
    assert extract_memory_worthy(None) == (None, 0)


def test_extract_memory_worthy_truncates_to_200_chars():
    long_text = "my name is " + "a" * 300
    text, _ = extract_memory_worthy(long_text)
    assert len(text) == 200


# ---- is_high_emotional_intensity ----

def test_crisis_keyword_is_high_intensity():
    assert is_high_emotional_intensity("i want to kill myself") is True


def test_heavy_emotion_keyword_is_high_intensity():
    assert is_high_emotional_intensity("i feel alone") is True


def test_high_joy_keyword_is_high_intensity():
    assert is_high_emotional_intensity("i got the job") is True


def test_short_flat_message_is_high_intensity():
    assert is_high_emotional_intensity(".") is True
    assert is_high_emotional_intensity("k") is True


def test_shouting_is_high_intensity():
    assert is_high_emotional_intensity("WHY WOULD YOU DO THAT") is True


def test_repeated_punctuation_is_high_intensity():
    assert is_high_emotional_intensity("really???") is True


def test_routine_message_is_not_high_intensity():
    assert is_high_emotional_intensity("what time works for you tomorrow") is False


def test_empty_is_not_high_intensity():
    assert is_high_emotional_intensity("") is False
    assert is_high_emotional_intensity(None) is False


# ---- pick_chat_model ----

def test_uncommon_language_routes_to_flagship():
    assert pick_chat_model("klingon", "hello", "") == FLAGSHIP_MODEL


def test_top_language_routine_message_routes_to_fast():
    assert pick_chat_model("english", "what time works for you tomorrow", "") == FAST_MODEL


def test_high_emotion_routes_to_flagship():
    assert pick_chat_model("english", "i want to kill myself", "") == FLAGSHIP_MODEL


def test_active_memory_context_routes_to_flagship():
    result = pick_chat_model("english", "what time works for you tomorrow", "USER MEMORY:\n  - loves hiking")
    assert result == FLAGSHIP_MODEL


# ---- build_memory_context ----

def test_build_memory_context_empty_input():
    assert build_memory_context([], {}) == ""
    assert build_memory_context(None, None) == ""


def test_build_memory_context_includes_memories_mood_goals():
    memories = [{"memory_text": "loves hiking", "importance": 2}]
    context = {"today_mood": {"mood": "happy"}, "active_goals": [{"title": "run a marathon"}]}
    result = build_memory_context(memories, context)
    assert "loves hiking" in result
    assert "happy" in result
    assert "run a marathon" in result


def test_build_memory_context_skips_malformed_entries():
    memories = ["not a dict", {"memory_text": "", "importance": 1}]
    assert build_memory_context(memories, {}) == ""


def test_build_memory_context_skips_malformed_goals():
    context = {"active_goals": ["not a dict", {"title": ""}]}
    assert build_memory_context([], context) == ""


def test_build_memory_context_caps_at_top_ten_by_importance():
    memories = [{"memory_text": f"fact {i}", "importance": i} for i in range(15)]
    result = build_memory_context(memories, {})
    assert "fact 14" in result  # importance 14, kept
    assert "fact 0" not in result  # importance 0, cut
