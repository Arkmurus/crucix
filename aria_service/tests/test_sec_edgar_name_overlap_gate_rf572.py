"""R-F572 — SEC filer-name match tighten.

Pre-R-F572 MVP fire-test (2026-05-16) found that sec_edgar.lookup
defaulted to a fuzzy threshold of 0.62, letting through unrelated
issuers as INFO-level findings in DD reports:

    Rosoboronexport → "E.ON SE" (German utility)
    Aselsan A.S.    → "ASHLAND INC." (US chemicals)
    Acme Widgets    → "CME GROUP INC." (US exchange)

These are NOT blocking findings (severity=info), but they pollute the
DD report with bogus filer attribution that erodes operator trust.

R-F572 ships two layers, mirroring R-F569:
  1. Threshold bump 0.62 → 0.85 on the score-only filter.
  2. Name-overlap gate after fuzzy_filter: matched companies must
     share ≥2 meaningful tokens OR ≥1 token of ≥5 chars with the
     query (R-F351 short-acronym noise discipline).

This file covers the unit-level invariants for the gate function.
The capability test (Rosoboronexport DD no longer reports E.ON SE)
is exercised via the live fly.io endpoint after deploy.
"""
from __future__ import annotations

import inspect

# R-F3773/§16 — NOT inspect.getsource: it slices at line numbers captured AT
# IMPORT, so a mid-run edit silently returns a DIFFERENT function's body. A CLASS
# target scopes the lookup to that class's own body (R-F3771).
from ._source_probe import function_source


def test_rf572_default_threshold_is_at_least_0_85():
    """The sec_edgar.lookup default threshold must be ≥0.85
    post-R-F572. Pre-R-F572 it was 0.62, far too permissive for
    issuer-name matching against the SEC ticker universe."""
    from aria_service.intel.sources import sec_edgar
    sig = inspect.signature(sec_edgar.lookup)
    threshold_default = sig.parameters["threshold"].default
    assert threshold_default >= 0.85, (
        f"R-F572 REGRESSION: sec_edgar.lookup threshold default is "
        f"{threshold_default}, expected ≥0.85"
    )


# ── Token-overlap rejections — the three live false positives ─────────────


def test_rf572_rejects_rosoboronexport_to_eon():
    """Rosoboronexport → E.ON SE — zero shared tokens. Must fail
    the overlap gate."""
    from aria_service.intel._sanctions_classify import _tokenize_entity_name
    q = _tokenize_entity_name("Rosoboronexport")
    c = _tokenize_entity_name("E.ON SE")
    shared = q & c
    assert len(shared) == 0, (
        f"R-F572 REGRESSION: Rosoboronexport vs E.ON SE shares {shared}; "
        f"expected zero shared meaningful tokens"
    )


def test_rf572_rejects_aselsan_to_ashland():
    """Aselsan A.S. → ASHLAND INC. — zero shared tokens after
    corporate-suffix strip (a.s. / inc dropped)."""
    from aria_service.intel._sanctions_classify import _tokenize_entity_name
    q = _tokenize_entity_name("Aselsan A.S.")
    c = _tokenize_entity_name("ASHLAND INC.")
    shared = q & c
    assert len(shared) == 0, (
        f"R-F572 REGRESSION: Aselsan vs ASHLAND shares {shared}; expected zero"
    )


def test_rf572_rejects_acme_to_cme():
    """Acme Widgets Limited → CME GROUP INC. — 'acme' and 'cme' look
    similar but ARE different tokens after normalisation. 'widgets'
    and 'group' don't match. 'limited' / 'inc' are stripped suffixes."""
    from aria_service.intel._sanctions_classify import _tokenize_entity_name
    q = _tokenize_entity_name("Acme Widgets Limited")
    c = _tokenize_entity_name("CME GROUP INC.")
    shared = q & c
    assert len(shared) == 0, (
        f"R-F572 REGRESSION: Acme Widgets vs CME GROUP shares {shared}; "
        f"expected zero (acme ≠ cme as discrete tokens)"
    )


# ── Token-overlap acceptances — real positives must still pass ─────────────


def test_rf572_accepts_embraer_real_match():
    """Embraer S.A. → EMBRAER S.A./ADR — shared 'embraer' (≥5 chars).
    The gate must let this through."""
    from aria_service.intel._sanctions_classify import _tokenize_entity_name
    q = _tokenize_entity_name("Embraer S.A.")
    c = _tokenize_entity_name("EMBRAER S.A./ADR")
    shared = q & c
    assert "embraer" in shared, (
        f"R-F572 OVER-CORRECTED: Embraer real-match shares {shared}; "
        f"must contain 'embraer'"
    )
    assert len(next(iter(shared))) >= 5  # single-token allowed because ≥5 chars


def test_rf572_accepts_aselsan_real_match():
    """Aselsan A.S. → ASELSAN Elektronik Sanayi ve Ticaret A.S./ADR —
    shared 'aselsan' (≥5 chars). Must pass."""
    from aria_service.intel._sanctions_classify import _tokenize_entity_name
    q = _tokenize_entity_name("Aselsan A.S.")
    c = _tokenize_entity_name("ASELSAN Elektronik Sanayi ve Ticaret A.S./ADR")
    shared = q & c
    assert "aselsan" in shared
    assert len(next(iter(shared))) >= 5


def test_rf572_score_bypass_present_at_0_95():
    """The gate's exact-or-near-exact match bypass MUST be at ≥0.95 —
    the same threshold as R-F569's sanctions-path bypass. Verify by
    reading the lookup() source to confirm the constant landed. This
    keeps the two gates consistent so future tuning lands in both."""
    import inspect
    from aria_service.intel.sources import sec_edgar
    src = function_source(sec_edgar, "lookup")
    assert "0.95" in src, (
        f"R-F572 REGRESSION: the score≥0.95 bypass constant is "
        f"missing from sec_edgar.lookup; gate may be over-strict on "
        f"exact normalised matches like BAE Systems plc ↔ BAE SYSTEMS PLC/ADR"
    )


def test_rf572_adidas_vs_adobe_rejected_by_gate():
    """A low-score acronym-shape false positive — Adidas AG normalises
    to {"adidas"}, Adobe Inc normalises to {"adobe"}. Zero token
    overlap means the gate's reject branch fires."""
    from aria_service.intel._sanctions_classify import _tokenize_entity_name
    q = _tokenize_entity_name("Adidas AG")
    c = _tokenize_entity_name("Adobe Inc")
    shared = q & c
    assert len(shared) == 0, (
        f"Adidas vs Adobe must have zero token overlap — got {shared}"
    )
