"""R-F1915 (G4 vaccine) — the wedge gene: sync-CPU-on-the-event-loop +
unbounded heavy-job concurrency must not regress.

The single-process brain freezes when (a) a GIL-bound encode runs sync on the
loop, or (b) too many 840s deep-DDs run at once. This guards both:
  A1: semantic_search_ep offloads the encode to a thread (was sync-on-loop).
  B:  _launch_deep_dd_bg enforces a global concurrency cap (was per-key dedup
      only — N distinct users/entities could each launch an 840s job at once).
"""
from __future__ import annotations

import pathlib

import aria_service.routes.aria as aria


def test_semantic_search_ep_offloads_encode_sourcepin():
    """semantic_search() runs a GIL-bound encode; the endpoint must not call it
    synchronously on the loop."""
    repo = pathlib.Path(__file__).resolve().parents[2]
    src = (repo / "aria_service" / "routes" / "aria.py").read_text(encoding="utf-8", errors="ignore")
    assert "await asyncio.to_thread(semantic_search, query, top_k)" in src, \
        "semantic_search_ep must offload the encode via asyncio.to_thread"
    # and the old sync form must be gone
    assert "return {\"results\": semantic_search(query, top_k)}" not in src


def test_deep_dd_global_cap_rejects_beyond_max(monkeypatch):
    """At the global cap, a NEW deep-bg DD is rejected (caller falls back to the
    shallow inline result) instead of piling another heavy job onto the brain."""
    monkeypatch.setattr(aria, "_DD_DEEP_BG_MAX", 2)
    # pre-fill the inflight set to the cap with unrelated keys
    saved = set(aria._DD_DEEP_BG_INFLIGHT)
    try:
        aria._DD_DEEP_BG_INFLIGHT.clear()
        aria._DD_DEEP_BG_INFLIGHT.update({("ent_a", "userX"), ("ent_b", "userY")})
        launched = aria._launch_deep_dd_bg(
            {"name": "BrandNewCo"}, llm=None, user_id="userZ", user_email=None)
        assert launched is False, "must reject when the global cap is reached"
        # the set must NOT have grown (no task created)
        assert len(aria._DD_DEEP_BG_INFLIGHT) == 2
    finally:
        aria._DD_DEEP_BG_INFLIGHT.clear()
        aria._DD_DEEP_BG_INFLIGHT.update(saved)


def test_deep_dd_dedup_still_rejects_same_key(monkeypatch):
    """The per-(entity,user) dedup must still hold (below the cap)."""
    monkeypatch.setattr(aria, "_DD_DEEP_BG_MAX", 10)
    saved = set(aria._DD_DEEP_BG_INFLIGHT)
    try:
        aria._DD_DEEP_BG_INFLIGHT.clear()
        # Canonical id of "DupCo" for userP — computed the same way the function
        # does, which is the whole point of the fixture.
        #
        # R-F3807 — this called `_canon("DupCo")` POSITIONALLY inside a bare
        # `except Exception: ent = "dupco"`. `canonical_entity_id` is KEYWORD-ONLY,
        # so the call raised TypeError, the except swallowed it, and the test keyed
        # on the lowercased raw string — reproducing precisely the defect R-F3648
        # fixed in production (aria.py:8745-8761), where the same swallowed TypeError
        # downgraded the de-dup key so two spellings of one entity could stack
        # concurrent 840s deep-DD jobs. The test therefore asserted against the OLD
        # key while production used the new one: no match, no de-dup, and the call
        # fell through to `asyncio.create_task` outside a loop.
        #
        # No fallback: if the key cannot be computed the fixture is invalid and must
        # fail loudly rather than quietly test the wrong thing.
        from aria_service.intel.dd_versioning import canonical_entity_id as _canon
        ent = _canon(entity_type="company", name="DupCo") or "dupco"
        aria._DD_DEEP_BG_INFLIGHT.add((ent, "userP"))
        launched = aria._launch_deep_dd_bg(
            {"name": "DupCo"}, llm=None, user_id="userP", user_email=None)
        assert launched is False, "duplicate (entity,user) must be de-duped"
    finally:
        aria._DD_DEEP_BG_INFLIGHT.clear()
        aria._DD_DEEP_BG_INFLIGHT.update(saved)
