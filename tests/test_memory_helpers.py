# ============================================================
# Tests for the pure (no network call) helpers in memory.py.
# extract_memory_worthy and detect_goal_completion are LLM-based
# now -- see test_extract_memory_worthy.py and
# test_detect_goal_completion.py for those.
# ============================================================

from memory import (
    clean_history,
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


def test_build_memory_context_caps_minor_memories_at_the_limit():
    memories = [{"memory_text": f"fact {i}", "importance": 1} for i in range(15)]
    result = build_memory_context(memories, {}, limit=10)
    for i in range(10):
        assert f"fact {i}" in result  # first 10 (stable sort keeps input order on ties), kept
    for i in range(10, 15):
        assert f"fact {i}" not in result  # past the limit, cut


def test_build_memory_context_never_cuts_importance_three_even_past_the_limit():
    # Major/identity-level memories are exempt from the cap entirely --
    # something genuinely important doesn't stop mattering just
    # because it's outnumbered by smaller, more recent memories.
    major = [{"memory_text": f"major {i}", "importance": 3} for i in range(12)]
    minor = [{"memory_text": f"minor {i}", "importance": 1} for i in range(5)]
    result = build_memory_context(major + minor, {}, limit=10)
    for i in range(12):
        assert f"major {i}" in result  # all 12 survive, despite limit=10
    for i in range(5):
        assert f"minor {i}" not in result  # no slots left once major already exceeds the limit


def test_build_memory_context_fills_remaining_slots_with_top_minor_memories():
    major = [{"memory_text": "major fact", "importance": 3}]
    minor = [{"memory_text": f"fact {i}", "importance": 2} for i in range(5)] + \
            [{"memory_text": f"fact {i}", "importance": 1} for i in range(5, 10)]
    result = build_memory_context(major + minor, {}, limit=5)
    assert "major fact" in result
    for i in range(4):  # remaining = 5 - 1 major = 4 slots, highest-importance minor first
        assert f"fact {i}" in result
    assert "fact 4" not in result


def test_build_memory_context_shows_category_when_present():
    memories = [{"memory_text": "stuck on a login bug", "importance": 2, "category": "struggle"}]
    result = build_memory_context(memories, {})
    assert "[struggle] stuck on a login bug" in result


def test_build_memory_context_omits_bracket_when_category_missing():
    memories = [{"memory_text": "loves hiking", "importance": 2}]
    result = build_memory_context(memories, {})
    assert "[" not in result
