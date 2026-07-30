"""R-F3504 — an empty PSC register read as opacity. R-F2830 built the fix and nobody called it.

THE DEFECT, from the delivered Babcock report. The Network & Ownership section's entire
visible content was::

    UBO chain nodes traversed 11

Babcock is a listed PLC. Its PSC register is lawfully empty because it trades on a UK
regulated market and discloses ownership through market rules instead. A reader was left
to infer something from an absence that has a checkable explanation, and the natural
inference — undisclosed ownership — is the wrong one.

R-F2830 ALREADY BUILT THE ANSWER. `companies_house.get_psc_exemptions` exists, is
documented, and states the stakes in its own docstring: a company disclosing no beneficial
owners looks opaque, potentially evasive, whereas one exempt because it trades on a
regulated market is behaving entirely normally — "reporting the first when the truth is
the second is a false ACCUSATION, the mirror of a false clean". **Nothing ever called it.**

THREE OUTCOMES, and all three are distinct answers a reader must be able to tell apart:
  * exemption ACTIVE   — the absence is lawful and normal; say so
  * no exemption       — ownership is genuinely undisclosed; a real gap
  * check DID NOT RUN  — unknown. Claim NEITHER, because an unperformed check that
                         resolves to "lawful" is a false clean and one that resolves to
                         "opaque" is a false accusation

COST DISCIPLINE: asked only when the register is EMPTY. An exemption explains nothing when
controllers are disclosed, so the call would be pure waste.
"""
from __future__ import annotations

import pathlib
import re

import pytest

SRC = (pathlib.Path(__file__).resolve().parents[1]
       / "intel" / "dd_orchestrator.py").read_text(encoding="utf-8", errors="replace")


def test_the_reader_is_now_called_at_all():
    """THE DEFECT: R-F2830 shipped a documented reader with no caller."""
    assert "get_psc_exemptions(" in SRC, (
        "the PSC exemption reader is still dormant — an empty register keeps reading as "
        "opacity")


def test_it_uses_the_readers_real_keys():
    """The reader returns `checked` / `has_active_exemption` / `active`. My first cut
    invented `active_exemptions`, which would have silently matched nothing and made the
    exemption branch unreachable — a wiring that looks done and does nothing."""
    from aria_service.intel import companies_house as ch
    import inspect
    reader = inspect.getsource(ch.get_psc_exemptions)
    for key in ('"checked"', '"has_active_exemption"', '"active"'):
        assert key in reader, f"the reader no longer returns {key}"
    assert '_ex.get("has_active_exemption")' in SRC
    assert '_ex.get("active")' in SRC
    assert "active_exemptions" not in SRC, "the invented key is back"


def test_an_active_exemption_is_reported_as_lawful_and_normal():
    # 2200, not 900: the detail is built by multi-line string concatenation, so a short
    # window cuts the match mid-sentence and fails against correct code.
    m = re.search(r"No PSC recorded — a LAWFUL exemption.{0,2200}", SRC, re.S)
    assert m, "the lawful-exemption finding is gone"
    body = m.group(0)
    assert "must not be read as opacity" in body
    assert "regulated market" in body


def test_no_exemption_is_reported_as_genuinely_undisclosed():
    """The direction where silence would hide a REAL signal."""
    assert "no active exemption is" in SRC
    assert "undisclosed rather than lawfully exempt" in SRC


def test_an_unperformed_check_claims_neither():
    """An unperformed check that resolves to 'lawful' is a false clean; one that resolves
    to 'opaque' is a false accusation. It must resolve to neither."""
    m = re.search(r'if not _ex\.get\("checked"\):.{0,700}', SRC, re.S)
    assert m, "the unchecked branch is gone"
    body = m.group(0)
    assert "Not a finding of opacity and not" in body
    assert "a clearance" in body


def test_it_is_only_asked_when_the_register_is_empty():
    """Cost discipline: an exemption explains nothing when controllers are disclosed."""
    assert "if not report.identity.shareholders:" in SRC
    idx = SRC.index("if not report.identity.shareholders:")
    assert "get_psc_exemptions(" in SRC[idx: idx + 1500], (
        "the exemption lookup is no longer gated on an empty register")


def test_the_result_is_persisted_on_the_report():
    """A reader of the stored report must be able to see WHY the register was empty."""
    from aria_service.intel.dd_schema import ARKDDReport
    r = ARKDDReport()
    assert hasattr(r.identity, "psc_exemptions")
    assert r.identity.psc_exemptions == {}
    assert "report.identity.psc_exemptions = _ex" in SRC


def test_the_lookup_cannot_fail_the_identity_layer():
    """An explanatory lookup must never cost the report."""
    m = re.search(r"get_psc_exemptions\(_rg\).{0,3200}", SRC, re.S)
    assert m and "except Exception" in m.group(0)
