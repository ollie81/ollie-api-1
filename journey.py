# ============================================================
# JOURNEY — "Our Space": the shared history between Ollie and
# the user. See relationship.py for the relationship-stage
# computation and database.py's get_journey_summary for the
# underlying data.
# ============================================================

import logging
from datetime import date, datetime, timezone

from fastapi import APIRouter, HTTPException, Depends

from database import OllieDB
from auth import get_current_user
from premium import is_premium_active
from relationship import compute_relationship_stage, STAGE_LABELS, STAGE_EMOJI

logger = logging.getLogger("ollie.journey")

router = APIRouter()

# Premium sees a deeper slice of highlights -- FREE_HIGHLIGHT_LIMIT
# matches get_journey_summary's own pre-existing default exactly, so
# free-tier "Our Space" is byte-for-byte unchanged by this split.
FREE_HIGHLIGHT_LIMIT = 30
PREMIUM_HIGHLIGHT_LIMIT = 100

# How stale a saved home-screen highlight (see daily_message.py's
# _process_morning_checkin) can be before it's just left out rather
# than shown -- a couple of days' slack for timezone rounding, never
# old enough to reference something that's clearly no longer "today".
HOME_HIGHLIGHT_MAX_AGE_DAYS = 2


def _fresh_home_highlight(current_user: dict) -> str | None:
    text = current_user.get("last_home_highlight_text")
    highlight_date_str = current_user.get("last_home_highlight_date")
    if not text or not highlight_date_str:
        return None
    try:
        highlight_date = date.fromisoformat(highlight_date_str)
    except ValueError:
        return None
    age = datetime.now(timezone.utc).date() - highlight_date
    return text if age.days <= HOME_HIGHLIGHT_MAX_AGE_DAYS else None


@router.get("/")
def get_journey(current_user: dict = Depends(get_current_user)):
    try:
        db = OllieDB()
        user_id = current_user["id"]
        is_premium = is_premium_active(user_id)

        summary = db.get_journey_summary(
            user_id,
            highlight_limit=PREMIUM_HIGHLIGHT_LIMIT if is_premium else FREE_HIGHLIGHT_LIMIT,
        )
        # current_user is already the full row (get_current_user
        # selects "*"), so this is free -- no extra query.
        active_days = current_user.get("total_active_days") or 0
        accomplishment_count = len(summary["completed_goals"])

        stage = compute_relationship_stage(
            active_days=active_days,
            memory_count=summary["memory_count"],
            accomplishment_count=accomplishment_count,
        )

        return {
            "stage": stage,
            "stage_label": STAGE_LABELS[stage],
            "stage_emoji": STAGE_EMOJI[stage],
            "active_days": active_days,
            "memory_count": summary["memory_count"],
            "active_goals": summary["active_goals"],
            "completed_goals": summary["completed_goals"],
            "highlights": summary["highlights"],
            "is_premium": is_premium,
            "home_highlight": _fresh_home_highlight(current_user),
        }
    except Exception as e:
        logger.error(f"get_journey failed for user {current_user.get('id')}: {e}")
        raise HTTPException(status_code=500, detail="Could not load your journey")
