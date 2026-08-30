# ============================================================
# Tests for relationship.compute_relationship_stage -- pure
# function, no network/DB calls.
# ============================================================

from relationship import compute_relationship_stage, STAGE_LABELS, STAGE_EMOJI


def test_brand_new_user_is_new():
    assert compute_relationship_stage(active_days=0, memory_count=0, accomplishment_count=0) == "new"


def test_few_days_but_no_depth_stays_new():
    # Lots of days but nothing genuinely learned yet -- still new.
    assert compute_relationship_stage(active_days=10, memory_count=0, accomplishment_count=0) == "new"


def test_lots_of_depth_but_one_day_stays_new():
    # Can't rush it by messaging a lot in a single sitting.
    assert compute_relationship_stage(active_days=1, memory_count=100, accomplishment_count=10) == "new"


def test_getting_to_know_you_threshold():
    assert compute_relationship_stage(active_days=5, memory_count=3, accomplishment_count=0) == "getting_to_know_you"


def test_just_under_getting_to_know_you_stays_new():
    assert compute_relationship_stage(active_days=4, memory_count=3, accomplishment_count=0) == "new"
    assert compute_relationship_stage(active_days=5, memory_count=2, accomplishment_count=0) == "new"


def test_close_threshold():
    assert compute_relationship_stage(active_days=21, memory_count=15, accomplishment_count=0) == "close"


def test_accomplishments_count_double_toward_depth():
    # 2 accomplishments = depth 4, same as 4 plain memories.
    assert compute_relationship_stage(active_days=5, memory_count=0, accomplishment_count=2) == "getting_to_know_you"


def test_just_under_close_stays_getting_to_know_you():
    assert compute_relationship_stage(active_days=20, memory_count=15, accomplishment_count=0) == "getting_to_know_you"
    assert compute_relationship_stage(active_days=21, memory_count=14, accomplishment_count=0) == "getting_to_know_you"


def test_trusted_threshold():
    assert compute_relationship_stage(active_days=60, memory_count=40, accomplishment_count=0) == "trusted"


def test_just_under_trusted_stays_close():
    assert compute_relationship_stage(active_days=59, memory_count=40, accomplishment_count=0) == "close"
    assert compute_relationship_stage(active_days=60, memory_count=39, accomplishment_count=0) == "close"


def test_negative_or_none_inputs_are_treated_as_zero():
    assert compute_relationship_stage(active_days=None, memory_count=None, accomplishment_count=None) == "new"
    assert compute_relationship_stage(active_days=-5, memory_count=-5, accomplishment_count=-5) == "new"


def test_all_four_stages_have_labels_and_emoji():
    for stage in ("new", "getting_to_know_you", "close", "trusted"):
        assert stage in STAGE_LABELS
        assert stage in STAGE_EMOJI
