"""R-F4219 / C-199: facts recalled from ARIA's own index produced ZERO citations.

§15 and §27e make ARIA's own compounding index the moat: every paid search writes
to rag_store + intel_ledger + brain_hook, and CLAUDE.md records the live proof
("memory:documents supplied 5 of 10 results"). Measured on the live box the same
way: `/api/aria/explore` for an Angola procurement query returned 17 facts, of
which the memory-first hits carry `memory://<id>` provenance.

THE DEFECT. `chat_sources.extract()` — which builds the `sources` array on every
chat answer — could only see `https?://`. `_URL_RE` is anchored on that scheme,
and the RAG branch matches a DIFFERENT shape (`[source: ...]`) from the one
web_explorer actually emits (`URL: memory://...`). So a fact recalled from ARIA's
own corpus contributed NOTHING to the reported evidence, and an answer built
entirely from her own index reported `Sources: 0`.

That inverts the differentiator: **the better her memory got, the more ungrounded
she looked.** The asset §15 exists to build was invisible to the surface that
sells it.

THE FIX IS TO COUNT IT, NEVER TO DISGUISE IT. R-F3183 already ruled on exactly
this distinction in dd_orchestrator: "a memory:// URL is ARIA'S OWN RAG, not an
external source ... Tier by the SOURCE, not by the code path that fetched it",
tiering it MEMORY_ONLY and counting it separately. This mirrors that contract —
a distinct `type: "memory"`, never `type: "url"` — so an auditor can always tell
"recalled from our own corpus" from "fetched from defenceweb.co.za". Making
memory look external would be manufacturing certainty, which is the opposite of
the point.
"""

from __future__ import annotations

import pytest

from aria_service.intel import chat_sources as cs


# Shaped exactly like the brave_answer -> web_explorer block (routes/aria.py).
TOOL_CONTEXT = """
[TOOL: brave_answer → web_explorer (R-F336 reroute) — health=HEALTHY memory=5 web=12]
FACTS RETRIEVED (verbatim, with provenance):
  [1] [UNVERIFIED searxng:bing] Angola has pursued force modernisation
      URL: https://www.defenceweb.co.za/land/angola-modernisation/
  [2] [UNVERIFIED memory] research:web_search:Angola defence procurement
      URL: memory://2f6008008499
  [3] [UNVERIFIED memory] Angola procurement tender 2026 reference
      URL: rag://a1b2c3d4e5f6
"""
ANSWER = "Angola has pursued a modest programme of force modernisation."


def _by_type(sources):
    out = {}
    for s in sources:
        out.setdefault(s.get("type"), []).append(s)
    return out


def test_memory_provenance_is_cited_at_all():
    """The whole defect in one assertion."""
    got = _by_type(cs.extract(ANSWER, tool_context=TOOL_CONTEXT))
    assert "memory" in got, (
        "a fact recalled from ARIA's own index produced no citation, so an answer "
        "built from her own corpus reports Sources: 0 — §15's asset is invisible "
        "to the surface that sells it"
    )
    assert len(got["memory"]) == 2, got


def test_memory_is_never_disguised_as_an_external_source():
    """R-F3183's contract: ARIA quoting herself is not an external source."""
    sources = cs.extract(ANSWER, tool_context=TOOL_CONTEXT)
    for s in sources:
        if str(s.get("label", "")).startswith("ARIA memory") or s.get("type") == "memory":
            assert s.get("type") == "memory", s
            assert s.get("url") is None, (
                "a memory:// ref must not be published as a clickable external "
                "URL — that presents a self-citation as third-party evidence")


def test_external_urls_are_still_typed_as_urls():
    """The guard must still be able to FAIL (R-F3858) — real sources unaffected."""
    got = _by_type(cs.extract(ANSWER, tool_context=TOOL_CONTEXT))
    assert "url" in got, got
    assert any("defenceweb" in (s.get("label") or "") for s in got["url"]), got
    assert all(str(s.get("url", "")).startswith("http") for s in got["url"])


def test_memory_refs_carry_their_id_so_a_reader_can_trace_them():
    sources = cs.extract(ANSWER, tool_context=TOOL_CONTEXT)
    mem = [s for s in sources if s.get("type") == "memory"]
    blob = " ".join(str(s.get("label")) for s in mem)
    assert "2f6008008499" in blob, mem
    assert "a1b2c3d4e5f6" in blob, mem


def test_memory_refs_are_deduplicated():
    """Repeating a recall must not inflate the evidence count."""
    doubled = TOOL_CONTEXT + "\n      URL: memory://2f6008008499\n"
    mem = [s for s in cs.extract(ANSWER, tool_context=doubled) if s.get("type") == "memory"]
    assert len(mem) == 2, mem


def test_no_memory_refs_means_no_memory_sources():
    """Absence must read as absence, not as a manufactured citation."""
    plain = "Angola has pursued modernisation.\n  URL: https://example.org/a"
    got = _by_type(cs.extract(ANSWER, tool_context=plain))
    assert "memory" not in got, got


@pytest.mark.parametrize("scheme", ["memory", "rag"])
def test_both_internal_schemes_are_recognised(scheme):
    ctx = f"  URL: {scheme}://deadbeef1234"
    mem = [s for s in cs.extract("x", tool_context=ctx) if s.get("type") == "memory"]
    assert len(mem) == 1, (scheme, mem)
