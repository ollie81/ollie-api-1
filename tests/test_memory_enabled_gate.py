# ============================================================
# Tests for the memory_enabled gate wired into
# chat._process_chat_message and chat._process_image_message --
# when a user has turned memory off in Settings, Ollie must stop
# both READING previously stored memories/mood/goals/interests
# (so it behaves like a fresh conversation) and WRITING new ones,
# without breaking anything else in the pipeline (reminders, event
# scheduling, streaks, moderation).
#
# Every chat.py-level dependency is mocked out -- this is purely
# about which calls happen/don't happen based on memory_enabled,
# not about re-verifying the untouched parts of the pipeline
# (already covered by the rest of the suite passing).
# ============================================================

from unittest.mock import patch, MagicMock

from chat import _process_chat_message, _process_image_message


def _patch_common():
    """Context managers shared by every test -- mocks every
    chat.py-level dependency _process_chat_message touches, with
    reasonable defaults so the pipeline runs end to end."""
    return [
        patch("chat.detect_language", return_value="english"),
        patch("chat.moderate_text", return_value=None),
        patch("chat.is_premium_active", return_value=False),
        patch("chat.build_memory_context", return_value="MEMORY BLOCK"),
        patch("chat.clean_history", return_value=[]),
        patch("chat.pick_chat_model", return_value="gpt-4.1-nano"),
        patch("chat.build_interest_context", return_value="INTEREST BLOCK"),
        patch("chat.get_ollie_response", return_value="hey!"),
        patch("chat._location_block", return_value=""),
        patch("chat._is_crisis_message", return_value=False),
        patch("chat.extract_memory_worthy", return_value=("Has a dog named Max", "person", 2)),
        patch("chat.detect_mood", return_value="happy"),
        patch("chat.extract_goal", return_value="run a marathon"),
        patch("chat.detect_goal_completion", return_value=None),
        patch("chat.maybe_schedule_event"),
        patch("chat.maybe_schedule_reminder"),
        patch("chat.maybe_track_interest"),
    ]


def _run_with_mocks(current_user):
    db = MagicMock()
    db.get_or_create_session.return_value = "session-1"
    db.get_relevant_memories.return_value = [{"memory_text": "old memory", "importance": 2}]
    db.get_user_context.return_value = {"active_goals": [{"title": "run a marathon"}]}
    db.get_recent_messages.return_value = []
    db.update_streak.return_value = 3

    patches = _patch_common()
    for p in patches:
        p.start()
    try:
        result = _process_chat_message(db, "user-1", "I finally got the login working!", None, current_user)
    finally:
        for p in patches:
            p.stop()
    return db, result


def test_memory_disabled_skips_retrieval():
    db, _ = _run_with_mocks({"id": "user-1", "memory_enabled": False})
    db.get_relevant_memories.assert_not_called()
    db.get_user_context.assert_not_called()


def test_memory_disabled_skips_all_writes():
    db, _ = _run_with_mocks({"id": "user-1", "memory_enabled": False})
    db.save_memory.assert_not_called()
    db.update_mood.assert_not_called()
    db.save_goal.assert_not_called()
    db.complete_goal.assert_not_called()


def test_memory_disabled_skips_interest_tracking():
    with patch("chat.maybe_track_interest") as mock_track:
        _run_with_mocks({"id": "user-1", "memory_enabled": False})
    mock_track.assert_not_called()


def test_memory_disabled_still_replies_and_updates_streak():
    db, result = _run_with_mocks({"id": "user-1", "memory_enabled": False})
    assert result["reply"] == "hey!"
    db.update_streak.assert_called_once()


def test_memory_enabled_by_default_reads_and_writes():
    db, _ = _run_with_mocks({"id": "user-1"})  # unset -- must default to enabled
    db.get_relevant_memories.assert_called_once_with("user-1", limit=10)
    db.get_user_context.assert_called_once_with("user-1")
    db.save_memory.assert_called_once_with("user-1", "Has a dog named Max", importance=2, category="person")
    db.update_mood.assert_called_once_with("user-1", "happy")
    db.save_goal.assert_called_once_with("user-1", "run a marathon")


def test_memory_enabled_true_reads_and_writes():
    db, _ = _run_with_mocks({"id": "user-1", "memory_enabled": True})
    db.get_relevant_memories.assert_called_once()
    db.save_memory.assert_called_once()


def test_goal_completion_closes_goal_and_logs_accomplishment():
    db = MagicMock()
    db.get_or_create_session.return_value = "session-1"
    db.get_relevant_memories.return_value = []
    db.get_user_context.return_value = {"active_goals": [{"title": "fix the login bug"}]}
    db.get_recent_messages.return_value = []
    db.update_streak.return_value = 1

    with patch("chat.detect_language", return_value="english"), \
         patch("chat.moderate_text", return_value=None), \
         patch("chat.is_premium_active", return_value=False), \
         patch("chat.build_memory_context", return_value=""), \
         patch("chat.clean_history", return_value=[]), \
         patch("chat.pick_chat_model", return_value="gpt-4.1-nano"), \
         patch("chat.build_interest_context", return_value=""), \
         patch("chat.get_ollie_response", return_value="you finally got it working!"), \
         patch("chat._location_block", return_value=""), \
         patch("chat._is_crisis_message", return_value=False), \
         patch("chat.extract_memory_worthy", return_value=(None, None, 0)), \
         patch("chat.detect_mood", return_value=None), \
         patch("chat.extract_goal", return_value=None), \
         patch("chat.detect_goal_completion", return_value="fix the login bug"), \
         patch("chat.maybe_schedule_event"), \
         patch("chat.maybe_schedule_reminder"), \
         patch("chat.maybe_track_interest"):
        _process_chat_message(db, "user-1", "I finally got the login bug fixed!", None, {"id": "user-1"})

    db.complete_goal.assert_called_once_with("user-1", "fix the login bug")
    db.save_memory.assert_called_once_with("user-1", "Accomplished: fix the login bug", importance=3, category="accomplishment")


# ---- _process_image_message ----

def _run_image_with_mocks(current_user):
    db = MagicMock()
    db.get_or_create_session.return_value = "session-1"
    db.get_relevant_memories.return_value = [{"memory_text": "old memory", "importance": 2}]
    db.get_user_context.return_value = {"active_goals": []}
    db.get_recent_messages.return_value = []
    db.update_streak.return_value = 1

    with patch("chat.detect_language", return_value="english"), \
         patch("chat.is_premium_active", return_value=False), \
         patch("chat.build_memory_context", return_value="MEMORY BLOCK"), \
         patch("chat.clean_history", return_value=[]), \
         patch("chat._location_block", return_value=""), \
         patch("chat.build_system_prompt", return_value="SYSTEM PROMPT"), \
         patch("chat._get_image_reaction", return_value="nice photo!"), \
         patch("chat.moderate_text", return_value=None):
        result = _process_image_message(
            db, "user-1", b"fake image bytes", "image/jpeg", "check this out", None, current_user,
        )
    return db, result


def test_image_message_memory_disabled_skips_retrieval():
    db, _ = _run_image_with_mocks({"id": "user-1", "memory_enabled": False})
    db.get_relevant_memories.assert_not_called()
    db.get_user_context.assert_not_called()


def test_image_message_memory_enabled_reads_memories():
    db, _ = _run_image_with_mocks({"id": "user-1", "memory_enabled": True})
    db.get_relevant_memories.assert_called_once_with("user-1", limit=10)
