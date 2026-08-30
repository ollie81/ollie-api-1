# ============================================================
# Tests for the "reply to a message" plumbing added to chat.py:
# _process_chat_message now accepts reply_to_id and threads it into
# the user message's save_message call, and returns message_id/
# user_message_id (the ids save_message assigned) so the client can
# reply to -- or search-and-jump-to -- either side of a turn it just
# sent, not only messages reloaded from /history.
#
# memory_enabled=False on current_user sidesteps the whole memory/
# mood/goal/interest branch (irrelevant to this behavior, and each
# of those is its own LLM call) -- only what genuinely can't be
# skipped (extract_memory_worthy runs unconditionally, see its own
# comment in chat.py) is mocked out alongside it.
# ============================================================

from unittest.mock import patch, MagicMock

from chat import _process_chat_message, chat, ChatRequest


def _mock_db(save_message_ids=("user-msg-1", "ollie-msg-1"), streak=5):
    db = MagicMock()
    db.get_or_create_session.return_value = "session-1"
    db.get_recent_messages.return_value = []
    db.save_message.side_effect = list(save_message_ids)
    db.update_streak.return_value = streak
    return db


def _patched(**overrides):
    defaults = dict(
        detect_language=MagicMock(return_value="english"),
        moderate_text=MagicMock(return_value=None),
        extract_memory_worthy=MagicMock(return_value=(None, None, 0)),
        get_ollie_response=MagicMock(return_value="hey! good to hear from you"),
        maybe_schedule_event=MagicMock(),
        maybe_schedule_reminder=MagicMock(),
    )
    defaults.update(overrides)
    return defaults


def test_reply_to_id_forwarded_to_user_message_save():
    db = _mock_db()
    mocks = _patched()
    with patch("chat.detect_language", mocks["detect_language"]), \
         patch("chat.moderate_text", mocks["moderate_text"]), \
         patch("chat.extract_memory_worthy", mocks["extract_memory_worthy"]), \
         patch("chat.get_ollie_response", mocks["get_ollie_response"]), \
         patch("chat.maybe_schedule_event", mocks["maybe_schedule_event"]), \
         patch("chat.maybe_schedule_reminder", mocks["maybe_schedule_reminder"]):
        _process_chat_message(
            db, "user-1", "about what you just said...", None,
            {"id": "user-1", "memory_enabled": False},
            reply_to_id="ollie-earlier-msg",
        )

        user_save_call = db.save_message.call_args_list[0]
        assert user_save_call.kwargs.get("reply_to_id") == "ollie-earlier-msg"
        # sender positional arg is "user" for the first save
        assert user_save_call.args[3] == "user"


def test_returns_both_saved_message_ids():
    db = _mock_db(save_message_ids=("uid-123", "oid-456"))
    mocks = _patched()
    with patch("chat.detect_language", mocks["detect_language"]), \
         patch("chat.moderate_text", mocks["moderate_text"]), \
         patch("chat.extract_memory_worthy", mocks["extract_memory_worthy"]), \
         patch("chat.get_ollie_response", mocks["get_ollie_response"]), \
         patch("chat.maybe_schedule_event", mocks["maybe_schedule_event"]), \
         patch("chat.maybe_schedule_reminder", mocks["maybe_schedule_reminder"]):
        result = _process_chat_message(
            db, "user-1", "hi", None, {"id": "user-1", "memory_enabled": False},
        )

        assert result["user_message_id"] == "uid-123"
        assert result["message_id"] == "oid-456"


def test_no_reply_target_passes_none_through():
    db = _mock_db()
    mocks = _patched()
    with patch("chat.detect_language", mocks["detect_language"]), \
         patch("chat.moderate_text", mocks["moderate_text"]), \
         patch("chat.extract_memory_worthy", mocks["extract_memory_worthy"]), \
         patch("chat.get_ollie_response", mocks["get_ollie_response"]), \
         patch("chat.maybe_schedule_event", mocks["maybe_schedule_event"]), \
         patch("chat.maybe_schedule_reminder", mocks["maybe_schedule_reminder"]):
        _process_chat_message(
            db, "user-1", "hi", None, {"id": "user-1", "memory_enabled": False},
        )

        user_save_call = db.save_message.call_args_list[0]
        assert user_save_call.kwargs.get("reply_to_id") is None


def test_chat_route_forwards_reply_to_id_to_process_chat_message():
    with patch("chat.OllieDB") as mock_db_cls, \
         patch("chat.is_premium_active", return_value=True), \
         patch("chat._process_chat_message", return_value={"reply": "hi!"}) as mock_process:
        req = ChatRequest(message="about that...", reply_to_id="ollie-msg-9")

        chat(req, current_user={"id": "user-1"})

        mock_process.assert_called_once()
        assert mock_process.call_args.kwargs.get("reply_to_id") == "ollie-msg-9"
