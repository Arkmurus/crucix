"""R-F2397 — grounding_reward must recognize ARIA's REAL production RAG citation
formats, not just the synthetic ``[Source: Sx]``. The old ``_CITE_RE`` extracted
~0 sources from a production context (which uses ``↳ source: <label>`` lines and
``• [n.nn] <type>:...`` chunk headers), so it flagged EVERY real citation as
fabricated — the bug behind 3 false "FAIL" verdicts.

These are CAPABILITY tests: they drive the real scorer path (score()/reward()) on
production-format contexts and assert honest attribution — genuine citations are
credited, fabricated ones are still rejected (no over-crediting), and the scoring
weights/thresholds are UNCHANGED (only format recognition was extended).
"""
from aria_service.intel import grounding_reward as gr

# A realistic slice of ARIA's production open-book RAG context format
# (get_rag_context_with_sources): bullet chunks with a relevance score, a header,
# and an authoritative "↳ source:" label line.
PROD_CTX = (
    "[RAG RETRIEVED — proprietary intelligence indexed from your sources.]\n\n"
    "• [1.04] web_search:What is the current population of the FAA: 6 results\n"
    "  ↳ source: brain_hook:web_search | 2026-05-15\n\n"
    "• [0.99] Angolan Armed Forces active personnel estimate\n"
    "  ↳ source: mem0:session_eval_5183c5f8fd:2026-05-15T06:01:36Z\n\n"
    "• [0.97] Who is the current Defence Minister\n"
    "  ↳ source: research:investigation:current Who is the current Def | 2026-06-07\n"
)


def test_extract_context_sources_recognizes_production_format():
    """The new extractor finds the real ↳ source labels — the old one found ~none."""
    srcs = gr.extract_context_sources(PROD_CTX)
    assert any("mem0:session_eval_5183c5f8fd" in s for s in srcs), srcs
    assert any("brain_hook:web_search" in s for s in srcs), srcs
    assert any("research:investigation:current who is the current def" in s for s in srcs), srcs
    # regression proof: the OLD path (extract_citations) does NOT see these labels
    old = set(gr.extract_citations(PROD_CTX))
    assert not any("mem0:session_eval_5183c5f8fd" in s for s in old), old


def test_genuine_citation_to_prod_label_is_grounded():
    """An answer that cites a real ↳ source label is fully grounded (precision 1.0)."""
    ans = ("Mozambique's status is described in the retrieved intel "
           "[from mem0:session_eval_5183c5f8fd:2026-05-15T06:01:36Z].")
    b = gr.score(ans, PROD_CTX)
    assert b.grounded_citations == 1, b.as_dict()
    assert b.fabricated_citations == 0, b.as_dict()
    assert b.citation_precision == 1.0, b.as_dict()


def test_leaf_and_suffix_citations_credited():
    """Hierarchical leaf (investigation:X ⊂ research:investigation:X) and
    label+date suffix are genuine references and must be credited."""
    ans = ("The minister question is addressed "
           "[from investigation:current Who is the current Def] and confirmed "
           "[from research:investigation:current Who is the current Def | 2026-06-07].")
    b = gr.score(ans, PROD_CTX)
    assert b.fabricated_citations == 0, b.as_dict()
    assert b.grounded_citations == 2, b.as_dict()


def test_fabricated_citation_still_rejected_no_overcredit():
    """A citation to a source NOT in the context stays fabricated — the fix must
    not over-credit (honest attribution)."""
    ans = ("The figure is confirmed by external reporting "
           "[from OSINT Weekly Bulletin 2027-01] and [Source: Jane's Defence 2099].")
    b = gr.score(ans, PROD_CTX)
    assert b.grounded_citations == 0, b.as_dict()
    assert b.fabricated_citations == 2, b.as_dict()
    assert b.score <= 0.05, b.as_dict()  # no grounded citation -> capped low


def test_synthetic_sx_format_still_works():
    """Backward-compat: the original [Source: Sx] context format is still parsed."""
    ctx = "Context: [Source: S1] Saudi 2024 defence budget ~$75B. [Source: S2] ..."
    ans = "The budget is ~$75B [Source: S1]."
    b = gr.score(ans, ctx)
    assert b.grounded_citations == 1, b.as_dict()
    assert b.fabricated_citations == 0, b.as_dict()
    assert b.citation_precision == 1.0, b.as_dict()


def test_scoring_weights_unchanged_regression():
    """Guard: only format recognition changed, NOT the scoring math. A fully
    grounded synthetic answer still scores 1.0; a no-context abstention still 1.0;
    a fabrication-with-no-context still 0.0 (exact prior case-score contract)."""
    # fully grounded synthetic -> precision 1.0 -> score 1.0
    assert gr.reward("x [Source: S1]", "[Source: S1] fact") == 1.0
    # correct abstention when context has no sources -> 1.0
    assert gr.reward("I cannot confirm this from the context.", "") == 1.0
    # fabricated sources with no context -> 0.0
    assert gr.reward("It is true [Source: S9]", "") == 0.0
