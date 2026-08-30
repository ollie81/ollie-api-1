# ============================================================
# Tests for Phase 8's "deeper memory" premium boundary: Premium
# users get a higher memory-recall depth (MEMORY_RECALL_LIMIT_
# PREMIUM) than free tier (MEMORY_RECALL_LIMIT_FREE, which matches
# what everyone already got before this tier split existed --
# zero regression for free users), applied consistently everywhere
# Ollie draws on memory: regular chat, image reactions, and "Do It
# With Me" openers. See chat.py's MEMORY_RECALL_LIMIT_* constants.
# ============================================================

from unittest.mock import patch, MagicMock

from chat import (
    _process_chat_message,
    _process_image_message,
    _generate_mode_opener,
    MEMORY_RECALL_LIMIT_FREE,
    MEMORY_RECALL_LIMIT_PREMIUM,
)


def test_free_and_premium_limits_are_distinct():
    # Sanity check the constants themselves before relying on them
    # below -- premium must genuinely be more, never less or equal.
    assert MEMORY_RECALL_LIMIT_PREMIUM > MEMORY_RECALL_LIMIT_FREE


# ---- _process_chat_message ----

def _patch_chat_pipeline(is_premium):
    return [
        patch("chat.detect_language", return_value="english"),
        patch("chat.moderate_text", return_value=None),
        patch("chat.is_premium_active", return_value=is_premium),
        patch("chat.build_memory_context", return_value=""),
        patch("chat.clean_history", return_value=[]),
        patch("chat.pick_chat_model", return_value="gpt-4.1-nano"),
        patch("chat.build_interest_context", return_value=""),
        patch("chat.get_ollie_response", return_value="hey!"),
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


def _run_chat_message(is_premium):
    db = MagicMock()
    db.get_or_create_session.return_value = "session-1"
    db.get_relevant_memories.return_value = []
    db.get_user_context.return_value = {"active_goals": []}
    db.get_recent_messages.return_value = []
    db.update_streak.return_value = 1

    patches = _patch_chat_pipeline(is_premium)
    for p in patches:
        p.start()
    try:
        _process_chat_message(db, "user-1", "hey", None, {"id": "user-1"})
    finally:
        for p in patches:
            p.stop()
    return db


def test_free_user_gets_free_recall_limit_in_chat():
    db = _run_chat_message(is_premium=False)
    db.get_relevant_memories.assert_called_once_with("user-1", limit=MEMORY_RECALL_LIMIT_FREE)


def test_premium_user_gets_deeper_recall_limit_in_chat():
    db = _run_chat_message(is_premium=True)
    db.get_relevant_memories.assert_called_once_with("user-1", limit=MEMORY_RECALL_LIMIT_PREMIUM)


# ---- _process_image_message ----

def _run_image_message(is_premium):
    db = MagicMock()
    db.get_or_create_session.return_value = "session-1"
    db.get_relevant_memories.return_value = []
    db.get_user_context.return_value = {}
    db.get_recent_messages.return_value = []
    db.update_streak.return_value = 1

    with patch("chat.detect_language", return_value="english"), \
         patch("chat.is_premium_active", return_value=is_premium), \
         patch("chat.build_memory_context", return_value=""), \
         patch("chat.clean_history", return_value=[]), \
         patch("chat._location_block", return_value=""), \
         patch("chat.build_system_prompt", return_value="SYSTEM PROMPT"), \
         patch("chat._get_image_reaction", return_value="nice photo!"), \
         patch("chat.moderate_text", return_value=None):
        _process_image_message(
            db, "user-1", b"fake image bytes", "image/jpeg", "check this out", None, {"id": "user-1"},
        )
    return db


def test_free_user_gets_free_recall_limit_for_images():
    db = _run_image_message(is_premium=False)
    db.get_relevant_memories.assert_called_once_with("user-1", limit=MEMORY_RECALL_LIMIT_FREE)


def test_premium_user_gets_deeper_recall_limit_for_images():
    db = _run_image_message(is_premium=True)
    db.get_relevant_memories.assert_called_once_with("user-1", limit=MEMORY_RECALL_LIMIT_PREMIUM)


# ---- _generate_mode_opener ----

def _run_mode_opener(is_premium, mock_chat_completion):
    with patch("chat.OllieDB") as mock_db_cls, \
         patch("chat.is_premium_active", return_value=is_premium), \
         patch("chat.openai_client.chat.completions.create",
               return_value=mock_chat_completion("hey! ready when you are")), \
         patch("chat.moderate_text", return_value=None):
        mock_db_cls.return_value.get_relevant_memories.return_value = []
        mock_db_cls.return_value.get_user_context.return_value = {}
        _generate_mode_opener("user-1", "study", {"id": "user-1"}, None)
        return mock_db_cls.return_value


def test_free_user_gets_free_recall_limit_for_mode_opener(mock_chat_completion):
    db = _run_mode_opener(False, mock_chat_completion)
    db.get_relevant_memories.assert_called_once_with("user-1", limit=MEMORY_RECALL_LIMIT_FREE)


def test_premium_user_gets_deeper_recall_limit_for_mode_opener(mock_chat_completion):
    db = _run_mode_opener(True, mock_chat_completion)
    db.get_relevant_memories.assert_called_once_with("user-1", limit=MEMORY_RECALL_LIMIT_PREMIUM)
