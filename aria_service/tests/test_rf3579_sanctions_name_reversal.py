"""R-F3579 — a reversed name survived the sanctions coincidence filter.

LIVE (dd_acaee511f0f4, Wilson James Limited, reg 02269560 — a London security
contractor). The identity panel read "Sanctions matches 1". The match was:

    JAMES WILSON, Alejandro Antonio (score 0.50, lists: ofac_sdn, matched_via=weak_match)

An individual on the OFAC SDN list, surfaced against a company because the company's
name is two forenames.

R-F2994's coincidence filter could not see it: it drops a candidate sharing NO
distinctive token with the subject, and {wilson, james} & {james, wilson, alejandro,
antonio} is NON-empty, so the reversal read as a genuine partial match. That is the
same set-blindness as R-F3574/R-F3576 in the FCA matcher — a third module, one root
cause: a set intersection cannot see order.

DISPLAY-ONLY, DELIBERATELY. Like R-F2994 before it, this changes only the
transparency list a reader scans and the count printed above it. The
never-false-clean BLOCKING path (fuzzy_screen corroboration) is untouched, and the
screen is still disclosed — when the only hit is a reversal the report falls through
to "Sanctions/PEP screen — no entity-name match", which is honest, not silent.
"""
from __future__ import annotations

import inspect

from aria_service.intel import dd_orchestrator as dd

# R-F3784/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import module_source


# ── the decision ──────────────────────────────────────────────────────────────

def test_the_live_ofac_reversal_is_recognised():
    """PROVE RED: this produced "Sanctions matches 1" on a real delivered report."""
    assert dd._is_name_reversal(
        "Wilson James Limited", "JAMES WILSON, Alejandro Antonio") is True


def test_a_plain_reversal_is_recognised():
    assert dd._is_name_reversal("Wilson James Limited", "James Wilson") is True


def test_the_same_order_is_NOT_a_reversal():
    """A genuine related entity must keep showing — suppressing it would trade a
    false positive for a false clean, which on this path is far worse."""
    assert dd._is_name_reversal("Wilson James Limited", "Wilson James Holdings") is False


def test_a_single_distinctive_token_can_never_be_a_reversal():
    """One token has no order to get wrong. Without this guard every one-word
    subject would have its real matches discarded."""
    assert dd._is_name_reversal("Chemring Group PLC", "Chemring Countermeasures") is False
    assert dd._is_name_reversal("Rosoboronexport", "ROSOBORONEXPORT JSC") is False


def test_a_partial_overlap_is_left_to_the_token_filter():
    """R-F2994 already answers this one; the reversal check must not double-judge it."""
    assert dd._is_name_reversal(
        "Silverbrook Capital Management", "System Capital Management") is False


def test_an_unrelated_name_is_not_a_reversal():
    assert dd._is_name_reversal("Wilson James Limited", "Smith Jones Ltd") is False


def test_extra_tokens_on_the_candidate_do_not_hide_a_reversal():
    """The R-F3574 failure mode: extra tokens made the sets unequal and the check
    never ran. Only the SHARED tokens' order is compared."""
    assert dd._is_name_reversal(
        "Wilson James Limited", "JAMES WILSON trading as Acme Security Services") is True


def test_empty_and_malformed_input_is_safe():
    for a, b in (("", ""), ("Wilson James", ""), ("", "James Wilson"), ("A B", "B A")):
        assert isinstance(dd._is_name_reversal(a, b), bool)


# ── it is actually wired into the filter, and nothing goes silent ─────────────

def _identity_source() -> str:
    """The filter lives inline in the identity layer; find it by its anchor."""
    src = module_source(dd)
    i = src.index("_coincidences = [")
    return src[i - 1200: i + 1800]


def test_the_reversal_check_is_wired_into_the_coincidence_filter():
    """R-F3515's lesson: a helper nothing calls is indistinguishable from no fix."""
    blk = _identity_source()
    assert "_is_name_reversal(" in blk, (
        "the reversal check is not used by the coincidence filter"
    )


def test_a_dropped_reversal_still_counts_as_screened_noise_not_silence():
    """THE SAFETY PROPERTY. Coincidences must feed `_noise_info_n`, or a report whose
    ONLY hit was a reversal would print nothing at all about the screen — turning a
    false positive into a false clean."""
    blk = _identity_source()
    assert "len(_coincidences)" in blk, (
        "dropped coincidences no longer feed the noise count — a reversal-only "
        "screen would go SILENT"
    )
    assert "no entity-name match" in blk, (
        "the honest fallback finding is missing from this branch"
    )


def test_the_blocking_path_is_not_touched():
    """The never-false-clean guarantee: this is display-only. R-F2994 says so and
    R-F3579 must not have widened it into the screen itself."""
    blk = _identity_source()
    assert "DISPLAY-ONLY" in blk.upper() or "Display-only" in blk, (
        "the display-only contract is no longer stated next to the filter"
    )
    # the filter must operate on the DISPLAY list, never on `matches`/`screen`
    assert "_real_info" in blk
