"""R-F3567 — the SUCCESS branch must actually FIRE, not merely be present.

The wiring audit is a TEXTUAL gate: it asks whether `wire_success(` appears in a
module. That is enough to fail a module that has no sink at all, and nowhere near
enough to prove the sink runs. A `wire_success` sitting on an unreachable branch,
or after an early `return`, passes the audit and signals nothing — which is the
green-tests-cannot-see-unreachable-code class this repo has already been bitten by.

So the audit going green at R-F3567 is checked here the only way that means
anything: CALL each newly-wired function for real and assert the brain saw it.

Deliberately NOT asserted: that every module signals. These are the ones whose
success path can be driven without network, without a live store and without an
LLM. The rest are covered by their own capability tests; a test that mocked its
way to a green tick on all ten would be measuring the mocks.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def captured(monkeypatch):
    """Capture wire_success at the source AND at every module-level rebinding.

    `from .engine_wiring import wire_success` copies the function object into the
    importing module's namespace, so patching only `engine_wiring.wire_success`
    would miss every module that imports it at the top. Both are patched.

    monkeypatch (never a bare `module.func = fake`) because an unrestored swap is
    how R-F3449's order-dependent failures were built — seven of them from two
    tests.
    """
    calls: list[dict] = []

    def _fake(**kwargs):
        calls.append(kwargs)

    from aria_service.intel import engine_wiring
    monkeypatch.setattr(engine_wiring, "wire_success", _fake)

    for mod_path in (
        "aria_service.intel.country_sanctions",
        "aria_service.intel.dd_case_archive",
        "aria_service.intel.zefix",
        "aria_service.intel.eval_judge",
        "aria_service.intel.memory_wal",
    ):
        try:
            import importlib
            mod = importlib.import_module(mod_path)
        except Exception:  # pragma: no cover - module unavailable in this env
            continue
        if hasattr(mod, "wire_success"):
            monkeypatch.setattr(mod, "wire_success", _fake, raising=False)
    return calls


def test_country_sanctions_signals_when_it_answers(captured):
    """format_regime_answer is the PRIMARY 'is <country> sanctioned?' path."""
    from aria_service.intel import country_sanctions

    answer = country_sanctions.format_regime_answer("Russia")
    assert answer.get("found") is True, "fixture country is not in the index"
    assert captured, (
        "format_regime_answer returned a real answer and the brain heard nothing"
    )
    sig = captured[-1]
    assert sig["module"] == "country_sanctions"
    assert "regime" in sig["summary"]


def test_country_sanctions_does_not_signal_when_it_finds_nothing(captured):
    """The early 'not found' return must NOT report a successful screen —
    that would be a clean-looking signal for a country never checked."""
    from aria_service.intel import country_sanctions

    answer = country_sanctions.format_regime_answer("Atlantis-Not-A-Country")
    assert answer.get("found") is False
    assert not captured, f"a not-found lookup emitted a success signal: {captured}"


def test_eccn_lookup_signals_on_a_control_list_hit(captured):
    """The export-control screen: a HIT proves the control list is loaded and
    matching, which is the distinction R-F3105 was written about."""
    from aria_service.intel.sources import eccn_lookup

    if not eccn_lookup.dataset_available():
        pytest.skip("ECCN seed dataset not present in this environment")

    hits = eccn_lookup.lookup_by_keyword("inertial navigation system")
    if not hits:
        pytest.skip("seed dataset does not contain the fixture keyword")
    assert captured, "an ECCN match was returned and the brain heard nothing"
    assert captured[-1]["module"] == "sources.eccn_lookup"


def test_eccn_lookup_stays_quiet_on_a_no_match(captured):
    """Per-item screening: a no-match must not signal, or the ledger fills with
    non-events and the real hits become unreadable."""
    from aria_service.intel.sources import eccn_lookup

    if not eccn_lookup.dataset_available():
        pytest.skip("ECCN seed dataset not present in this environment")
    eccn_lookup.lookup_by_keyword("zzzz-no-such-controlled-item-zzzz")
    assert not captured, f"a no-match lookup emitted a success signal: {captured}"


def test_dd_case_archive_signals_on_a_real_archive_write(captured, tmp_path, monkeypatch):
    """The cold tier had `wire_success` IMPORTED and never called, so it could
    only ever report its own failures."""
    from aria_service.intel import dd_case_archive

    monkeypatch.setattr(dd_case_archive, "_DB_PATH", tmp_path / "archive.db", raising=False)

    dd_case_archive.archive_entry({
        "run_id": "rf3567-capability",
        "canonical_entity_id": "ent-1",
        "version_number": 1,
        "previous_run_id": None,
        "entity_name": "Capability Test Ltd",
        "jurisdiction": "GB",
        "risk_classification": "LOW",
        "generated_at": "2026-07-31T00:00:00+00:00",
    })

    assert captured, "a case was archived and the brain heard nothing"
    sig = captured[-1]
    assert sig["module"] == "dd_case_archive"
    assert "Capability Test Ltd" in (sig.get("entity_name", "") + sig.get("summary", ""))


def test_dd_case_archive_ignores_an_empty_entry_without_signalling(captured):
    """The guard clause returns before doing anything; it must not claim a write."""
    from aria_service.intel import dd_case_archive

    dd_case_archive.archive_entry({})
    assert not captured, f"an empty entry produced a success signal: {captured}"


@pytest.mark.asyncio
async def test_memory_wal_drain_signals_only_when_it_did_work(captured, tmp_path, monkeypatch):
    """The WAL is ARIA's §7 'never forget a fact' guarantee. An empty drain is
    the healthy steady state and runs on a timer — signalling it every cycle is
    the flood the loop_monitor precedent exists to avoid."""
    from aria_service.intel import memory_wal

    monkeypatch.setattr(memory_wal, "_WAL_PATH", tmp_path / "wal.jsonl", raising=False)

    async def _ok(**kwargs):
        return True

    empty = await memory_wal.drain(_ok)
    assert empty.get("pending") == 0
    assert not captured, f"an empty drain signalled success: {captured}"

    (tmp_path / "wal.jsonl").write_text(
        '{"topic": "t", "content": "c", "source": "s"}\n', encoding="utf-8"
    )
    result = await memory_wal.drain(_ok)
    assert result.get("retried") == 1, result
    assert captured, "a drain recovered a fact and the brain heard nothing"
    assert captured[-1]["module"] == "memory_wal"
