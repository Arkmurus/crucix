"""R-F3708 — CAPABILITY: intel adapters name their subject, so provenance is
EARNED rather than the gate being relaxed.

THE FINDING (peer agent, live measurement): only 13 of 39 Golden Intel signals
carried `source_adapter` provenance — 26 graded `classifier_template` and were
correctly refused by the channel. The gate is right and MUST NOT be relaxed:
publishing canned category prose as ARIA's own analysis is exactly what
R-F2899/R-F2930 exist to prevent.

The defect is on the SUPPLY side. `_is_item_specific` accepts any one of three
signals in the `why_it_matters` text — a number, the target's name, or an
extracted entity. Two adapters KNEW their subject and never put it in the
sentence:

  * sanctions_diff  opened with "New OFAC SDN sanctions designation." and
                    appended jurisdiction / programs / listing date only when
                    the upstream record carried them. On a sparse record all
                    three are absent, leaving a sentence that describes every
                    designation ever made.
  * public_watchlist used upstream `detail` ("Previously clean, now
                    sanctioned.") whenever present — prose about a TRANSITION
                    with no subject. Only its FALLBACK named the entity.

Naming the subject makes the text genuinely item-specific — it identifies one
record instead of a category — and is a strictly better sentence for a human.

Run: python -m pytest aria_service/tests/test_rf3708_intel_provenance_supply.py -v
"""
from __future__ import annotations

import pytest

from aria_service.intel.golden_intel_bridge import _is_item_specific

# R-F3785/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import module_source


# ── The gate itself must NOT have been relaxed ─────────────────────────────

def test_generic_category_prose_is_still_rejected():
    """The exact sentence R-F2930 was written for."""
    why = ("Security conditions may affect delivery risk, end-use risk, or "
           "market timing.")
    assert _is_item_specific(why, "signal", {}) is False, (
        "relaxing this gate would publish canned prose as ARIA's own analysis"
    )


def test_a_bare_designation_sentence_is_still_rejected():
    """What sanctions_diff used to emit on a sparse record."""
    assert _is_item_specific("New OFAC SDN sanctions designation.", "signal", {}) is False


def test_a_bare_transition_sentence_is_still_rejected():
    """What public_watchlist used to pass through from `detail`."""
    assert _is_item_specific("Previously clean, now sanctioned.", "signal", {}) is False


def test_an_empty_why_is_never_item_specific():
    for why in ("", "   ", "short"):
        assert _is_item_specific(why, "Rosoboronexport", {}) is False


# ── Naming the subject EARNS provenance ────────────────────────────────────

def test_naming_the_entity_earns_provenance_on_a_sparse_designation():
    """No jurisdiction, no programs, no date — only the subject."""
    why = "Rosoboronexport: new OFAC SDN sanctions designation."
    assert _is_item_specific(why, "Rosoboronexport", {}) is True, (
        "a sentence that identifies ONE record is item-specific by definition"
    )


def test_naming_the_entity_earns_provenance_on_a_bare_transition():
    why = "Rosoboronexport: Previously clean, now sanctioned."
    assert _is_item_specific(why, "Rosoboronexport", {}) is True


def test_a_dated_designation_was_already_specific():
    """The number path — unchanged, and still sufficient on its own."""
    why = "New OFAC SDN sanctions designation. Listed 2026-07-20."
    assert _is_item_specific(why, "Rosoboronexport", {}) is True


# ── The adapters now build it that way ─────────────────────────────────────

def test_sanctions_diff_why_opens_with_the_entity():
    import inspect
    from aria_service.intel import golden_intel_bridge as gib

    src = module_source(gib)
    assert 'why = (f"{entity}: "' in src, (
        "sanctions_diff must name its subject — the entity is already bound as "
        "`target` two lines below, it was simply never put in the sentence"
    )


def test_public_watchlist_prefixes_the_entity_when_detail_omits_it():
    import inspect
    from aria_service.intel import golden_intel_bridge as gib

    src = module_source(gib)
    assert 'entity.lower() in _clean(a.get("detail")).lower()' in src, (
        "when upstream `detail` already names the entity we must not duplicate "
        "it; when it does not, the subject has to be prefixed"
    )


def test_public_watchlist_does_not_read_a_field_that_does_not_exist():
    """§3b — rescreen_public_watchlist emits no country on its alerts."""
    import inspect
    from aria_service.intel import golden_intel_bridge as gib

    # Strip COMMENTS before asserting — the code's own rationale NAMES the
    # field it refuses to read. This is the second time in this session a
    # structural assertion matched its subject's prose instead of its code;
    # matching prose is how a structural test lies to you.
    src = "\n".join(
        ln for ln in module_source(gib).splitlines()
        if not ln.strip().startswith("#")
    )
    assert 'a.get("country")' not in src, (
        "the public-watchlist alert carries {entity, change_type, old_status, "
        "new_status, old_score, new_score, detail, timestamp, scope} and no "
        "country — reading one would be always-empty and misleading"
    )


# ── The shapes the adapters now produce, end to end ────────────────────────

@pytest.mark.parametrize("entity,detail,expected_specific", [
    ("Rosoboronexport", "", True),                              # fallback path
    ("Rosoboronexport", "Previously clean, now sanctioned.", True),   # prefixed
    ("Rosoboronexport", "Rosoboronexport moved to HIT.", True),       # already named
    ("JSC Kalashnikov", "New designation under EO 14024.", True),     # prefixed
])
def test_public_watchlist_why_shapes_are_item_specific(entity, detail, expected_specific):
    """Mirrors the expression now shipped in the adapter."""
    clean = (detail or "").strip()
    if clean and entity.lower() in clean.lower():
        why = clean
    elif clean:
        why = f"{entity}: {clean}"
    else:
        why = f"{entity}: CLEAN -> HIT."
    assert _is_item_specific(why, entity, {"countries": [], "products": [], "oems": []}) is expected_specific


@pytest.mark.parametrize("countries,programs,date", [
    ("", "", ""),                       # the sparse record that used to fail
    ("RU", "", ""),
    ("", "UKRAINE-EO14024", ""),
    ("RU", "UKRAINE-EO14024", "2026-07-20"),
])
def test_sanctions_diff_why_shapes_are_item_specific(countries, programs, date):
    entity = "Rosoboronexport"
    why = (f"{entity}: new OFAC SDN sanctions designation."
           + (f" Jurisdiction: {countries}." if countries else "")
           + (f" Programs: {programs}." if programs else "")
           + (f" Listed {date}." if date else ""))
    assert _is_item_specific(why, entity, {}) is True, (
        f"sparse={not any((countries, programs, date))} — naming the subject "
        f"must be sufficient on its own"
    )
