# ============================================================
# LOCAL CONTEXT — one genuinely current, real-world detail tying
# the user's location to something they're already known to care
# about (a tracked interest). Feeds the morning check-in only,
# once per user per day, never the live chat path.
# ============================================================
#
# Ollie's own model has no live knowledge of "what's happening this
# week" -- without this, "current local context" in the personality
# prompt could only ever mean generic, possibly-stale, occasionally
# hallucinated color, not a real fact. This uses OpenAI's hosted web
# search tool so what gets surfaced is actually true today, grounded
# in the user's real location.
#
# Deliberately narrow and opt-in, same shape as everything else this
# feeds into: needs BOTH a location and at least one tracked
# interest, only ever surfaces something when the search actually
# finds one specific, current match, and never raises -- a miss or a
# failure here just means the morning check-in falls back to what it
# already does today.

import logging

from openai import OpenAI
from config import OPENAI_API_KEY
from memory import FLAGSHIP_MODEL
from interest_memory import get_top_interests

logger = logging.getLogger("ollie.local_context")

openai_client = OpenAI(api_key=OPENAI_API_KEY)

MAX_INTERESTS_TO_SEARCH = 3


def get_local_highlight(
    user_id: str,
    country: str | None,
    region: str | None,
    district: str | None,
) -> str | None:
    """
    Searches the web for one current, specific thing this week that
    connects the user's location to one of their tracked interests --
    a match, a new release, a local event, relevant news. Returns
    None if there's no location, no tracked interests, the search
    finds nothing specific and current, or anything fails.
    """
    if not (country or region or district):
        return None

    interests = get_top_interests(user_id, limit=MAX_INTERESTS_TO_SEARCH)
    if not interests:
        return None

    place = ", ".join(p for p in (district, region, country) if p)

    try:
        response = openai_client.responses.create(
            model=FLAGSHIP_MODEL,
            tools=[{
                "type": "web_search",
                "user_location": {
                    "type": "approximate",
                    "country": country,
                    "region": region,
                    "city": district,
                },
                "search_context_size": "medium",
            }],
            input=(
                f"This person lives in {place} and is known to care about: "
                f"{', '.join(interests)}. Search for one genuinely current, "
                "specific thing from the last few days or this week that "
                "connects their location to one of those interests -- an "
                "upcoming or recent match, a new release, a local event or "
                "show, relevant news. If you find one clear, real, specific "
                "match, reply with ONLY that one fact as a single short "
                "sentence, no preamble, no citation markers. If nothing "
                "specific and current turns up, reply with ONLY: NONE"
            ),
            max_output_tokens=120,
            timeout=15,
        )
        content = (response.output_text or "").strip()
        if not content or content.upper() == "NONE":
            return None
        return content
    except Exception as e:
        logger.warning(f"get_local_highlight failed for user {user_id}: {e}")
        return None
