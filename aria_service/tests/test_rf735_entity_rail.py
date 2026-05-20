"""R-F735 (2026-05-20) — entity card right-rail static-presence tests.

Agent 2 audit ranked "No entity context panel (right rail)" as the #1
frontend gap hurting DD investigation flow: investigators had to
alt-tab to other tools to check sanctions / registry / freshness on
the entity they were investigating.

R-F735 adds:
  1. Right-rail <aside id="entity-rail"> after the chat-shell, with:
     - Empty state ("No active entity")
     - Card state showing CURRENT FOCUS / SOURCES / QUICK ACTIONS
     - 4 quick-action buttons (Screen, Full DD, Profile, Clear) that
       route through the existing R-F731/R-F729 slash commands.
  2. CSS that:
     - sets rail width 280px on desktop
     - hides the rail on screens <1100px (mobile / narrow laptops)
     - styles source chips by type (tool/url/primary_source/rag)
  3. JS:
     - updateEntityRail() reads window._ariaCurrentEntity +
       window._ariaLatestSources and renders the card
     - Source links open in new tab (target=_blank, rel=noopener)
     - Quick-action delegation: clicking "Screen" sends /screen <name>
     - Wired from the existing SSE event handlers — the
       progress (R-F737) event populates entity name, the sources
       (R-F732) event populates the source list, both call
       updateEntityRail()

Browser-render test out of scope; these are static-presence checks.
"""
from __future__ import annotations

import re
from pathlib import Path

ARIA_HTML = Path(__file__).resolve().parents[2] / "public" / "aria.html"


def _read_html() -> str:
    return ARIA_HTML.read_text(encoding="utf-8")


def test_entity_rail_html_present():
    """R-F735: <aside id="entity-rail"> exists in the markup."""
    src = _read_html()
    assert re.search(r'id="entity-rail"', src), "R-F735: entity-rail aside missing"
    assert re.search(r'id="entity-rail-card"', src), "R-F735: entity-rail-card missing"
    assert re.search(r'id="entity-rail-empty"', src), "R-F735: entity-rail-empty missing"


def test_entity_rail_quick_actions_present():
    """R-F735: 4 quick-action buttons (screen, dd, profile, clear)."""
    src = _read_html()
    assert 'data-action="screen"' in src
    assert 'data-action="dd"' in src
    assert 'data-action="profile"' in src
    assert 'data-action="clear"' in src


def test_entity_rail_quick_actions_use_slash_commands():
    """R-F735: clicking quick-action buttons sends slash commands —
    so the rail integrates with R-F731 (/screen) + R-F729 (/dd)."""
    src = _read_html()
    # The action handler must reference the slash commands
    assert "'/screen '" in src or '"/screen "' in src
    assert "'/dd '" in src or '"/dd "' in src


def test_entity_rail_responsive_hides_on_small_screens():
    """R-F735: media query hides the rail at <1100px width."""
    src = _read_html()
    # Look for the media-query block targeting .entity-rail
    assert re.search(
        r"@media\s*\(\s*max-width:\s*1100px\s*\)\s*\{[^}]*\.entity-rail[^}]*display:\s*none",
        src,
    ), "R-F735: responsive hide on <1100px missing"


def test_entity_rail_css_present():
    """R-F735: core CSS classes exist."""
    src = _read_html()
    for cls in (
        ".entity-rail",
        ".entity-rail-empty",
        ".entity-rail-card",
        ".entity-rail-section",
        ".entity-rail-name",
        ".entity-rail-sources",
        ".entity-rail-action",
    ):
        assert cls in src, f"R-F735: CSS class {cls} missing"


def test_entity_rail_source_type_styling_present():
    """R-F735: source chips are colour-coded by type (tool/url/
    primary_source/rag) so the user can at-a-glance see source mix."""
    src = _read_html()
    assert ".src-type.tool" in src
    assert ".src-type.url" in src
    assert ".src-type.primary_source" in src
    assert ".src-type.rag" in src


def test_update_entity_rail_function_present():
    """R-F735: the updateEntityRail() JS function exists and is hooked
    into the SSE event handlers."""
    src = _read_html()
    assert "function updateEntityRail" in src
    # Reads the shared state slots
    assert "_ariaCurrentEntity" in src
    assert "_ariaLatestSources" in src
    # Calls into the rail from both the progress and sources event
    # handlers (so the rail updates progressively during a turn)
    # — R-F737 progress emits entity, R-F732 emits sources.
    # Look for at least two updateEntityRail() invocations from the
    # SSE handler chain.
    handler_invocations = src.count("updateEntityRail()")
    assert handler_invocations >= 2, (
        f"R-F735: expected ≥2 updateEntityRail() calls (from progress + "
        f"sources event handlers); found {handler_invocations}"
    )


def test_entity_rail_action_clear_resets_state():
    """R-F735: 'Clear' button must reset window._ariaCurrentEntity +
    window._ariaLatestSources so the rail returns to empty state."""
    src = _read_html()
    # Find the click delegation for entity-rail-action
    assert "data-action" in src
    # The clear branch must reset both globals
    clear_section_match = re.search(
        r"action === 'clear'[\s\S]{0,300}",
        src,
    )
    assert clear_section_match, "R-F735: clear action handler missing"
    clear_block = clear_section_match.group(0)
    assert "_ariaCurrentEntity" in clear_block
    assert "_ariaLatestSources" in clear_block


def test_entity_rail_source_links_open_externally():
    """R-F735: source chips with URLs must use target=_blank +
    rel=noopener so opening doesn't navigate the chat away or
    inherit the chat origin's permissions."""
    src = _read_html()
    # In updateEntityRail, link nodes get target+rel set
    assert "target = '_blank'" in src or 'target = "_blank"' in src
    assert "noopener" in src


def test_rf735_marker_count():
    """R-F735: ≥4 markers (HTML, CSS block, JS update function,
    handler hook) to detect partial revert."""
    src = _read_html()
    count = src.count("R-F735")
    assert count >= 4, (
        f"R-F735: expected ≥4 marker occurrences across the addition "
        f"zones, found {count}."
    )


def test_existing_chat_layout_intact():
    """R-F735 added a third panel — verify the existing chat-shell and
    convos-panel haven't been clobbered."""
    src = _read_html()
    assert 'id="chat-feed"' in src
    assert 'class="chat-shell"' in src
    assert 'id="convos-panel"' in src
    # And the chat-shell still closes before the main-app close
    assert "</main>" in src
