"""R-F4135 (C-169) — the entity resolver's prior-facts lookup has never worked.
Not once, since R-F730 introduced it.

```python
hits = knowledge.search_knowledge(query, limit=limit)     # entity_resolver.py:103
```

`search_knowledge(query: str) -> str` takes **no** `limit`. Every call raises

    TypeError: search_knowledge() got an unexpected keyword argument 'limit'

into an `except Exception` whose only response is `logger.debug(...)`. So
`_fetch_prior_facts` returned `[]` unconditionally, and nothing anywhere said so.

Proven before it was believed — one matching fact in the store:

```
search_knowledge('rosoboronexport')            -> "[ARIA KNOWLEDGE BASE ...]" (works)
search_knowledge('rosoboronexport', limit=5)   -> TypeError
_fetch_prior_facts('rosoboronexport')          -> []
```

**What it cost, which is more than an empty list.** `prior_facts` is the largest
single component of the resolver's confidence score:

```python
if out["prior_facts"]:   score += 0.5
if out["prior_signals"]: score += 0.3
if out["aliases"]:       score += 0.2
```

So resolver confidence has been **structurally capped at 0.5** — any downstream
threshold above that was unreachable. And `render_context_block` never emitted a
"Prior facts" section, so the `[ENTITY HINTS]` block fed to BOTH chat paths
(`aria_engine.py:4227` complete and `:5147` stream, §13) has been missing the
verified facts ARIA already holds about the entity being asked about.

**Two failures, not one, and the second is why it survived:**

1. §3b — a call written against a signature nobody checked.
2. §21a — the failure was **DARK**. `logger.debug` is not a wire. A brain signal
   on the failure branch would have surfaced this on day one; instead a
   permanently-failing capability looked exactly like an entity with no history.

**The fix is `search_fact_records`, which is the function this code wanted all
along** — it accepts `limit` and returns records. But the records must be
normalised: the renderer reads `f["summary"]`, and raw fact dicts carry
`topic`/`content`. Restoring the data without the mapping would return facts and
still render nothing — a fix that looks green and changes nothing user-visible.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import entity_resolver as er
from aria_service.intel import knowledge as k


@pytest.fixture(autouse=True)
def _corpus(monkeypatch):
    facts = [
        {"id": "a", "topic": "Rosoboronexport",
         "content": "Rosoboronexport is the Russian state arms exporter, sanctioned by OFAC.",
         "accessCount": 0, "confidence": 0.9, "createdAt": "2026-01-01"},
        {"id": "b", "topic": "Rosoboronexport subsidiaries",
         "content": "Rosoboronexport holds stakes in several defence trading entities.",
         "accessCount": 0, "confidence": 0.8, "createdAt": "2026-01-02"},
        {"id": "z", "topic": "unrelated", "content": "gaskets and flanges",
         "accessCount": 0, "confidence": 0.5, "createdAt": "2026-01-03"},
    ]
    monkeypatch.setattr(k, "_cache", {"facts": facts}, raising=False)
    k._search_lc.clear()
    monkeypatch.setattr(k, "_search_lc_facts_id", 0, raising=False)
    yield


def test_prior_facts_are_actually_returned():
    """The headline. Before the fix this was [] for every entity that has ever
    existed, because the call raised TypeError into a debug log."""
    got = asyncio.run(er._fetch_prior_facts("Rosoboronexport"))
    assert got, "the resolver still sees no prior facts"
    assert len(got) == 2, got


def test_each_record_carries_a_summary_the_renderer_can_read():
    """The trap in the obvious fix. `render_context_block` reads
    `f.get("summary")`; raw fact dicts have `topic`/`content`. Swapping the
    function without normalising returns facts and still renders nothing."""
    got = asyncio.run(er._fetch_prior_facts("Rosoboronexport"))
    assert all((r.get("summary") or "").strip() for r in got), got
    assert any("arms exporter" in r["summary"] for r in got), got


def test_the_rendered_block_shows_them():
    """§3c — the user-visible outcome, not the helper. This block is what
    reaches the chat prompt."""
    resolved = asyncio.run(er.resolve("Rosoboronexport"))
    block = er.render_context_block(resolved)
    assert "Prior facts" in block, block
    assert "arms exporter" in block, block


def test_confidence_is_no_longer_capped_at_half():
    """prior_facts is worth 0.5 of the score. While the lookup was dead the
    resolver could never exceed 0.5, so any threshold above that was
    unreachable — a silent ceiling, not an error."""
    resolved = asyncio.run(er.resolve("Rosoboronexport"))
    assert resolved["prior_facts"], resolved
    assert resolved["confidence"] >= 0.5, resolved


def test_the_limit_is_honoured():
    """The argument whose presence caused the bug must now actually work."""
    got = asyncio.run(er._fetch_prior_facts("Rosoboronexport", limit=1))
    assert len(got) == 1, got


def test_an_entity_with_no_history_still_returns_empty():
    """The fix must not invent history. An empty result is correct here, and
    must stay distinguishable from the old permanent emptiness only by the fact
    that the matching case now works."""
    assert asyncio.run(er._fetch_prior_facts("Nonexistentcorp Zzqq")) == []


def test_a_failure_reaches_the_brain_instead_of_a_debug_log(monkeypatch):
    """§21a — the real reason this survived for so long.

    `logger.debug` is not a wire. A permanently-failing capability was
    indistinguishable from an entity with no history. Any future breakage of
    this path must announce itself."""
    seen: list[dict] = []
    import aria_service.intel.engine_wiring as ew
    monkeypatch.setattr(ew, "wire_failure", lambda **kw: seen.append(kw), raising=True)

    def boom(*a, **kw):
        raise RuntimeError("knowledge exploded")
    monkeypatch.setattr(k, "search_fact_records", boom, raising=True)

    assert asyncio.run(er._fetch_prior_facts("Rosoboronexport")) == []
    assert seen, "the failure was swallowed silently again"
    assert seen[0]["module"] == "entity_resolver", seen


def test_the_wire_cannot_break_the_lookup(monkeypatch):
    """Fail-open: an observability failure must not remove a working answer."""
    import aria_service.intel.engine_wiring as ew
    monkeypatch.setattr(
        ew, "wire_failure",
        lambda **kw: (_ for _ in ()).throw(RuntimeError("wire down")),
        raising=True)
    assert asyncio.run(er._fetch_prior_facts("Rosoboronexport"))


def test_the_signature_it_calls_actually_accepts_limit():
    """§3b, stated as a test. This is the check that was skipped."""
    import inspect
    sig = inspect.signature(k.search_fact_records)
    assert "limit" in sig.parameters, sig
    assert "limit" not in inspect.signature(k.search_knowledge).parameters, (
        "search_knowledge grew a limit= — re-read C-169 before changing the caller back")
