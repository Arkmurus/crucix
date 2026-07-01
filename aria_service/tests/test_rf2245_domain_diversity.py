"""R-F2245 — anti-collusion: search per-domain diversity cap + vault same-domain guard.

Operator directive: intel must not be dominated by repetitive same-domain data
(echo-chamber / collusion). Search caps results per domain before the top-N slice;
the vault add-flow caps sources per domain per user.
"""
from __future__ import annotations
from types import SimpleNamespace
from pathlib import Path
from aria_service.intel.web_search import _apply_domain_diversity_cap


def _r(url): return SimpleNamespace(url=url)


def test_cap_breaks_a_single_domain_monopoly(monkeypatch):
    monkeypatch.setenv("ARIA_SEARCH_MAX_PER_DOMAIN", "3")
    # 6 janes.com ranked first, then 4 diverse domains
    results = [_r(f"https://www.janes.com/{i}") for i in range(6)] + [_r(f"https://s{i}.com/x") for i in range(4)]
    out = _apply_domain_diversity_cap(results, 5)
    assert len(out) == 5
    assert sum(1 for r in out if "janes.com" in r.url) <= 3, "one domain must not own the top-N"


def test_never_returns_fewer_than_plain_slice(monkeypatch):
    # all from one domain + a tight cap: overflow must backfill to fill max_results
    monkeypatch.setenv("ARIA_SEARCH_MAX_PER_DOMAIN", "1")
    results = [_r(f"https://one.com/{i}") for i in range(10)]
    assert len(_apply_domain_diversity_cap(results, 5)) == 5


def test_cap_zero_disables(monkeypatch):
    monkeypatch.setenv("ARIA_SEARCH_MAX_PER_DOMAIN", "0")
    results = [_r(f"https://one.com/{i}") for i in range(10)]
    assert len(_apply_domain_diversity_cap(results, 5)) == 5


def test_short_list_is_noop(monkeypatch):
    monkeypatch.setenv("ARIA_SEARCH_MAX_PER_DOMAIN", "3")
    results = [_r("https://a.com/1"), _r("https://a.com/2")]
    assert len(_apply_domain_diversity_cap(results, 5)) == 2


def test_vault_add_has_domain_guard():
    # source-contract: the vault add endpoint enforces a per-domain cap
    src = (Path(__file__).resolve().parent.parent / "routes" / "aria.py").read_text(encoding="utf-8")
    assert "ARIA_VAULT_MAX_PER_DOMAIN" in src
    assert "keep your intelligence diverse" in src  # the user-facing anti-collusion message
