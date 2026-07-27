"""R-F1498/R-F3298 capability test — the portal digest REPORTS, it does not register.

ARIA emails the operator the honest state of each external portal it cannot use
autonomously: what is blocked, why, and what only a human can do. send_email is
mocked, so no real email is sent.

R-F3298 — WHY THIS FILE WAS REWRITTEN. Two independent problems met here.

1. The digest drove live registrations. `email_portal_requirements_to_operator`
   called `determine_and_drive_all()` with driving ON, and 17 of the 24 vault
   portals reach step 6 of `determine_and_drive`, which calls
   `register_for_portal` against real government portals. So *rendering a report*
   performed a registration sweep, and `autonomous_scheduler._portal_vault_redrive`
   drives the same portals in the statement immediately before it calls the
   digest, meaning the sweep ran TWICE per scheduler tick.

   For this test it meant a unit test about EMAIL COMPOSITION was driving live
   portal registration, which is the R-F2812 class (un-mocked network from
   pytest) and is why the file was slow enough to block the suite. How slow
   depends on how fast the network refuses you: measured at 60s+ on one machine
   and 1.5s on another. Stubbing it in the test alone would have hidden the
   production double-sweep, so the fix is in the code and this test now PROVES
   the absence of the side effect rather than mocking it away.

2. The old assertions had been stale since R-F1502 and could never pass. They
   required `paid >= 1`, `captcha >= 1`, `candidates >= 1` and a "CANDIDATE
   SOURCES" section. But R-F1502 deliberately suppresses declined and deferred
   portals from the recurring digest (its own docstring: "don't nag about a
   'no'"), and it does so structurally: `determine_and_drive_all(portal_ids=None)`
   lists only vault status pending/needs_operator, so the 5 declined and 1
   deferred portals are never in the result set and `paid`/`deferred` are always
   0. R-F1502 also dropped R-F1501's `candidates` count and CANDIDATE SOURCES
   section entirely, so `c["candidates"]` was a KeyError waiting to happen.

   These are asserted against the CURRENT deliberate contract rather than the
   removed one. What is kept is R-F1501's actual invariant, which still matters:
   never tell the operator to set a key no code reads.
"""
import os
from unittest.mock import patch

import pytest

from aria_service.intel import portal_registry as pr  # noqa: E402


# R-F2801 — this module used to do `os.environ.setdefault("ARIA_OPERATOR_EMAIL",
# "op@imaria.io")` AT IMPORT TIME. That is a process-global mutation no
# monkeypatch ever undoes, so it leaked into every later test in the run. It
# silently broke test_memory_replication::test_stats_reports_live_when_smtp_configured,
# whose `email_destination` resolves ARIA_OPERATOR_EMAIL ahead of ARIA_SMTP_USER
# (memory_replication.py:632-637) — that test passed alone and failed in-suite,
# which is the signature of exactly this anti-pattern.
#
# Scoped to the tests that need it instead. autouse keeps them working unchanged
# while monkeypatch guarantees teardown.
@pytest.fixture(autouse=True)
def _operator_email(monkeypatch):
    monkeypatch.setenv("ARIA_OPERATOR_EMAIL", "op@imaria.io")


@pytest.mark.asyncio
async def test_digest_never_registers_on_a_live_portal(monkeypatch):
    """R-F3298 THE CAPABILITY TEST. Composing the digest must attempt no registration.

    This is the whole defect: a report with a write side effect. It fails loudly
    against the pre-fix code, where this counter reached 17.
    """
    attempted: list[str] = []

    async def _must_not_run(portal_id, *a, **kw):
        attempted.append(portal_id)
        return {"success": False, "error": "test"}

    monkeypatch.setattr(pr, "register_for_portal", _must_not_run)

    with patch("aria_service.integrations.email_outbound.send_email",
               lambda **kw: {"sent": True}):
        await pr.email_portal_requirements_to_operator()

    assert attempted == [], (
        f"the digest registered on {len(attempted)} live portals: {attempted[:5]}. "
        "Rendering a report must never drive registration."
    )


@pytest.mark.asyncio
async def test_digest_writes_no_vault_status(monkeypatch):
    """A determination-only pass must not mutate the vault either.

    `determine_and_drive_all` writes vault status and files operator actions per
    portal. That is correct for a driving pass and wrong for a report.
    """
    writes: list[tuple] = []
    from aria_service.intel.agent_signup_vault import get_vault
    vault = get_vault()
    monkeypatch.setattr(vault, "update_status",
                        lambda *a, **kw: writes.append(a[:2]))

    with patch("aria_service.integrations.email_outbound.send_email",
               lambda **kw: {"sent": True}):
        await pr.email_portal_requirements_to_operator()

    assert writes == [], f"the digest mutated vault status {len(writes)} times: {writes[:5]}"


@pytest.mark.asyncio
async def test_determine_only_still_classifies_the_local_blockers():
    """drive=False must still do real work, not return empty.

    Steps 1-5 of determine_and_drive are local (vault reads plus two id sets) and
    must keep classifying. If this returned nothing, the digest would be honest
    but useless, and `not_attempted` would be hiding a broken determination.
    """
    results = await pr.determine_and_drive_all(drive=False)
    assert results, "determination must still produce results"

    statuses = {r.get("status") for r in results}
    assert "needs_operator" in statuses, (
        "locally-determinable blockers (captcha, declined, deferred) must still "
        "be classified without touching the network"
    )
    # Portals that would need a live attempt are reported as not attempted rather
    # than guessed at. Not claiming an outcome we did not observe is the point.
    assert "not_attempted" in statuses


@pytest.mark.asyncio
async def test_requirements_email_composes_an_honest_body():
    captured = {}

    def fake_send(**kw):
        captured.update(kw)
        return {"sent": True}

    with patch("aria_service.integrations.email_outbound.send_email", fake_send):
        res = await pr.email_portal_requirements_to_operator()

    assert res["to"], "must resolve an operator recipient"
    c = res["counts"]

    # CAPTCHA portals are determined locally, so they survive a no-drive pass and
    # are the reason the digest still has something worth sending.
    assert c["captcha"] >= 1, "CAPTCHA portals must be flagged"

    # R-F1501: no data-fetcher reads a portal key, so nothing is activatable by a
    # key alone. Under R-F3298 nothing is auto-attempted during a report either,
    # so there is nothing the operator must chase.
    assert c["actionable"] == 0, "no portal is wired yet — actionable must be 0"

    body = captured["body"]
    assert "CAPTCHA" in body

    # R-F1501's real invariant: never instruct the operator to set a key that no
    # code reads. R-F3199 REMOVED the 2captcha solver (portal_registry raises an
    # unconditional ImportError where it used to load), yet the captcha message
    # still said "set ARIA_TWOCAPTCHA_API_KEY" — an instruction to configure a
    # deleted integration. That is the exact class R-F1501 existed to prevent.
    for dead_key in ("ARIA_TWOCAPTCHA_API_KEY", "set NEWSAPI", "set GNEWS"):
        assert dead_key not in body, (
            f"digest tells the operator to set {dead_key}, which no code reads"
        )


@pytest.mark.asyncio
async def test_operator_copy_carries_no_ai_dashes():
    """Operator-facing copy must not contain em or en dashes (house style).

    The digest is a real outbound email, so it is product copy.
    """
    captured = {}

    with patch("aria_service.integrations.email_outbound.send_email",
               lambda **kw: (captured.update(kw), {"sent": True})[1]):
        await pr.email_portal_requirements_to_operator()

    for field in ("body", "subject"):
        text = captured.get(field, "")
        assert "—" not in text and "–" not in text, (
            f"em/en dash in operator-facing {field}: "
            f"{[ln for ln in text.splitlines() if '—' in ln or '–' in ln][:3]}"
        )


@pytest.mark.asyncio
async def test_no_operator_email_returns_unsent_not_crash():
    with patch.dict(os.environ, {"ARIA_OPERATOR_EMAIL": "", "ARIA_EMAIL_OPERATOR_ALLOWLIST": "",
                                 "ARIA_SMTP_USER": ""}, clear=False):
        res = await pr.email_portal_requirements_to_operator()
    assert res["sent"] is False
    assert "no operator email" in res.get("error", "")
