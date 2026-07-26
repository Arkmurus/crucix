"""R-F2386 — vault/source add buttons write truthful, non-duplicate sources."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from aria_service.routes import aria as routes


class _Req:
    def __init__(self, body: dict[str, Any]):
        self._body = body

    async def json(self) -> dict[str, Any]:
        return self._body


class _Vault:
    def __init__(self, rows: list[dict[str, Any]] | None = None):
        self.rows = list(rows or [])
        self.record_calls: list[dict[str, Any]] = []

    def get(self, site_id: str) -> dict[str, Any] | None:
        for row in self.rows:
            if row["site_id"] == site_id:
                return row
        return None

    def list(self, **_filters: Any) -> list[dict[str, Any]]:
        return list(self.rows)

    def record(self, **kwargs: Any) -> dict[str, Any]:
        self.record_calls.append(kwargs)
        row = {
            "site_id": kwargs["site_id"],
            "site_name": kwargs["site_name"],
            "site_url": kwargs["site_url"],
            "site_type": kwargs["site_type"],
            "agent_id": kwargs["agent_id"],
            "status": kwargs["status"],
        }
        self.rows.append(row)
        return row


def _body(url: str = "https://new.example.com/feed.xml", site_type: str = "rss") -> dict[str, Any]:
    return {
        "site_id": "new_example_com",
        "site_name": "New Example",
        "site_url": url,
        "site_type": site_type,
        "agent_id": "admin_manual",
        "status": "verified",
        "metadata": {
            "topics": ["sanctions", "defence"],
            "research_summary": (
                "Independent review established the publisher, update cadence, "
                "primary-source basis, access model, limitations, and provenance."
            ),
            "relevance_rationale": (
                "This fills a documented intelligence gap not covered by existing sources."
            ),
            "evidence_urls": [
                "https://evidence-one.example.org/methodology",
                "https://evidence-two.example.net/review",
            ],
        },
    }


def test_vault_record_rejects_builtin_source_monitor_duplicate(monkeypatch):
    """Add intel source must not duplicate ARIA's built-in source monitor feeds."""
    vault = _Vault()
    monkeypatch.setattr("aria_service.intel.agent_signup_vault.get_vault", lambda: vault)
    monkeypatch.setattr("aria_service.intel.security.validate_url", lambda _url: (True, "OK"))
    monkeypatch.setattr(
        "aria_service.intel.news_monitor.NEWS_SOURCES",
        [("Built In", "https://feeds.example.com/rss", "defence_global", "en", "tier_1b", ["defence"])],
    )

    out = asyncio.run(routes.vault_record_ep(_Req(_body(url="https://feeds.example.com/rss/"))))

    assert out["success"] is False
    assert out["duplicate"] is True
    assert out["duplicate_scope"] == "aria_source_monitor"
    assert "Built In" in out["error"]
    assert vault.record_calls == []


def test_vault_record_rejects_source_monitor_variant(monkeypatch):
    """Scheme, trailing slash, tracking and query order cannot bypass dedup."""
    vault = _Vault()
    monkeypatch.setattr("aria_service.intel.agent_signup_vault.get_vault", lambda: vault)
    monkeypatch.setattr("aria_service.intel.security.validate_url", lambda _url: (True, "OK"))
    monkeypatch.setattr(
        "aria_service.intel.news_monitor.NEWS_SOURCES",
        [("Built In", "http://feeds.example.com/rss?a=1&b=2", "defence_global", "en", "tier_1b", ["defence"])],
    )
    body = _body(url="https://feeds.example.com/rss/?utm_source=x&b=2&a=1")

    out = asyncio.run(routes.vault_record_ep(_Req(body)))

    assert out["success"] is False
    assert out["duplicate_scope"] == "aria_source_monitor"
    assert vault.record_calls == []


def test_vault_record_rejects_unresearched_manual_source(monkeypatch):
    """The real POST path blocks a relevant-looking but unresearched data point."""
    vault = _Vault()
    monkeypatch.setattr("aria_service.intel.agent_signup_vault.get_vault", lambda: vault)
    monkeypatch.setattr("aria_service.intel.security.validate_url", lambda _url: (True, "OK"))
    body = _body()
    body["metadata"] = {"topics": ["defence"]}

    out = asyncio.run(routes.vault_record_ep(_Req(body)))

    assert out["success"] is False
    assert out["admission_gate"] == "research"
    assert "research gate" in out["error"]
    assert vault.record_calls == []


def test_vault_record_returns_existing_vault_duplicate_without_second_write(monkeypatch):
    """Duplicate vault URLs should be idempotent, not a second ingesting row."""
    existing = {
        "site_id": "existing",
        "site_name": "Existing Feed",
        "site_url": "https://vault.example.com/feed",
        "site_type": "rss",
        "agent_id": "admin_manual",
        "status": "verified",
    }
    vault = _Vault([existing])
    monkeypatch.setattr("aria_service.intel.agent_signup_vault.get_vault", lambda: vault)
    monkeypatch.setattr("aria_service.intel.security.validate_url", lambda _url: (True, "OK"))
    monkeypatch.setattr("aria_service.intel.news_monitor.NEWS_SOURCES", [])

    out = asyncio.run(routes.vault_record_ep(_Req(_body(url="https://vault.example.com/feed/"))))

    assert out["success"] is True
    assert out["duplicate"] is True
    assert out["duplicate_scope"] == "vault"
    assert out["entry"]["site_id"] == "existing"
    assert out["ingestion_enabled"] is True
    assert out["output"] == "news_monitor.vault_curated"
    assert vault.record_calls == []


def test_vault_record_reports_ingestion_contract_for_new_feed(monkeypatch):
    """A unique Website/RSS source tells the UI the correct data output path."""
    vault = _Vault()
    monkeypatch.setattr("aria_service.intel.agent_signup_vault.get_vault", lambda: vault)
    monkeypatch.setattr("aria_service.intel.security.validate_url", lambda _url: (True, "OK"))
    monkeypatch.setattr("aria_service.intel.news_monitor.NEWS_SOURCES", [])

    out = asyncio.run(routes.vault_record_ep(_Req(_body())))

    assert out["success"] is True
    assert out["ingestion_enabled"] is True
    assert out["output"] == "news_monitor.vault_curated"
    assert vault.record_calls[0]["site_type"] == "rss"
    assert vault.record_calls[0]["status"] == "pending"


def test_vault_record_reports_catalogue_contract_for_portal(monkeypatch):
    """Portal/API additions are catalogue rows, not live News Monitor feeds."""
    vault = _Vault()
    monkeypatch.setattr("aria_service.intel.agent_signup_vault.get_vault", lambda: vault)
    monkeypatch.setattr("aria_service.intel.security.validate_url", lambda _url: (True, "OK"))
    monkeypatch.setattr("aria_service.intel.news_monitor.NEWS_SOURCES", [])

    body = _body(url="https://portal.example.com", site_type="portal")
    body["status"] = "needs_operator"
    out = asyncio.run(routes.vault_record_ep(_Req(body)))

    assert out["success"] is True
    assert out["ingestion_enabled"] is False
    assert out["output"] == "vault.catalogue"
    assert vault.record_calls[0]["status"] == "needs_operator"


def test_vault_and_sources_pages_keep_buttons_wired_to_vault_api():
    """Static contract for the two user-visible buttons under review."""
    vault_html = Path("public/vault.html").read_text(encoding="utf-8")
    sources_html = Path("public/sources.html").read_text(encoding="utf-8")

    assert 'id="btn-clear-vault"' in vault_html
    assert "authed('/api/aria/vault', { method: 'DELETE' })" in vault_html
    assert 'id="btn-add-site"' in vault_html
    assert "API.post('/api/aria/vault', body)" in vault_html
    assert "defaultStatusForType(site_type)" in vault_html
    assert "Source rejected by research gate" not in vault_html
    assert 'id="as-research-summary"' in vault_html
    assert 'id="as-relevance"' in vault_html
    assert 'id="as-evidence"' in vault_html

    assert 'id="btn-add-intel-source"' in sources_html
    assert "API.post('/api/aria/vault', body)" in sources_html
    assert "Source added to vault-curated News Monitor" in sources_html
    assert "Add &amp; start ingesting" not in sources_html
    assert 'id="add-src-research"' in sources_html
    assert 'id="add-src-relevance"' in sources_html
    assert 'id="add-src-evidence"' in sources_html
