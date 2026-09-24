# ============================================================
# Tests that the slow, non-reply-affecting parts of a chat turn
# (moderation, mood/goal/interest extraction, reminder detection)
# are genuinely DEFERRED to a background task rather than run
# inline before the reply is returned -- see
# chat._process_message_side_effects and the comment on
# chat._process_chat_message's background_tasks.add_task call.
#
# This is the actual fix for messages piling up 7+ sequential
# model calls before a reply ever went out, which was pushing
# ordinary messages close to (and sometimes past) the client's
# request timeout. A test that only checked the final state (as
# test_memory_enabled_gate.py's tests do, after manually running
# the queued task) wouldn't catch a regression back to running
# everything inline -- these specifically check nothing has run
# yet at the moment _process_chat_message returns.
# ============================================================

from unittest.mock import patch, MagicMock

from fastapi import BackgroundTasks

from chat import _process_chat_message


def _patch_pipeline():
    return [
        patch("chat.detect_language", return_value="english"),
        patch("chat.moderate_text", return_value=None),
        patch("chat.is_premium_active", return_value=False),
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


def test_slow_side_effects_are_scheduled_not_run_inline():
    db = MagicMock()
    db.get_or_create_session.return_value = "session-1"
    db.get_relevant_memories.return_value = []
    db.get_user_context.return_value = {"active_goals": []}
    db.get_recent_messages.return_value = []
    db.update_streak.return_value = 1

    patches = _patch_pipeline()
    mocks = {p.attribute: p.start() for p in patches}
    try:
        background_tasks = BackgroundTasks()
        result = _process_chat_message(
            db, "user-1", "hey", None, {"id": "user-1"}, background_tasks,
        )

        # The reply is already back -- nothing slow has run yet.
        assert result["reply"] == "hey!"
        mocks["moderate_text"].assert_not_called()
        mocks["detect_mood"].assert_not_called()
        mocks["extract_goal"].assert_not_called()
        mocks["detect_goal_completion"].assert_not_called()
        mocks["maybe_schedule_event"].assert_not_called()
        mocks["maybe_schedule_reminder"].assert_not_called()
        mocks["maybe_track_interest"].assert_not_called()
        assert len(background_tasks.tasks) == 1

        # Only once the scheduled task actually runs (as the ASGI
        # server would, after the response is sent) do they fire.
        task = background_tasks.tasks[0]
        task.func(*task.args, **task.kwargs)

        mocks["detect_mood"].assert_called_once()
        mocks["extract_goal"].assert_called_once()
        mocks["maybe_schedule_reminder"].assert_called_once()
        mocks["maybe_track_interest"].assert_called_once()
        assert mocks["moderate_text"].call_count == 2  # input + output
    finally:
        for p in patches:
            p.stop()
