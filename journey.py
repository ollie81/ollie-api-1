# ============================================================
# JOURNEY — "Our Space": the shared history between Ollie and
# the user. See relationship.py for the relationship-stage
# computation and database.py's get_journey_summary for the
# underlying data.
# ============================================================

import logging

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
        }
    except Exception as e:
        logger.error(f"get_journey failed for user {current_user.get('id')}: {e}")
        raise HTTPException(status_code=500, detail="Could not load your journey")
