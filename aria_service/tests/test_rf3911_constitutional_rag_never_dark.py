"""R-F3911 — §20's binding constitutional priming returned NOTHING, for the third time.

CLAUDE.md §20 makes this a MANDATORY pre-code step:

    query_constitutional_constraints('modifying <module> <task>', top_k=5)

It has now failed silently three separate ways, in the same function:

  * R-F2623 — the documented snippet wrapped a SYNC function in `asyncio.run()`, so
    it raised `TypeError` every time and "this binding step silently never ran".
  * R-F3099 — the `coding_constitutional` collection existed but was never
    populated from the CLI, so the step returned `[]` on every session. Its own
    docstring calls that "the R-F2623 failure class exactly: a mandatory step
    certified by an absence".
  * THIS ONE — when chromadb itself is unavailable, `_ensure()` is False and the
    step returned `[]`. Indistinguishable, to the caller, from "no rule applies".

WHY INSTALLING CHROMADB IS NOT THE FIX, and this is the point. On win32/ARM64 no
chromadb wheel exists (§16 lists it among five import-guarded packages with no
win-arm64 wheel), so the DECLARED dev environment cannot have it. Installing it
would make one workstation's query pass while CI, production and every other
developer stayed exactly as dark — the band-aid §1 forbids, applied to the very
mechanism that exists to remind us not to.

THE RULES WERE NEVER THE MISSING PIECE. `CONSTITUTIONAL_RULES` is a plain list of 31
dicts, already in the process, carrying the full text of every clause. Only the
RANKING needed a vector store. So an unavailable store now degrades to a lexical
match over the real rules, LABELLED `retrieval_mode: "lexical"`, instead of
returning nothing.

A crude ranking that delivers the constraints beats a sophisticated one that
delivers silence.
"""
from __future__ import annotations

import pytest

from aria_service.intel import coding_rag_indexer as cri
from aria_service.intel.constitutional_rules import CONSTITUTIONAL_RULES


# ── the degraded path: what this platform actually runs ─────────────────────────

def test_the_binding_step_returns_rules_with_no_vector_store(monkeypatch):
    """THE CAPABILITY. With the store unavailable — this box's real condition — the
    §20 step must still hand back constitutional constraints."""
    monkeypatch.setattr(cri, "_ensure", lambda: False)

    out = cri.query_constitutional_constraints("modifying web_search backend", top_k=5)

    assert out, "the binding §20 step returned NOTHING with no vector store (R-F3911)"
    assert len(out) == 5
    assert all(r.get("rule") for r in out), "every result must carry rule text"


def test_the_degraded_path_is_labelled_as_degraded(monkeypatch):
    """A degraded answer that looks identical to a semantic one is how a session
    concludes it was properly primed when it was not."""
    monkeypatch.setattr(cri, "_ensure", lambda: False)

    out = cri.query_constitutional_constraints("circuit breaker timeout", top_k=3)
    for r in out:
        assert r["retrieval_mode"] == cri.CONST_MODE_LEXICAL
        assert r["degraded"] is True


def test_the_existing_consumer_contract_is_unchanged(monkeypatch):
    """§20's documented snippet does `r['rule']` over the result. That must keep
    working — a fix that breaks the caller it exists to serve is not a fix."""
    monkeypatch.setattr(cri, "_ensure", lambda: False)

    out = cri.query_constitutional_constraints("deploy verification", top_k=2)
    rendered = "\n".join("- " + r["rule"] for r in out)   # the literal §20 snippet
    assert rendered.count("- ") == 2
    assert all(isinstance(r["metadata"], dict) for r in out)


def test_the_ranking_actually_ranks(monkeypatch):
    """Crude is fine; useless is not. A query about band-aid timeout fixes must
    surface the root-cause rule ahead of unrelated clauses."""
    monkeypatch.setattr(cri, "_ensure", lambda: False)

    out = cri.query_constitutional_constraints(
        "raising a timeout retry cooldown band-aid symptom", top_k=3)
    names = [r["metadata"]["name"] for r in out]
    assert "root-cause-not-symptom" in names, names
    assert out[0]["matched_terms"] > 0, "the top hit matched no query terms at all"


def test_a_query_matching_nothing_still_returns_constraints(monkeypatch):
    """§20 exists to surface rules the session might not recall. 'No keyword hit' is
    not a licence to conclude 'no constraints apply' — that is the absence-reads-as-
    health error this whole file is about."""
    monkeypatch.setattr(cri, "_ensure", lambda: False)

    out = cri.query_constitutional_constraints("zzzz qqqq xxxx", top_k=3)
    assert len(out) == 3
    assert all(r["matched_terms"] == 0 for r in out), (
        "unmatched results must SAY they are unmatched, not pose as hits")


# ── the available path: it must still prefer semantic ───────────────────────────

def test_a_working_store_is_reported_as_semantic_not_degraded(monkeypatch):
    """The converse control (R-F3858) — the fallback must not swallow the real
    path. A stubbed working store must serve semantic results, undegraded."""
    class _Coll:
        def count(self): return 31
        def query(self, query_texts, n_results):
            return {
                "documents": [["CLAUDE.md §1 [root-cause-not-symptom] ..."]],
                "metadatas": [[{"name": "root-cause-not-symptom"}]],
                "distances": [[0.1]],
            }

    monkeypatch.setattr(cri, "_ensure", lambda: True)
    monkeypatch.setattr(cri, "_constitutional_collection", _Coll())
    monkeypatch.setattr(cri, "_CONST_LAZY_SYNC_TRIED", True)

    out = cri.query_constitutional_constraints("root cause", top_k=1)
    assert out and out[0]["retrieval_mode"] == cri.CONST_MODE_SEMANTIC
    assert out[0]["degraded"] is False


def test_a_present_but_empty_store_still_delivers_rules(monkeypatch):
    """R-F3099's case, now covered by the same fallback: a store that is UP but
    answers with nothing is still an absence the caller cannot distinguish."""
    class _Empty:
        def count(self): return 0
        def query(self, query_texts, n_results):
            return {"documents": [[]], "metadatas": [[]], "distances": [[]]}

    monkeypatch.setattr(cri, "_ensure", lambda: True)
    monkeypatch.setattr(cri, "_constitutional_collection", _Empty())
    monkeypatch.setattr(cri, "_CONST_LAZY_SYNC_TRIED", True)

    out = cri.query_constitutional_constraints("anything", top_k=2)
    assert out, "a present-but-empty store must not re-create the silent no-op"
    assert out[0]["degraded"] is True


# ── proprioception: a session can ask which mode served ─────────────────────────

def test_the_retrieval_mode_is_queryable():
    """§25 — a session must be able to ask whether its binding priming was semantic,
    rather than inferring it from output that looks the same either way."""
    st = cri.constitutional_retrieval_status()
    assert st["mode"] in (cri.CONST_MODE_SEMANTIC, cri.CONST_MODE_LEXICAL)
    assert st["rules_in_code"] == len(CONSTITUTIONAL_RULES) > 0
    assert isinstance(st["vector_store_available"], bool)
    if st["degraded"]:
        assert st["reason"], "a degraded mode must say WHY, or nobody can act on it"


def test_the_rules_are_readable_without_any_vector_store():
    """The fact the whole fix rests on: the constraints are in the process already."""
    assert len(CONSTITUTIONAL_RULES) >= 31
    for rule in CONSTITUTIONAL_RULES:
        assert rule.get("name") and rule.get("clause_number")
        assert rule.get("description") and rule.get("constraint")


def test_the_documented_section_20_snippet_runs_on_this_platform():
    """CAPABILITY TEST — the operator's actual command (§23: drive the real entry
    point). It must produce rules here, where chromadb genuinely cannot be
    installed, or §20 is unenforceable on the dev box."""
    out = cri.query_constitutional_constraints(
        "modifying search_engine_health blocked engines", top_k=5)
    assert out, (
        "the §20 binding priming step produced no constraints on this platform — "
        "that is the defect R-F3911 exists to close")
    assert all("rule" in r for r in out)
