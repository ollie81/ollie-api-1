# ============================================================
# Tests for modes.mode_block -- pure function, no network/DB calls.
# ============================================================

from modes import mode_block, MODE_LABELS, MODE_INSTRUCTIONS, MODE_OPENER_FALLBACKS


def test_no_mode_returns_empty_string():
    assert mode_block(None) == ""
    assert mode_block("") == ""


def test_unknown_mode_returns_empty_string():
    assert mode_block("not_a_real_mode") == ""


def test_known_mode_includes_label_and_instructions():
    result = mode_block("study")
    assert "STUDY TOGETHER" in result
    assert MODE_INSTRUCTIONS["study"] in result


def test_all_six_modes_produce_a_block():
    for mode in ("study", "build", "plan_day", "learn", "practice", "brainstorm"):
        result = mode_block(mode)
        assert result != ""
        assert MODE_LABELS[mode].upper() in result


def test_shared_suffix_present_telling_it_not_to_force_the_mode():
    result = mode_block("build")
    assert "don't force it" in result


def test_opening_flag_adds_the_opener_instruction():
    normal = mode_block("brainstorm", is_opening=False)
    opening = mode_block("brainstorm", is_opening=True)
    assert "hasn't said anything yet" in opening
    assert "hasn't said anything yet" not in normal


def test_every_mode_has_a_fallback_opener():
    for mode in MODE_LABELS:
        assert mode in MODE_OPENER_FALLBACKS
        assert MODE_OPENER_FALLBACKS[mode]
