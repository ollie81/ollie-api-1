# ============================================================
# Tests for the /chat/image route (chat.chat_image) -- Ollie
# reacting to a shared photo. Its own smaller pipeline, not
# _process_chat_message: memory/mood/goal extraction and event/
# reminder scheduling don't run for an image message, only a
# reaction gets generated, saved, and moderated.
#
# chat_image is a route function called directly (same style as
# test_chat_voice.py's chat_voice calls), async, so calls go
# through asyncio.run.
# ============================================================

import asyncio
from unittest.mock import patch, MagicMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from chat import chat_image, MAX_IMAGE_BYTES


def _fake_request(path="/"):
    return Request(scope={
        "type": "http", "method": "POST", "path": path,
        "headers": [], "client": ("testclient", 123), "query_string": b"",
    })


class _FakeUpload:
    def __init__(self, data: bytes, content_type: str = "image/jpeg", filename: str = "photo.jpg"):
        self._data = data
        self.content_type = content_type
        self.filename = filename

    async def read(self):
        return self._data


def _run(image_bytes=b"fake image bytes", caption=None, utc_offset_minutes=None, user_id="user-1",
         content_type="image/jpeg"):
    return asyncio.run(chat_image(
        request=_fake_request(),
        image=_FakeUpload(image_bytes, content_type=content_type),
        caption=caption,
        utc_offset_minutes=utc_offset_minutes,
        current_user={"id": user_id},
    ))


def _mock_db(mock_db_cls, *, trial_ok=True, streak=3):
    instance = mock_db_cls.return_value
    instance.try_consume_message.return_value = trial_ok
    instance.get_or_create_session.return_value = "session-1"
    instance.get_relevant_memories.return_value = []
    instance.get_user_context.return_value = {}
    instance.get_recent_messages.return_value = []
    instance.update_streak.return_value = streak
    return instance


def _mock_reaction(mock_openai, text="that's a cool photo, where was this taken?"):
    mock_openai.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=text))]
    )


def test_success_returns_reply_and_streak():
    with patch("chat.OllieDB") as mock_db_cls, \
         patch("chat.is_premium_active", return_value=False), \
         patch("chat.openai_client") as mock_openai, \
         patch("chat.moderate_text", return_value=None):
        _mock_db(mock_db_cls)
        _mock_reaction(mock_openai)

        result = _run(caption="check this out")

        assert result["reply"] == "that's a cool photo, where was this taken?"
        assert result["streak"] == 3


def test_empty_image_rejected_before_charging_a_message():
    with patch("chat.OllieDB") as mock_db_cls, \
         patch("chat.is_premium_active", return_value=False), \
         patch("chat.openai_client") as mock_openai:
        db = _mock_db(mock_db_cls)

        with pytest.raises(HTTPException) as exc_info:
            _run(image_bytes=b"")

        assert exc_info.value.status_code == 400
        db.try_consume_message.assert_not_called()
        mock_openai.chat.completions.create.assert_not_called()


def test_oversized_image_rejected():
    with patch("chat.OllieDB") as mock_db_cls, \
         patch("chat.is_premium_active", return_value=False):
        _mock_db(mock_db_cls)

        with pytest.raises(HTTPException) as exc_info:
            _run(image_bytes=b"x" * (MAX_IMAGE_BYTES + 1))

        assert exc_info.value.status_code == 400


def test_non_image_content_type_rejected():
    with patch("chat.OllieDB") as mock_db_cls, \
         patch("chat.is_premium_active", return_value=False):
        _mock_db(mock_db_cls)

        with pytest.raises(HTTPException) as exc_info:
            _run(content_type="application/pdf")

        assert exc_info.value.status_code == 400


def test_free_tier_daily_limit_reached_returns_429():
    with patch("chat.OllieDB") as mock_db_cls, \
         patch("chat.is_premium_active", return_value=False), \
         patch("chat.openai_client") as mock_openai:
        _mock_db(mock_db_cls, trial_ok=False)

        with pytest.raises(HTTPException) as exc_info:
            _run()

        assert exc_info.value.status_code == 429
        mock_openai.chat.completions.create.assert_not_called()


def test_premium_bypasses_daily_limit():
    with patch("chat.OllieDB") as mock_db_cls, \
         patch("chat.is_premium_active", return_value=True), \
         patch("chat.openai_client") as mock_openai, \
         patch("chat.moderate_text", return_value=None):
        db = _mock_db(mock_db_cls)
        _mock_reaction(mock_openai)

        _run()

        db.try_consume_message.assert_not_called()
        db.increment_message_count.assert_called_once()


def test_vision_call_failure_falls_back_to_in_character_line():
    with patch("chat.OllieDB") as mock_db_cls, \
         patch("chat.is_premium_active", return_value=False), \
         patch("chat.openai_client") as mock_openai, \
         patch("chat.time.sleep"), \
         patch("chat.moderate_text", return_value=None):
        _mock_db(mock_db_cls)
        mock_openai.chat.completions.create.side_effect = Exception("timeout")

        result = _run()

        assert "send it again" in result["reply"]
        assert mock_openai.chat.completions.create.call_count == 2  # one retry


def test_no_caption_still_saves_a_readable_placeholder():
    with patch("chat.OllieDB") as mock_db_cls, \
         patch("chat.is_premium_active", return_value=False), \
         patch("chat.openai_client") as mock_openai, \
         patch("chat.moderate_text", return_value=None):
        db = _mock_db(mock_db_cls)
        _mock_reaction(mock_openai)

        _run(caption=None)

        first_save_call = db.save_message.call_args_list[0]
        assert first_save_call[0][2] == "[shared a photo]"


def test_caption_appended_to_placeholder():
    with patch("chat.OllieDB") as mock_db_cls, \
         patch("chat.is_premium_active", return_value=False), \
         patch("chat.openai_client") as mock_openai, \
         patch("chat.moderate_text", return_value=None):
        db = _mock_db(mock_db_cls)
        _mock_reaction(mock_openai)

        _run(caption="look at this sunset")

        first_save_call = db.save_message.call_args_list[0]
        assert first_save_call[0][2] == "[shared a photo] look at this sunset"


def test_flagged_reply_still_gets_flagged_and_saved():
    with patch("chat.OllieDB") as mock_db_cls, \
         patch("chat.is_premium_active", return_value=False), \
         patch("chat.openai_client") as mock_openai, \
         patch("chat.moderate_text", return_value={"categories": ["x"]}), \
         patch("chat._flag_moderation") as mock_flag:
        db = _mock_db(mock_db_cls)
        _mock_reaction(mock_openai, text="flagged reply")

        result = _run()

        mock_flag.assert_called_once()
        assert result["reply"] == "flagged reply"
        db.save_message.assert_any_call("user-1", "session-1", "flagged reply", "ollie", 0.0)
