"""R-F4291 / C-245 — IS-16 is answered by the debarment register already consulted.

Fourth instance of the C-235 shape, and the first HYBRID one. IS-16 ("Fraud,
bribery or financial-crime convictions, and regulatory penalties") rendered
NOT_RUN "no resolver is bound to this question in this build" while the DD's
primary-source fan-out called `sources.worldbank_debarred` on every run
(dd_orchestrator:2986) and R-F2843 recorded whether that list answered
(`identity.sanctions_screen.primary_snapshots`).

World Bank debarment is squarely in IS-16's scope: it is an enforcement action
for fraud, corruption or collusion, cross-recognised by AfDB/AsDB/EBRD/IDB under
MCEA 2010.

THE HYBRID HALF IS THE POINT. IS-16's pass condition is "Enforcement registers
are consulted; **a formal criminal-record check is counterparty-supplied**". So a
clean debarment register is NOT a pass — it answers the open-source half and
leaves a stated boundary, which is exactly what AWAITING_COUNTERPARTY is for
("they are a stated boundary, not a failure to look"). An adverse finding, by
contrast, answers on its own: a live debarment is a refusal ground whatever the
counterparty later supplies.

R-F2843's own rule is what makes this safe: "an unstamped source asserts nothing,
because defaulting to 'ok' would claim a check we never made."
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
DEBAR_SOURCE = "sources.worldbank_debarred"


def _report(*, snapshots=None, findings=(), **extra) -> dict:
    identity = {"entity_name": "PROBE LTD", "findings": list(findings)}
    if snapshots is not None:
        identity["sanctions_screen"] = {"primary_snapshots": snapshots}
    return {"subject": {"name": "PROBE LTD", "jurisdiction": "GB"},
            "identity": identity, **extra}


def _is16(**kw) -> dict:
    rows = ds.assess(_report(**kw), tier="ENHANCED")["resolutions"]
    return {r["question_id"]: r for r in rows}["IS-16"]


def _debarment(active=True) -> dict:
    return {"severity": "red" if active else "info",
            "title": ("World Bank debarment (active): PROBE LTD" if active
                      else "World Bank debarment (expired): PROBE LTD"),
            "detail": "Grounds: fraud.", "source": DEBAR_SOURCE,
            "confidence": "PROBABLE" if active else "ASSESSED"}


# ── the defect ─────────────────────────────────────────────────────────────

def test_a_consulted_register_is_no_longer_reported_as_unbound() -> None:
    """THE CAPABILITY TEST — the live symptom C-245 files."""
    row = _is16(snapshots={"wb_debarred": "ok"})
    assert "no resolver is bound" not in str(row["reason"])
    assert "debarment" in str(row["reason"]).lower()


def test_an_active_debarment_is_an_adverse_ANSWER() -> None:
    """A live debarment is a refusal ground whatever the counterparty supplies."""
    row = _is16(snapshots={"wb_debarred": "ok"}, findings=[_debarment()])
    assert row["state"] in ANSWERED
    assert "debarment" in str(row["reason"]).lower()


def test_an_expired_debarment_is_still_reported() -> None:
    row = _is16(snapshots={"wb_debarred": "ok"}, findings=[_debarment(active=False)])
    assert row["state"] in ANSWERED


# ── the HYBRID boundary: a clean register is not a pass ────────────────────

def test_a_clean_register_awaits_the_counterparty_half() -> None:
    """IS-16's pass condition names a counterparty-supplied criminal-record check.

    Clearing the open-source half and calling the whole question answered would
    be a false clean on the most consequential integrity question in the set.
    """
    row = _is16(snapshots={"wb_debarred": "ok"})
    assert row["state"] == ds.EvidenceState.AWAITING_COUNTERPARTY.value
    assert row["state"] not in ANSWERED
    assert "criminal-record" in str(row["reason"]).lower() or \
           "criminal record" in str(row["reason"]).lower()


def test_awaiting_counterparty_still_counts_in_the_denominator() -> None:
    """It is an outstanding item, not an excused one (only NOT_APPLICABLE is excused)."""
    assert ds.EvidenceState.AWAITING_COUNTERPARTY.value not in ds._EXCLUDED_FROM_DENOMINATOR
    assert ds.EvidenceState.AWAITING_COUNTERPARTY.value not in ds._ANSWERED_STATES


# ── never a fabricated clean ───────────────────────────────────────────────

def test_no_screen_on_the_report_is_not_run() -> None:
    row = _is16()
    assert row["state"] == ds.EvidenceState.NOT_RUN.value


def test_an_unstamped_source_asserts_nothing() -> None:
    """R-F2843's rule, enforced here: a snapshot dict that never mentions the
    register must NOT read as 'the register answered'."""
    row = _is16(snapshots={"ofac_sdn": "ok", "un_sc": "ok"})
    assert row["state"] == ds.EvidenceState.NOT_RUN.value
    assert row["state"] not in ANSWERED


def test_an_unavailable_register_is_attempted_not_clean() -> None:
    row = _is16(snapshots={"wb_debarred": "unavailable"})
    assert row["state"] == ds.EvidenceState.ATTEMPTED_INCONCLUSIVE.value
    assert row["state"] not in ANSWERED


def test_an_adverse_finding_answers_even_if_the_register_reads_unavailable() -> None:
    """A hit already in hand is evidence regardless of the snapshot."""
    row = _is16(snapshots={"wb_debarred": "unavailable"}, findings=[_debarment()])
    assert row["state"] in ANSWERED


def test_a_malformed_screen_is_never_an_answer() -> None:
    for junk in ("ok", 0, [], {"wb_debarred": None}, {"wb_debarred": 1}):
        row = _is16(snapshots=junk)
        assert row["state"] not in ANSWERED, junk


def test_an_unrelated_finding_does_not_answer_is16() -> None:
    """Only the debarment register's own findings count here."""
    other = {"severity": "red", "title": "Adverse media", "source": "web_search"}
    row = _is16(snapshots={"wb_debarred": "ok"}, findings=[other])
    assert row["state"] == ds.EvidenceState.AWAITING_COUNTERPARTY.value


# ── it must not disturb anything else ──────────────────────────────────────

def test_binding_is16_changes_no_other_question() -> None:
    before = {r["question_id"]: r["state"]
              for r in ds.assess(_report(), tier="ENHANCED")["resolutions"]}
    after = {r["question_id"]: r["state"] for r in ds.assess(
        _report(snapshots={"wb_debarred": "ok"}), tier="ENHANCED")["resolutions"]}
    moved = {q for q in before if before[q] != after.get(q)}
    assert moved <= {"IS-16", "IS-13"}, moved


def test_the_reader_is_actually_bound() -> None:
    assert ds.QUESTIONS_BY_ID["IS-16"].reader is not None
