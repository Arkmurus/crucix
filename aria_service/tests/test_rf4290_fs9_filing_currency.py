"""R-F4290 / C-244 — FS-9 is answered by the filing evidence the DD already gathers.

Third instance of the C-235 shape. `dd_standard` declared FS-9 ("Statutory
accounts and filings are current, not overdue or in default") with `reader=None`,
so it rendered NOT_RUN "no resolver is bound to this question in this build" while
`financial_health._uk_registry_accounts` fetched exactly that evidence on every GB
run and parked it at `compliance.financial_health.registry_accounts`.

The evidence was deliberately withheld from FS-10, and correctly: that function's
own docstring says "THIS IS EVIDENCE, NOT A VERDICT ... answering financial
capacity from filing dates would be a false clean." **FS-9 is the question filing
dates DO answer**, and it had no reader to answer it.

FS-9 NAMES TWO FILINGS — accounts AND the confirmation statement — so the same
rule as IS-14 applies: a FINDING always answers, but a clean line needs both
halves. An overdue accounts filing is a definitive adverse answer on its own; a
current accounts filing with the confirmation statement unknown is half a question.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aria_service.intel import dd_standard as ds  # noqa: E402

ANSWERED = {ds.EvidenceState.CORROBORATED.value, ds.EvidenceState.SINGLE_SOURCE.value}

#: A confirmation statement that is KNOWN and current. `{"overdue": False}`
#: alone is deliberately NOT this: with no due date on file, "not flagged
#: overdue" is indistinguishable from "we never saw the block", which is
#: exactly what `_confirmation_block.known` exists to separate.
_CURRENT_CS = {"next_due": "2026-06-01", "overdue": False,
               "last_made_up_to": "2025-06-01"}


def _accounts(*, filed=True, overdue=False, flags=None, made_up="2025-03-31"):
    f = list(flags if flags is not None else ([] if not overdue else ["accounts_overdue"]))
    return {"filed": filed, "overdue": overdue, "last_made_up_to": made_up if filed else "",
            "last_type": "small", "next_due": "2026-12-31", "distress_flags": f,
            "has_figures": False}


def _report(registry_accounts=None, **extra):
    fin = {}
    if registry_accounts is not None:
        fin["registry_accounts"] = registry_accounts
    return {"subject": {"name": "PROBE LTD", "jurisdiction": "GB"},
            "compliance": {"financial_health": fin}, **extra}


def _fs9(registry_accounts=None, **extra) -> dict:
    rows = ds.assess(_report(registry_accounts, **extra), tier="ENHANCED")["resolutions"]
    return {r["question_id"]: r for r in rows}["FS-9"]


def _reg(accounts=None, confirmation=None):
    out = {"source": "companies_house", "company_number": "01234567",
           "accounts": accounts if accounts is not None else _accounts(),
           "source_url": "https://find-and-update.company-information.service.gov.uk/"
                         "company/01234567/filing-history"}
    if confirmation is not None:
        # Normalise through the PRODUCER's helper, so the fixture carries the
        # shape production actually writes (including `known`) rather than a
        # hand-made dict that could drift from it.
        from aria_service.intel.companies_house import _confirmation_block
        out["confirmation_statement"] = _confirmation_block(confirmation)
    return out


# ── the defect ─────────────────────────────────────────────────────────────

def test_filing_evidence_is_no_longer_reported_as_unbound() -> None:
    """THE CAPABILITY TEST — the live symptom C-244 files."""
    row = _fs9(_reg(confirmation=_CURRENT_CS))
    assert "no resolver is bound" not in str(row["reason"])
    assert row["state"] in ANSWERED, row


def test_both_filings_current_credits_coverage() -> None:
    before = ds.assess(_report(), tier="ENHANCED")
    after = ds.assess(_report(_reg(confirmation=_CURRENT_CS)), tier="ENHANCED")
    assert after["answered"] > before["answered"]
    assert after["coverage_pct"] > before["coverage_pct"]


def test_overdue_accounts_are_reported_as_a_finding() -> None:
    """An overdue statutory filing is a standard early-distress signal."""
    row = _fs9(_reg(_accounts(overdue=True), confirmation=_CURRENT_CS))
    assert row["state"] in ANSWERED
    assert "overdue" in str(row["reason"]).lower()


def test_no_accounts_ever_filed_is_reported() -> None:
    row = _fs9(_reg(_accounts(filed=False, flags=["no_accounts_filed"]),
                    confirmation=_CURRENT_CS))
    assert row["state"] in ANSWERED
    assert "no accounts" in str(row["reason"]).lower() or "never" in str(row["reason"]).lower()


# ── never a fabricated clean ───────────────────────────────────────────────

def test_no_registry_evidence_is_not_run() -> None:
    row = _fs9()
    assert row["state"] == ds.EvidenceState.NOT_RUN.value
    assert row["state"] not in ANSWERED


def test_a_current_accounts_filing_alone_does_not_clear_the_question() -> None:
    """FS-9 names accounts AND the confirmation statement (the IS-14 rule).

    Half the question answered is honest partial evidence, never a clean line on
    the whole of it.
    """
    row = _fs9(_reg())                       # no confirmation_statement block
    assert row["state"] == ds.EvidenceState.ATTEMPTED_INCONCLUSIVE.value
    assert "confirmation" in str(row["reason"]).lower()


def test_an_overdue_confirmation_statement_is_adverse_even_with_clean_accounts() -> None:
    row = _fs9(_reg(confirmation={"overdue": True, "next_due": "2025-01-01"}))
    assert row["state"] in ANSWERED
    assert "confirmation" in str(row["reason"]).lower()


def test_an_adverse_accounts_finding_answers_even_without_the_confirmation_half() -> None:
    """A finding always answers — the same rule as IS-14."""
    row = _fs9(_reg(_accounts(overdue=True)))
    assert row["state"] in ANSWERED


def test_a_malformed_block_is_never_an_answer() -> None:
    for junk in ("filed", 0, [], {"accounts": "yes"}, {"accounts": {}}):
        row = _fs9(junk)
        assert row["state"] not in ANSWERED, junk


# ── the producer must actually carry the confirmation statement ────────────

def test_the_profile_exposes_the_confirmation_statement_block() -> None:
    """R-F4290 extends the profile: `confirmation_next_due` alone cannot say
    whether the filing is LATE, and deriving that from today's date in a reader
    would make the answer depend on when the report is re-read."""
    from aria_service.intel.companies_house import _confirmation_block

    block = _confirmation_block({"next_due": "2026-06-01", "overdue": True,
                                 "last_made_up_to": "2025-06-01"})
    assert block["overdue"] is True
    assert block["next_due"] == "2026-06-01"
    # absent or malformed must not read as a clean filing
    for junk in (None, {}, "soon", 0):
        assert _confirmation_block(junk)["overdue"] is False
        assert _confirmation_block(junk)["known"] is False
    assert _confirmation_block({"next_due": "2026-06-01", "overdue": False})["known"] is True


def test_the_registry_evidence_carries_it_through() -> None:
    """A block the producer fetches but does not pass on is not evidence."""
    src = (ROOT / "aria_service/intel/financial_health.py").read_text(encoding="utf-8")
    assert "confirmation_statement" in src, (
        "financial_health does not carry the confirmation statement into "
        "registry_accounts, so FS-9 can never see it"
    )


# ── it must not disturb anything else ──────────────────────────────────────

def test_binding_fs9_changes_no_other_question() -> None:
    base = _report()
    before = {r["question_id"]: r["state"]
              for r in ds.assess(base, tier="ENHANCED")["resolutions"]}
    after = {r["question_id"]: r["state"] for r in ds.assess(
        _report(_reg(confirmation=_CURRENT_CS)), tier="ENHANCED")["resolutions"]}
    assert {q for q in before if before[q] != after.get(q)} == {"FS-9"}


def test_fs10_still_refuses_to_answer_from_filing_dates() -> None:
    """The boundary that made this evidence unusable for FS-10 is UNTOUCHED.

    Filing metadata answers FS-9 (are the filings current) and must never answer
    FS-10 (can they perform) — `_uk_registry_accounts` calls that a false clean.
    """
    rows = {r["question_id"]: r for r in ds.assess(
        _report(_reg(confirmation=_CURRENT_CS)), tier="ENHANCED")["resolutions"]}
    assert rows["FS-10"]["state"] not in ANSWERED


def test_the_reader_is_actually_bound() -> None:
    assert ds.QUESTIONS_BY_ID["FS-9"].reader is not None
