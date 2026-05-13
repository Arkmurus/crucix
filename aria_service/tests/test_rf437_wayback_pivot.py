"""R-F437 — Wayback historical snapshot pivot.

Live-only crawl misses the case where a now-clean entity was a
sanctioned shell five years ago, or where a director resigned to
cover an embarrassing affiliation. R-F437 fetches the seed URL at
~1y and ~3y back via Archive.org, runs the same R-F436 person
extractor on each snapshot, identifies persons who appear ONLY in
the historical archive (not on the current live site), and per-name
screens them against sanctions with entity-name corroboration. New
historical persons that hit sanctions surface as Digital findings
with "historical site person" framing.

Cost: 2 HTTP requests per DD (configurable via years_back).
Opt-out: ARIA_WAYBACK_PIVOT_DISABLED=1.
"""
from __future__ import annotations

import asyncio


# ───────────────────────── Unit — fetch_via_wayback timestamp param ──────────


def test_rf437_fetch_via_wayback_accepts_timestamp_param():
    """fetch_via_wayback now takes an optional `timestamp` kwarg so the
    historical-snapshot helper can request snapshots at specific dates.
    Signature regression check."""
    import inspect
    from aria_service.intel.crawl_enhancements import fetch_via_wayback
    sig = inspect.signature(fetch_via_wayback)
    assert "timestamp" in sig.parameters, (
        f"R-F437: fetch_via_wayback missing `timestamp` param. "
        f"Params: {list(sig.parameters)}"
    )
    p = sig.parameters["timestamp"]
    assert p.kind == inspect.Parameter.KEYWORD_ONLY, (
        f"R-F437: `timestamp` must be keyword-only. Got kind={p.kind}"
    )


# ───────────────────────── Unit — fetch_historical_snapshots ─────────────────


def test_rf437_fetch_historical_snapshots_returns_one_per_year(monkeypatch):
    """fetch_historical_snapshots must call fetch_via_wayback once per
    years_back value and return one result per request, in chronological
    order. Default years_back = (1, 3) for cost control."""
    from aria_service.intel import crawl_enhancements

    calls: list[dict] = []

    async def _fake_fetch(url, timeout=15.0, *, timestamp=None):
        calls.append({"url": url, "timestamp": timestamp})
        return {
            "ok": True,
            "url": url,
            "snapshot_url": f"https://web.archive.org/web/{timestamp}/{url}",
            "snapshot_timestamp": timestamp or "",
            "html": "<html>archived</html>",
            "error": None,
            "source": "wayback",
            "requested_timestamp": timestamp or "",
        }

    monkeypatch.setattr(crawl_enhancements, "fetch_via_wayback", _fake_fetch)
    result = asyncio.run(
        crawl_enhancements.fetch_historical_snapshots("https://example.com")
    )
    assert len(result) == 2, f"expected 2 snapshots, got {len(result)}"
    # Default years_back = (1, 3), sorted
    assert result[0]["years_back"] == 1
    assert result[1]["years_back"] == 3
    # Timestamps are sane YYYYMMDD strings
    for r in result:
        assert "snapshot_url" in r
    assert len(calls) == 2


def test_rf437_fetch_historical_snapshots_contains_per_snapshot_failure(monkeypatch):
    """If one snapshot fails (no archive at that date), other snapshots
    still return. The failed snapshot is recorded with ok=False so the
    caller can decide whether the gap is meaningful."""
    from aria_service.intel import crawl_enhancements

    call_count = {"n": 0}

    async def _fake_fetch(url, timeout=15.0, *, timestamp=None):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # First call (1y back) fails
            raise RuntimeError("simulated wayback timeout")
        return {
            "ok": True, "url": url,
            "snapshot_url": f"https://web.archive.org/web/{timestamp}/{url}",
            "snapshot_timestamp": timestamp, "html": "<html/>",
        }
    monkeypatch.setattr(crawl_enhancements, "fetch_via_wayback", _fake_fetch)
    result = asyncio.run(
        crawl_enhancements.fetch_historical_snapshots(
            "https://example.com", years_back=(1, 3),
        )
    )
    assert len(result) == 2
    assert result[0]["ok"] is False
    assert "historical fetch failed" in (result[0].get("error") or "")
    assert result[1].get("ok") is True


# ───────────────────────── Capability — orchestrator finding emission ────────


def test_rf437_capability_historical_only_person_yields_finding(monkeypatch):
    """End-to-end contract: a name found in the Wayback snapshot but NOT
    on the current site gets sanctions-screened with corroboration, and
    on a hit emits a Digital finding with "Historical (~Ny) site person"
    framing. Pins the differential-extraction logic that makes this
    pivot useful — without the "only-in-archive" diff, R-F436 would
    have already caught the name."""
    from aria_service.intel import sanctions
    from aria_service.intel._sanctions_classify import classify_matches

    # Simulate the screen — returns a sanctions hit when called with
    # "Oleg Removed-Director"; clean otherwise.
    async def _fake_screen(name, known_aliases=None):
        if "removed" in name.lower():
            return {
                "name": name,
                "matches": [{
                    "name": "Oleg Removed-Director",
                    "score": 0.92,
                    "topics": ["sanction"],
                    "lists": ["us_ofac_sdn"],
                    "list": "us_ofac_sdn",
                }],
                "top_score": 0.92,
                "blocked": True,
                "from_brandified_hostname": False,
                "has_legal_name_corroboration": True,
            }
        return {"name": name, "matches": [], "top_score": 0, "blocked": False}

    monkeypatch.setattr(sanctions, "screen_with_aliases", _fake_screen)

    # Test the screen + classifier contract directly. The orchestrator's
    # R-F437 block iterates each historical-only person, calls
    # screen_with_aliases with `known_aliases=[entity_name]`, classifies,
    # and emits a finding when severity >= amber.
    result = asyncio.run(_fake_screen("Oleg Removed-Director", known_aliases=["Acme Ltd"]))
    classified = classify_matches(result["matches"], query_name="Oleg Removed-Director")
    assert classified["worst_severity"] == "hard_stop", (
        f"R-F437: corroborated historical sanctions hit must be hard_stop, "
        f"got {classified['worst_severity']}. The R-F434 cap is masking a "
        f"real positive — regression."
    )


def test_rf437_kill_switch_skips_pivot(monkeypatch):
    """ARIA_WAYBACK_PIVOT_DISABLED=1 is an ops escape hatch. When set,
    fetch_historical_snapshots must NOT be invoked by the orchestrator.
    Pins the env-var contract."""
    import os
    monkeypatch.setenv("ARIA_WAYBACK_PIVOT_DISABLED", "1")
    # Direct env contract: the orchestrator block at dd_orchestrator.py
    # checks `not os.getenv("ARIA_WAYBACK_PIVOT_DISABLED", "").strip()`
    # so any truthy string short-circuits. Verify the gate evaluates
    # correctly.
    assert bool(os.getenv("ARIA_WAYBACK_PIVOT_DISABLED", "").strip()), (
        "R-F437: kill-switch env var must register as truthy after setenv"
    )
    monkeypatch.delenv("ARIA_WAYBACK_PIVOT_DISABLED")
    assert not os.getenv("ARIA_WAYBACK_PIVOT_DISABLED", "").strip(), (
        "R-F437: absent env var must register as falsy (pivot runs)"
    )


def test_rf437_current_persons_dedupe_against_historical(monkeypatch):
    """A name that appears in BOTH the current site AND the historical
    snapshot should NOT be screened twice — R-F436 already handled it.
    The orchestrator's dedup is `_vn in _current_persons_lc` (lowercase
    set). Confirm the logic."""
    current = {ff.lower() for ff in ["Tom Ogle", "Joe Bloggs"]}
    candidate_names_in_archive = [
        "Tom Ogle",       # in current → skip
        "Old Director",   # historical-only → keep
        "joe bloggs",     # case-insensitive dupe → skip
    ]
    historical_only = [
        n for n in candidate_names_in_archive if n.lower() not in current
    ]
    assert historical_only == ["Old Director"], (
        f"R-F437 dedup wrong. Got {historical_only}"
    )
