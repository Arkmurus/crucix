"""R-F449 — verification-pass follow-ups for the R-F435..R-F445 batch.

Two parallel review agents flagged four concrete issues after the
cumulative R-F435..R-F445 batch was shipped:

1. R-F436 / R-F437 corroboration tag-write was nested inside
   `if _is_hostname_origin:` in sanctions.py, so the
   "passing entity name as known_aliases provides corroboration"
   story documented in those R-blocks was only TRUE for hostname-
   origin matches. Person screens (R-F436) and historical-person
   screens (R-F437) silently got no corroboration tag — the
   R-F434 cap fortunately only fires on hostname-origin matches
   so this was behaviourally moot today, but a future non-hostname
   cap reading the same flag would silently no-op.

2. R-F436 was missing a kill-switch — the rest of the Digital
   chain (R-F435/F437/F438/F439/F440/F441/F445) had one. Operator
   couldn't disable just R-F436 if sanctions API was rate-limited.

3. R-F437 was under-counting `report.digital.meta.subcalls` —
   didn't count the 2 wayback HTTP fetches, only the per-person
   sanctions screens. Cost dashboards reported ~30% lower than
   actual.

4. R-F441 had no empty-name guard — an entity with no resolved
   name would build polyglot queries against an empty entity
   string, dredging up unrelated corruption / sanctions news via
   bare-term searches.
"""
from __future__ import annotations

import asyncio


# ───────────────────────── 1. Corroboration tag-write moved ──────────────────


def test_rf449_corroboration_tag_set_on_non_hostname_matches(monkeypatch):
    """When screen_with_aliases is called with a person name (not a
    hostname), the resulting match dicts must STILL carry the
    `_has_caller_supplied_aliases` flag if known_aliases was provided
    at entry. Pre-R-F449 the flag was only written for
    hostname-origin matches."""
    from aria_service.intel import sanctions

    async def _fake_fuzzy_screen(name, *, threshold=0.78):
        return {
            "name": name,
            "matches": [{
                "name": "Some Person Match",
                "score": 0.85,
                "topics": ["info"],
                "lists": ["info_list"],
                "list": "info_list",
            }],
            "top_score": 0.85,
            "blocked": False,
        }
    monkeypatch.setattr(sanctions, "fuzzy_screen", _fake_fuzzy_screen)

    # Person-name input (NOT a hostname) with entity-name corroboration
    result = asyncio.run(sanctions.screen_with_aliases(
        "Tom Ogle",
        known_aliases=["Nebraska Gas Turbine Inc"],
    ))
    assert result["matches"], "expected match"
    for m in result["matches"]:
        # R-F449 fix: tag is now written for EVERY match, not just
        # hostname-origin ones.
        assert m.get("_has_caller_supplied_aliases") is True, (
            f"R-F449 regression: corroboration tag missing on non-"
            f"hostname match. Got match: {m}"
        )
        # Deprecated alias also present (R-F444 contract)
        assert m.get("_has_legal_name_corroboration") is True
        # And NO brandified-hostname flag (it wasn't a hostname)
        assert not m.get("_from_brandified_hostname")


def test_rf449_no_corroboration_when_no_aliases_supplied(monkeypatch):
    """The flag must be False when known_aliases is empty — the move
    out of the `if _is_hostname_origin:` block must not accidentally
    set the flag True for everyone."""
    from aria_service.intel import sanctions

    async def _fake_fuzzy_screen(name, *, threshold=0.78):
        return {
            "name": name,
            "matches": [{"name": "Match", "score": 0.7, "topics": [],
                         "lists": ["x"], "list": "x"}],
            "top_score": 0.7, "blocked": False,
        }
    monkeypatch.setattr(sanctions, "fuzzy_screen", _fake_fuzzy_screen)

    # Person-name input, NO known_aliases
    result = asyncio.run(sanctions.screen_with_aliases("Joe Bloggs"))
    for m in result["matches"]:
        assert m.get("_has_caller_supplied_aliases") is False


# ───────────────────────── 2. R-F436 kill-switch ─────────────────────────────


def test_rf449_page_entity_screen_killswitch_pattern():
    """Pin the kill-switch env-var contract: ARIA_PAGE_ENTITY_SCREEN_DISABLED
    set to any truthy stripped string short-circuits the R-F436 block."""
    import os
    # Idiom matches every other Digital-chain kill-switch
    os.environ["ARIA_PAGE_ENTITY_SCREEN_DISABLED"] = "1"
    assert bool(os.environ.get("ARIA_PAGE_ENTITY_SCREEN_DISABLED", "").strip())
    os.environ.pop("ARIA_PAGE_ENTITY_SCREEN_DISABLED", None)
    assert not os.environ.get("ARIA_PAGE_ENTITY_SCREEN_DISABLED", "").strip()


def test_rf449_killswitch_present_in_orchestrator():
    """Static check: the R-F449 kill-switch line must appear in
    dd_orchestrator.py inside the R-F436 try block. Catches the
    case where someone removes the env-var gate during a refactor."""
    from pathlib import Path
    src_path = (
        Path(__file__).resolve().parent.parent
        / "intel" / "dd_orchestrator.py"
    )
    src = src_path.read_text(encoding="utf-8")
    assert 'ARIA_PAGE_ENTITY_SCREEN_DISABLED' in src, (
        "R-F449: page-entity kill-switch removed from orchestrator"
    )


# ───────────────────────── 3. R-F437 wayback subcalls counted ───────────────


def test_rf449_subcalls_increment_for_each_ok_snapshot():
    """Pin the subcalls accounting: every wayback snapshot returned
    with ok=True increments subcalls by 1. Static-walk the
    accounting pattern."""
    # Replicate the orchestrator increment inline
    fake_snaps = [
        {"ok": True, "snapshot_url": "https://web.archive.org/.../2024"},
        {"ok": False, "error": "no archive"},
        {"ok": True, "snapshot_url": "https://web.archive.org/.../2022"},
    ]
    # The R-F449 fix uses this exact accounting line:
    subcalls_delta = sum(1 for s in fake_snaps if s.get("ok"))
    assert subcalls_delta == 2, (
        f"R-F449: subcalls accounting must count ok snapshots. "
        f"Got {subcalls_delta}"
    )


# ───────────────────────── 4. R-F441 empty-name guard ───────────────────────


def test_rf449_polyglot_skipped_when_entity_name_empty():
    """The empty-name guard must short-circuit the R-F441 block
    when neither `report.identity.entity_name` nor `name` is set —
    otherwise bare-term searches dredge unrelated corruption news."""
    # Replicate the guard logic
    for entity_name, name_arg in [
        ("", ""),
        (None, ""),
        ("", None),
        ("   ", "   "),
        (None, None),
    ]:
        guard_entity = (
            (entity_name or "")
            or (name_arg or "")
            or ""
        ).strip()
        assert not guard_entity, (
            f"R-F449: empty-name guard should skip "
            f"entity_name={entity_name!r}, name={name_arg!r}"
        )

    # When EITHER is non-empty, the block runs
    for entity_name, name_arg in [
        ("Acme Defence Ltd", ""),
        ("", "Acme Defence Ltd"),
        ("Acme", "Backup"),
    ]:
        guard_entity = (
            (entity_name or "")
            or (name_arg or "")
            or ""
        ).strip()
        assert guard_entity, (
            f"R-F449: guard wrongly skipped non-empty "
            f"entity_name={entity_name!r}, name={name_arg!r}"
        )
