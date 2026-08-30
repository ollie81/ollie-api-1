# ============================================================
# Tests for mode wiring into build_system_prompt and
# _process_chat_message -- the "Do It With Me" mode shapes HOW
# Ollie responds via one extra system-prompt block, without
# touching the rest of the chat pipeline.
# ============================================================

from unittest.mock import patch, MagicMock

from chat import build_system_prompt, _process_chat_message


# ---- build_system_prompt ----

def test_mode_instructions_included_when_given():
    prompt = build_system_prompt("english", "", mode_instructions="\nMODE — STUDY TOGETHER:\nsome instructions")
    assert "MODE — STUDY TOGETHER:" in prompt
    assert "some instructions" in prompt


def test_no_mode_instructions_by_default():
    prompt = build_system_prompt("english", "")
    assert "MODE —" not in prompt


def test_mode_appears_after_location_before_time():
    prompt = build_system_prompt(
        "english", "", location_block="\nUSER'S LOCATION: Kigali, Rwanda.",
        mode_instructions="\nMODE — BUILD TOGETHER:\nbuild stuff",
    )
    location_index = prompt.index("USER'S LOCATION")
    mode_index = prompt.index("MODE — BUILD TOGETHER")
    time_index = prompt.index("CURRENT TIME")
    assert location_index < mode_index < time_index


# ---- _process_chat_message: mode threading ----

def _patched_pipeline():
    return [
        patch("chat.detect_language", return_value="english"),
        patch("chat.moderate_text", return_value=None),
        patch("chat.is_premium_active", return_value=False),
        patch("chat.build_memory_context", return_value=""),
        patch("chat.clean_history", return_value=[]),
        patch("chat.pick_chat_model", return_value="gpt-4.1-nano"),
        patch("chat.build_interest_context", return_value=""),
        patch("chat._location_block", return_value=""),
        patch("chat._is_crisis_message", return_value=False),
        patch("chat.extract_memory_worthy", return_value=(None, None, 0)),
        patch("chat.detect_mood", return_value=None),
        patch("chat.extract_goal", return_value=None),
        patch("chat.detect_goal_completion", return_value=None),
        patch("chat.maybe_schedule_event"),
        patch("chat.maybe_schedule_reminder"),
        patch("chat.maybe_track_interest"),
    ]


def test_mode_is_passed_through_to_get_ollie_response():
    db = MagicMock()
    db.get_or_create_session.return_value = "session-1"
    db.get_relevant_memories.return_value = []
    db.get_user_context.return_value = {"active_goals": []}
    db.get_recent_messages.return_value = []
    db.update_streak.return_value = 1

    patches = _patched_pipeline()
    for p in patches:
        p.start()
    try:
        with patch("chat.get_ollie_response", return_value="let's build it!") as mock_response:
            _process_chat_message(db, "user-1", "let's work on my app", None, {"id": "user-1"}, mode="build")
            call_kwargs = mock_response.call_args[1]
            assert "MODE — BUILD TOGETHER:" in call_kwargs["mode_instructions"]
    finally:
        for p in patches:
            p.stop()


def test_no_mode_passes_empty_mode_instructions():
    db = MagicMock()
    db.get_or_create_session.return_value = "session-1"
    db.get_relevant_memories.return_value = []
    db.get_user_context.return_value = {"active_goals": []}
    db.get_recent_messages.return_value = []
    db.update_streak.return_value = 1

    patches = _patched_pipeline()
    for p in patches:
        p.start()
    try:
        with patch("chat.get_ollie_response", return_value="hey!") as mock_response:
            _process_chat_message(db, "user-1", "hey", None, {"id": "user-1"})
            call_kwargs = mock_response.call_args[1]
            assert call_kwargs["mode_instructions"] == ""
    finally:
        for p in patches:
            p.stop()


def test_unknown_mode_degrades_to_no_mode():
    db = MagicMock()
    db.get_or_create_session.return_value = "session-1"
    db.get_relevant_memories.return_value = []
    db.get_user_context.return_value = {"active_goals": []}
    db.get_recent_messages.return_value = []
    db.update_streak.return_value = 1

    patches = _patched_pipeline()
    for p in patches:
        p.start()
    try:
        with patch("chat.get_ollie_response", return_value="hey!") as mock_response:
            _process_chat_message(db, "user-1", "hey", None, {"id": "user-1"}, mode="not_a_real_mode")
            call_kwargs = mock_response.call_args[1]
            assert call_kwargs["mode_instructions"] == ""
    finally:
        for p in patches:
            p.stop()
