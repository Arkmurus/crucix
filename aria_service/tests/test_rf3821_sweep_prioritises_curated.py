"""R-F3821 — the 6h sweep must serve the CURATED seeds first and ration discovery.

THE DEFECT, measured live 2026-08-09. `crawl_loop` calls `crawl_seed_homepages()`
with no limit, so every enabled domain is fetched every cycle, ordered
`tier ASC, domain ASC`. Of 22,115 registry rows, **21,953 are tier-4
`sector='discovered'` (99.3%)** — unvetted rows admitted before R-F3820's ingress gate
existed. The 147 curated tier-1..3 seeds (OFAC, BIS, EU Commission, defence media)
were queued behind, and competing with, twenty thousand speculative ones, and the fly
logs showed the alphabetical march that produced: `investors.xpinc → investors.yeti →
invoicefly.com → involuzione.it`.

WHAT THIS DELIBERATELY DOES NOT DO, and why — because the obvious fix is worse.
"Judge each crawled page and disable the off-mission ones" was tested against ARIA's
OWN live index first, and **13 of 14 curated tier-1 sources came back off_topic**:

    ofac.treasury.gov   "Home | Office of Foreign Assets Control"
    bis.doc.gov         "Homepage | Bureau of Industry and Security"
    state.gov           "Technical Difficulties"
    ec.europa.eu        "Language selection | European Commission"

Content-based auto-disable would have switched off OFAC. A homepage title is
NAVIGATIONAL, not topical — which is exactly why R-F3820 judges a SERP title+snippet
(descriptive of a page) and never a bare domain or homepage. So no domain is disabled
here; only the ORDER and the per-cycle BUDGET change, and nothing is destroyed.

Fairness matters as much as the budget: rationing without rotation would starve the
tail forever, since `ORDER BY tier, domain` is stable and `domains[:limit]` would
re-crawl the same alphabetical prefix every cycle. Discovery is therefore taken
OLDEST-CRAWLED-FIRST, so every row is reached eventually.
"""
from __future__ import annotations

import pytest

from aria_service.crawler import runner


def _dom(domain, tier, last=None):
    return {"domain": domain, "tier": tier, "sector": "curated" if tier <= 3 else "discovered",
            "region": "", "language": "", "rate_limit_per_sec": 0.5,
            "last_crawled_at": last, "enabled": True}


def test_curated_seeds_always_come_first():
    """A tier-1 sanctions source must never queue behind speculative discovery."""
    domains = [_dom(f"disc{i}.example", 4, last=i) for i in range(50)]
    domains += [_dom("ofac.treasury.gov", 1), _dom("bis.doc.gov", 1), _dom("janes.com", 2)]

    ordered = runner._prioritise_sweep(domains, discovery_budget=10)
    head = [d["domain"] for d in ordered[:3]]
    assert set(head) == {"ofac.treasury.gov", "bis.doc.gov", "janes.com"}, (
        f"curated seeds must lead the sweep, got {head}")


def test_discovery_is_rationed_per_cycle():
    domains = [_dom(f"d{i}.example", 4, last=i) for i in range(5000)]
    domains += [_dom("ofac.treasury.gov", 1)]

    ordered = runner._prioritise_sweep(domains, discovery_budget=100)
    disc = [d for d in ordered if d["tier"] >= 4]
    assert len(disc) == 100, f"discovery must be capped at the budget, got {len(disc)}"
    assert len(ordered) == 101, "every curated seed must survive the ration"


def test_every_curated_seed_survives_however_small_the_budget():
    """The ration applies to DISCOVERY only. Curated is the product's backbone and is
    never dropped — a budget of zero must still sweep all of it."""
    domains = [_dom(f"c{i}.example", 1) for i in range(147)]
    domains += [_dom(f"d{i}.example", 4, last=i) for i in range(1000)]

    ordered = runner._prioritise_sweep(domains, discovery_budget=0)
    assert len([d for d in ordered if d["tier"] <= 3]) == 147
    assert [d for d in ordered if d["tier"] >= 4] == []


def test_discovery_rotates_oldest_first_so_nothing_starves():
    """THE FAIRNESS HALF. `ORDER BY tier, domain` is stable, so a naive cap would
    re-crawl the same alphabetical prefix forever and the tail would never be
    visited. Oldest-crawled-first guarantees every row is reached."""
    domains = [
        _dom("recent.example", 4, last=9_000),
        _dom("older.example", 4, last=5_000),
        _dom("never.example", 4, last=None),      # never crawled — most owed a turn
    ]
    ordered = [d["domain"] for d in runner._prioritise_sweep(domains, discovery_budget=2)]
    assert ordered == ["never.example", "older.example"], (
        f"expected never-crawled first then oldest, got {ordered}")


def test_a_never_crawled_domain_outranks_a_recently_crawled_one():
    domains = [_dom("fresh.example", 4, last=1_000_000)] + \
              [_dom("virgin.example", 4, last=None)]
    ordered = [d["domain"] for d in runner._prioritise_sweep(domains, discovery_budget=1)]
    assert ordered == ["virgin.example"]


def test_the_budget_is_operator_tunable():
    """The right number is an operational question, so it must be settable without a
    deploy — but it must have a sane default, since an unset env is the normal case."""
    assert isinstance(runner._discovery_budget(), int)
    assert runner._discovery_budget() > 0


def test_budget_env_override(monkeypatch):
    monkeypatch.setenv("ARIA_CRAWL_DISCOVERY_PER_CYCLE", "42")
    assert runner._discovery_budget() == 42


def test_a_broken_budget_env_falls_back_rather_than_crashing_the_sweep(monkeypatch):
    monkeypatch.setenv("ARIA_CRAWL_DISCOVERY_PER_CYCLE", "not-a-number")
    assert runner._discovery_budget() > 0, "a typo in an env var must not stop crawling"


def test_nothing_is_disabled_or_deleted():
    """§7 and the OFAC evidence above: this changes ORDER and VOLUME only. Every
    input domain must still be present in the union of swept + deferred."""
    domains = [_dom("ofac.treasury.gov", 1)] + \
              [_dom(f"d{i}.example", 4, last=i) for i in range(20)]
    ordered = runner._prioritise_sweep(domains, discovery_budget=5)
    assert all(d["enabled"] for d in ordered), "the sweep must not disable anything"
    assert len(ordered) == 6      # 1 curated + 5 rationed; the other 15 wait their turn
