# ============================================================
# RELATIONSHIP — how well Ollie and the user know each other,
# computed from genuine signals (time + depth), never a
# streak-style "don't break the chain" mechanic. Backs the
# "Our Space" journey screen. See: database.py
# get_journey_summary, journey.py.
# ============================================================

STAGE_LABELS = {
    "new": "New",
    "getting_to_know_you": "Getting to know you",
    "close": "Close",
    "trusted": "Trusted",
}

STAGE_EMOJI = {
    "new": "🌱",
    "getting_to_know_you": "🌿",
    "close": "🌳",
    "trusted": "⭐",
}


def compute_relationship_stage(active_days: int, memory_count: int, accomplishment_count: int) -> str:
    """
    Deliberately requires BOTH duration (active_days -- distinct
    calendar days talked to Ollie, not consecutive, so missing a
    day never sets this back -- see database.py's total_active_days)
    AND depth (memory_count + accomplishment weight) to advance a
    stage. Can't be rushed by messaging a lot in one sitting, and
    can't be faked without real time actually passing. This is the
    "meaningful interactions and shared experiences" signal, not a
    grindable streak.
    """
    active_days = max(0, active_days or 0)
    memory_count = max(0, memory_count or 0)
    accomplishment_count = max(0, accomplishment_count or 0)

    depth = memory_count + accomplishment_count * 2

    if active_days >= 60 and depth >= 40:
        return "trusted"
    if active_days >= 21 and depth >= 15:
        return "close"
    if active_days >= 5 and depth >= 3:
        return "getting_to_know_you"
    return "new"
