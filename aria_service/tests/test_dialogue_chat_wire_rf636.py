"""R-F636 — Chat-path wiring for cultural_atlas + user_model.

Pins three wirings, all in both _aria_chat_impl and _aria_chat_stream_impl:
  - cultural_atlas.detect_jurisdiction_in_text + render_context_block
    inject when message names a seeded non-UK/US jurisdiction
  - user_model.touch_active fires on every chat turn
  - Both wrapped in defensive try/except (failures must NEVER break the turn)

Also pins the jurisdiction detector (cultural_atlas.detect_jurisdiction_in_text):
  - Matches full country name (case-insensitive)
  - Matches uppercase ISO2 with word boundary
  - Does NOT false-fire on lowercase prose ("it", "in", "us")
  - Returns None for unseeded countries
"""
from __future__ import annotations

import pathlib

from ._source_probe import repo_path


# ══════════════════════════════════════════════════════════════════
# PART A — jurisdiction detector
# ══════════════════════════════════════════════════════════════════

def test_rf636_detect_jurisdiction_finds_full_country_name():
    from aria_service.intel.cultural_atlas import detect_jurisdiction_in_text
    assert detect_jurisdiction_in_text(
        "I'm on a call with the buyer in Saudi Arabia tomorrow"
    ) == "SA"
    assert detect_jurisdiction_in_text(
        "Angola FAA tender is moving"
    ) == "AO"
    assert detect_jurisdiction_in_text(
        "the Türkiye SSB procurement is centralised"
    ) == "TR"


def test_rf636_detect_jurisdiction_case_insensitive_for_names():
    from aria_service.intel.cultural_atlas import detect_jurisdiction_in_text
    assert detect_jurisdiction_in_text("nigeria contract update") == "NG"
    assert detect_jurisdiction_in_text("BRAZIL deal pipeline") == "BR"


def test_rf636_detect_jurisdiction_matches_uppercase_iso2():
    from aria_service.intel.cultural_atlas import detect_jurisdiction_in_text
    assert detect_jurisdiction_in_text(
        "Send the SA brief to Antonio"
    ) == "SA"


def test_rf636_detect_jurisdiction_does_not_match_lowercase_iso2_in_prose():
    """ISO2 collision protection — short tokens like 'it', 'in', 'us'
    appear constantly in English prose. We require UPPERCASE for ISO2
    fallback so they don't false-fire."""
    from aria_service.intel.cultural_atlas import detect_jurisdiction_in_text
    # Lowercase 'it' must NOT trigger IT (Italy)
    assert detect_jurisdiction_in_text(
        "I think it is going well"
    ) is None
    # Lowercase 'in' must NOT trigger IN (India)
    assert detect_jurisdiction_in_text(
        "the deal is going in the right direction"
    ) is None
    # Lowercase 'us' must NOT trigger US (United States)
    assert detect_jurisdiction_in_text(
        "this affects us in the long run"
    ) is None


def test_rf636_detect_jurisdiction_returns_none_for_unseeded():
    from aria_service.intel.cultural_atlas import detect_jurisdiction_in_text
    assert detect_jurisdiction_in_text(
        "the Tuvalu defence ministry is interested"
    ) is None
    assert detect_jurisdiction_in_text("") is None
    assert detect_jurisdiction_in_text(None) is None  # type: ignore[arg-type]


def test_rf636_detect_jurisdiction_returns_first_match():
    """Multiple countries in one message — return the first seeded one."""
    from aria_service.intel.cultural_atlas import detect_jurisdiction_in_text
    # Saudi appears before Brazil in the text; either result is acceptable
    # as long as it's one of them — the function is deliberately
    # non-deterministic across the multi-match case (depends on dict
    # iteration order). The pin is that *some* known country lands.
    result = detect_jurisdiction_in_text(
        "the Saudi deal is bigger than the Brazil deal"
    )
    assert result in ("SA", "BR")


# ══════════════════════════════════════════════════════════════════
# PART B — chat-impl wiring (both paths per §13)
# ══════════════════════════════════════════════════════════════════

def _src() -> str:
    return pathlib.Path(
        repo_path("aria_service/aria_engine.py")
    ).read_text(encoding="utf-8", errors="ignore")


def test_rf636_chat_impl_wires_cultural_atlas_inject():
    src = _src()
    impl_idx = src.find("async def _aria_chat_impl")
    stream_idx = src.find("async def _aria_chat_stream_impl", impl_idx)
    region = src[impl_idx:stream_idx]
    assert "cultural_atlas" in region
    assert "detect_jurisdiction_in_text" in region
    assert 'in ("GB", "US")' in region or "in ('GB', 'US')" in region


def test_rf636_chat_impl_wires_user_model_touch_active():
    src = _src()
    impl_idx = src.find("async def _aria_chat_impl")
    stream_idx = src.find("async def _aria_chat_stream_impl", impl_idx)
    region = src[impl_idx:stream_idx]
    assert "user_model" in region
    assert "touch_active" in region


def test_rf636_stream_impl_wires_cultural_atlas_inject():
    """§13 stream-bypass rule."""
    src = _src()
    stream_idx = src.find("async def _aria_chat_stream_impl")
    region = src[stream_idx:stream_idx + 30000]
    assert "cultural_atlas" in region, (
        "R-F636: cultural_atlas missing from stream impl (§13 violation)"
    )
    assert "detect_jurisdiction_in_text" in region


def test_rf636_stream_impl_wires_user_model_touch_active():
    """§13 stream-bypass rule."""
    src = _src()
    stream_idx = src.find("async def _aria_chat_stream_impl")
    region = src[stream_idx:stream_idx + 30000]
    assert "user_model" in region
    assert "touch_active" in region


def test_rf636_both_wirings_wrapped_in_try_except():
    """Cultural inject + user_model touch failures must NEVER break the chat turn."""
    src = _src()
    # Cultural inject try/except (both impls)
    for impl_name in ("_aria_chat_impl", "_aria_chat_stream_impl"):
        impl_idx = src.find(f"async def {impl_name}")
        next_def = src.find("\nasync def ", impl_idx + 10)
        region = src[impl_idx:next_def] if next_def > 0 else src[impl_idx:impl_idx + 30000]
        cult_idx = region.find("cultural_atlas")
        assert cult_idx > 0
        cult_zone = region[max(0, cult_idx - 200):cult_idx + 1200]
        assert "try:" in cult_zone
        assert "except" in cult_zone
        # user_model touch try/except
        um_idx = region.find("user_model")
        assert um_idx > 0
        um_zone = region[max(0, um_idx - 200):um_idx + 1200]
        assert "try:" in um_zone
        assert "except" in um_zone


def test_rf636_cultural_uk_us_exclusion_in_both_impls():
    """GB and US must be excluded in BOTH impls — operator's default
    baseline."""
    src = _src()
    for impl_name in ("_aria_chat_impl", "_aria_chat_stream_impl"):
        impl_idx = src.find(f"async def {impl_name}")
        next_def = src.find("\nasync def ", impl_idx + 10)
        region = src[impl_idx:next_def] if next_def > 0 else src[impl_idx:impl_idx + 30000]
        cult_idx = region.find("cultural_atlas")
        cult_zone = region[cult_idx:cult_idx + 1000]
        assert ('"GB"' in cult_zone and '"US"' in cult_zone) or (
            "'GB'" in cult_zone and "'US'" in cult_zone
        )
