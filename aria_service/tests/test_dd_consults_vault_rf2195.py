"""Capability test for R-F2195 — DD consults operator-curated vault sources (Pipeline 3).

Drives the real new path dd_orchestrator._consult_vault_sources: a manually-added vault
website that mentions the entity is cited as an INFO finding on the DD report, so manual
sources actually inform due diligence. Bulletproof: best-effort, bounded, never raises.

Run: python -m pytest aria_service/tests/test_dd_consults_vault_rf2195.py -q
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock, MagicMock

from aria_service.intel import dd_orchestrator as dd


def _report():
    return SimpleNamespace(identity=SimpleNamespace(findings=[], data_gaps=[]))


def _run(entries, text, ok=True):
    async def _r():
        fake_vault = MagicMock()
        fake_vault.list.return_value = entries
        with patch("aria_service.intel.agent_signup_vault.get_vault", return_value=fake_vault), \
             patch("aria_service.intel.researcher.extract_url_text",
                   AsyncMock(return_value={"extraction_ok": ok, "text": text})):
            rep = _report()
            n = await dd._consult_vault_sources("Acme Corp", "GB", rep)
            return n, rep
    return asyncio.run(_r())


def test_curated_source_mentioning_entity_is_cited():
    entries = [{"site_id": "acme", "site_name": "Acme Watch",
                "site_url": "https://acme.example.com", "site_type": "website", "status": "verified"}]
    n, rep = _run(entries, "Industry report: Acme Corp signed a new defense contract in London.")
    assert n == 1
    assert len(rep.identity.findings) == 1
    f = rep.identity.findings[0]
    assert f.source.startswith("vault:")
    assert "Acme Corp" in f.detail
    assert f.severity == "info"          # additive only — never affects hard-stop


def test_curated_source_not_mentioning_entity_skipped():
    entries = [{"site_id": "x", "site_name": "X News",
                "site_url": "https://x.example.com", "site_type": "website", "status": "verified"}]
    n, rep = _run(entries, "Completely unrelated content about agriculture.")
    assert n == 0
    assert rep.identity.findings == []


def test_terminal_status_and_portals_ignored():
    entries = [
        {"site_id": "dead", "site_url": "https://d.example.com", "site_type": "website", "status": "failed"},
        {"site_id": "p", "site_url": "https://p.example.com", "site_type": "portal", "status": "verified"},
    ]
    n, rep = _run(entries, "Acme Corp everywhere")
    assert n == 0


def test_empty_vault_no_crash():
    n, rep = _run([], "anything")
    assert n == 0


if __name__ == "__main__":
    test_curated_source_mentioning_entity_is_cited()
    test_curated_source_not_mentioning_entity_skipped()
    test_terminal_status_and_portals_ignored()
    test_empty_vault_no_crash()
    print("ALL PASS")
