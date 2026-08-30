# ============================================================
# Tests for chat._location_block and its wiring into
# build_system_prompt -- lets Ollie sound like a local (culture,
# slang, holidays) when the user has set a location in Settings.
# Entirely opt-in: current_user is already the full row, so no
# extra query, and it's a no-op for anyone who's never set one.
# ============================================================

from chat import _location_block, build_system_prompt


def test_no_location_fields_returns_empty_string():
    assert _location_block({"id": "user-1"}) == ""


def test_all_three_fields_are_included_district_first():
    result = _location_block({"district": "Kicukiro", "region": "Kigali", "country": "Rwanda"})
    assert "Kicukiro, Kigali, Rwanda" in result
    assert "USER'S LOCATION" in result


def test_only_country_set():
    result = _location_block({"country": "Rwanda"})
    assert "Rwanda" in result
    assert "USER'S LOCATION" in result


def test_only_region_set():
    result = _location_block({"region": "Kigali"})
    assert "Kigali" in result


def test_empty_string_fields_treated_as_unset():
    assert _location_block({"country": "", "region": None, "district": "   "}) == ""


def test_partial_fields_skips_the_missing_ones():
    result = _location_block({"country": "Rwanda", "region": None, "district": ""})
    assert "Rwanda" in result
    # No stray ", ," from the skipped fields.
    assert ",," not in result.replace(" ", "")


def test_never_announce_or_surveillance_framing_is_present():
    # Guards the "don't be creepy about it" instruction staying in
    # the generated block, not just in the personality prompt.
    result = _location_block({"country": "Rwanda"})
    assert "never" in result.lower()


def test_build_system_prompt_includes_location_block_when_given():
    prompt = build_system_prompt("english", "", location_block="\nUSER'S LOCATION: Kigali, Rwanda.")
    assert "USER'S LOCATION: Kigali, Rwanda." in prompt


def test_build_system_prompt_omits_location_section_when_absent():
    # Default call (no location_block argument at all) must behave
    # exactly as before this feature existed -- nothing per-user
    # location-specific should appear. Checks for the injected
    # block's own marker ("LOCATION:", with the colon) rather than
    # the bare phrase "USER'S LOCATION", since the static
    # personality text also legitimately mentions that phrase now
    # (see ADAPTING TO LOCATION AND CULTURE) regardless of whether
    # any actual per-user block gets injected.
    prompt = build_system_prompt("english", "")
    assert "USER'S LOCATION:" not in prompt


def test_build_system_prompt_omits_location_section_for_empty_string():
    prompt = build_system_prompt("english", "", location_block="")
    assert "USER'S LOCATION:" not in prompt
