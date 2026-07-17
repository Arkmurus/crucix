"""R-F2687 — PR-echo detector (C-3 v3). The residual R-F2677 left open before `enforce`.

THE BUG: a company press release REWORDED by >=2 trade outlets escapes the R-F2669
Jaccard-0.6 lexical gate — each outlet writes its own prose, so the word-5-shingles
diverge and the two echoes count as 2 INDEPENDENT origins. The report then claims
"independently corroborated" for a claim nobody independently checked: both sources
trace back to ONE origin, the subject's own announcement.

THE SIGNAL: journalists reword prose but paste QUOTES verbatim. A shared >=8-word
quoted span means one origin. Semantic cosine alone is NOT usable here — it measures
topic, not origin (two independent investigations of one event are also similar), so
it is gated behind a PR marker on both sides.

SAFETY DIRECTION: every signal here only ever MERGES clusters → origin counts can only
go DOWN → "never over-claim independence" (FP-rate 0) holds by construction. The cost
is recall, which is exactly what the C-3 measure-mode eval must score. Nothing here
claims that gate is met.
"""
import asyncio

import pytest

from aria_service.intel.dd_independent_verifier import (
    assess_independent_verification,
    cluster_stories_with_echo,
    detect_pr_echo,
    has_pr_marker,
    quote_fingerprints,
    shares_verbatim_quote,
)

# ── Fixtures: ONE press release, reworded by two trade outlets ────────────────
# Deliberately written the way real trade coverage reads: different prose, different
# lede, different length — but the SAME pasted CEO quote. This is the case that
# escapes Jaccard-0.6 today.

_QUOTE = (
    "This acquisition marks a decisive step in our long term strategy to serve "
    "customers across every European market we operate in"
)

_OUTLET_A = f"""
Acme Corp has agreed to buy Beta Systems, the two companies confirmed on Tuesday.
The deal, announced today in a press release, values the target at around 40 million
euros and is expected to close in the fourth quarter subject to regulatory clearance.
"{_QUOTE}," said Acme chief executive Jane Roe.
Beta Systems employs roughly 120 people at its Munich headquarters and will continue
to operate under its own brand for an initial transition period, the companies said.
"""

_OUTLET_B = f"""
German payments group Beta Systems is being acquired by Acme Corp in a transaction
worth about 40 million euros, according to a statement issued by the buyer.
Completion is slated for the final quarter of the year, pending approval from
competition regulators in Germany and Austria.
Acme boss Jane Roe commented: "{_QUOTE}."
The Munich based target, which has some 120 staff, keeps its brand for now.
"""

# A genuinely INDEPENDENT investigation of the same event: same topic, same facts,
# NO shared quote, no PR marker. This must NOT be collapsed — collapsing it is the
# recall failure that naive semantic clustering would cause.
_INDEPENDENT = """
Our reporters have reviewed filings lodged with the Munich commercial register showing
that Acme Corp quietly took control of Beta Systems several weeks before either firm
acknowledged the transaction publicly. Two people with direct knowledge of the talks,
who spoke on condition of anonymity because the negotiations were private, said the
final price was revised downward late in the process after due diligence uncovered
unresolved customer disputes. Acme declined to comment on the discrepancy when asked.
"""


# ── 1. Quote fingerprinting ───────────────────────────────────────────────────

def test_quote_fingerprints_extracts_the_pasted_quote():
    qa = quote_fingerprints(_OUTLET_A)
    qb = quote_fingerprints(_OUTLET_B)
    assert qa and qb
    assert shares_verbatim_quote(qa, qb), "the pasted CEO quote is the PR-echo tell"


def test_short_quotes_are_ignored():
    """Boilerplate must not manufacture false echoes (that would UNDER-count independence)."""
    a = 'The spokesman said "we are delighted" about the outcome of the review process today.'
    b = 'A rival firm also said "we are delighted" in an unrelated announcement this week.'
    assert not shares_verbatim_quote(quote_fingerprints(a), quote_fingerprints(b))


def test_independent_piece_shares_no_quote():
    assert not shares_verbatim_quote(
        quote_fingerprints(_OUTLET_A), quote_fingerprints(_INDEPENDENT)
    )


# ── 2. PR markers ─────────────────────────────────────────────────────────────

def test_pr_markers_detected_on_both_echoes():
    assert has_pr_marker(_OUTLET_A)   # "in a press release" / "announced today"
    assert has_pr_marker(_OUTLET_B)   # "according to a statement"


def test_independent_investigation_has_no_pr_marker():
    assert not has_pr_marker(_INDEPENDENT)


# ── 3. The detector ───────────────────────────────────────────────────────────

def test_reworded_pr_is_detected_as_echo():
    is_echo, reason = asyncio.run(detect_pr_echo(_OUTLET_A, _OUTLET_B))
    assert is_echo is True
    assert reason == "shared_verbatim_quote"


def test_independent_investigation_is_not_an_echo():
    """The recall guard: same event, same facts — but a real second witness."""
    is_echo, _ = asyncio.run(detect_pr_echo(_OUTLET_A, _INDEPENDENT))
    assert is_echo is False, (
        "an independent investigation must NOT be collapsed — that is the recall "
        "failure naive cosine clustering would cause"
    )


# ── 4. Clustering ─────────────────────────────────────────────────────────────

def test_echo_clustering_collapses_the_two_echoes():
    stories, echoes = asyncio.run(cluster_stories_with_echo({
        "https://trade-a.example/acme": _OUTLET_A,
        "https://trade-b.example/beta": _OUTLET_B,
    }))
    assert len(set(stories.values())) == 1, "two echoes of one PR = ONE origin"
    assert echoes and echoes[0]["reason"] == "shared_verbatim_quote"


def test_echo_clustering_keeps_the_independent_story_separate():
    stories, _ = asyncio.run(cluster_stories_with_echo({
        "https://trade-a.example/acme": _OUTLET_A,
        "https://trade-b.example/beta": _OUTLET_B,
        "https://real-journalism.example/probe": _INDEPENDENT,
    }))
    assert len(set(stories.values())) == 2, (
        "the two PR echoes collapse to one origin; the independent probe stays its own"
    )


# ── 5. THE capability test — the real report path ─────────────────────────────

def test_two_pr_echoes_no_longer_claim_independent_corroboration():
    """Drive assess_independent_verification, the function the DD orchestrator calls.

    PRE-FIX: 2 reworded echoes → 2 origins → press_independently_corroborated=True
    (a fabrication — nobody independently checked the claim).
    POST-FIX: 1 origin → False.
    """
    pages = {
        "https://trade-a.example/acme": _OUTLET_A,
        "https://trade-b.example/beta": _OUTLET_B,
    }

    async def _fetch(url):
        return 200, pages[url]

    report = {
        "entity": "Acme Corp",
        "digital": {"press_coverage": [{"url": u} for u in pages]},
    }
    res = asyncio.run(assess_independent_verification(report, fetcher=_fetch))

    assert res["independent_press_origins"] == 1, (
        "two reworded echoes of ONE press release must count as ONE origin"
    )
    assert res["press_independently_corroborated"] is False, (
        "R-F2687: claiming independent corroboration from two PR echoes is a fabrication"
    )
    assert res["pr_echoes_collapsed"] == 1
    assert res["pr_echoes"][0]["reason"] == "shared_verbatim_quote"


def test_genuine_second_witness_still_corroborates():
    """The gate must not become a wall: real independent coverage still counts."""
    pages = {
        "https://trade-a.example/acme": _OUTLET_A,
        "https://real-journalism.example/probe": _INDEPENDENT,
    }

    async def _fetch(url):
        return 200, pages[url]

    report = {
        "entity": "Acme Corp",
        "digital": {"press_coverage": [{"url": u} for u in pages]},
    }
    res = asyncio.run(assess_independent_verification(report, fetcher=_fetch))
    assert res["independent_press_origins"] == 2
    assert res["press_independently_corroborated"] is True
    assert res["pr_echoes_collapsed"] == 0


# ── 6. Embedder-unavailable must not grant independence ───────────────────────

# Two reworded PR echoes that share NO quote — only the semantic path can link
# these, so they exercise it (the quote path returns before it otherwise).
_NOQUOTE_A = """
Acme Corp said in a statement that it has agreed to acquire Beta Systems for about
40 million euros, with completion expected in the fourth quarter subject to clearance
from competition regulators. Beta employs around 120 staff in Munich and will keep its
brand through an initial transition period, the buyer added.
"""
_NOQUOTE_B = """
Beta Systems is to be bought by Acme Corp for roughly 40 million euros, according to a
statement from the acquirer. The transaction should complete in the final quarter once
regulators sign off. The Munich target has some 120 employees and retains its brand for
a transitional phase.
"""


def test_missing_embedder_does_not_grant_independence(monkeypatch):
    """None from the embedder = 'no semantic signal', never 'not an echo'.

    Drives the path that ACTUALLY calls semantic_similarity: no shared quote, PR
    markers on both sides. (The previous version of this test monkeypatched the
    embedder then asserted the QUOTE path, which returns before semantic_similarity
    is ever reached — it passed identically with the patch removed. Pass 2 caught it.)
    """
    from aria_service.intel import dd_independent_verifier as _v

    assert has_pr_marker(_NOQUOTE_A) and has_pr_marker(_NOQUOTE_B)
    assert not shares_verbatim_quote(
        quote_fingerprints(_NOQUOTE_A), quote_fingerprints(_NOQUOTE_B)
    ), "fixture must have no shared quote, else it never reaches the semantic path"

    called = {"n": 0}

    async def _no_embedder(a, b):
        called["n"] += 1
        return None

    monkeypatch.setattr(_v, "semantic_similarity", _no_embedder)
    is_echo, reason = asyncio.run(_v.detect_pr_echo(_NOQUOTE_A, _NOQUOTE_B))
    assert called["n"] == 1, "the semantic path must actually be exercised"
    assert is_echo is False and reason == ""


def test_semantic_path_fires_when_model_agrees(monkeypatch):
    """The secondary signal works when the embedder IS available and confident."""
    from aria_service.intel import dd_independent_verifier as _v

    async def _high(a, b):
        return 0.97

    monkeypatch.setattr(_v, "semantic_similarity", _high)
    is_echo, reason = asyncio.run(_v.detect_pr_echo(_NOQUOTE_A, _NOQUOTE_B))
    assert is_echo is True and reason.startswith("pr_marker_and_semantic_")


def test_semantic_signal_requires_pr_markers_on_both_sides(monkeypatch):
    """Cosine measures TOPIC, not origin — it must never collapse on its own.

    The independent investigation is about the same event, so a model will score it
    similar. Without a PR marker it must still count as its own origin.
    """
    from aria_service.intel import dd_independent_verifier as _v

    async def _high(a, b):
        return 0.99

    monkeypatch.setattr(_v, "semantic_similarity", _high)
    is_echo, _ = asyncio.run(_v.detect_pr_echo(_NOQUOTE_A, _INDEPENDENT))
    assert is_echo is False, (
        "high cosine alone must not collapse an independent witness — that is the "
        "recall failure naive semantic clustering causes"
    )


# ── 7. The Pass-2 counterexample: merging must never SPLIT other clusters ─────

def test_echo_merge_cannot_increase_origin_count():
    """Regression for the bug Pass 2 found in the first draft of this detector.

    With first-match-against-a-representative clustering, absorbing B into A meant B
    never became a representative — so a later C that matched only B fragmented into a
    NEW story, RAISING the origin count and flipping a report to a false
    "independently corroborated". Union-find fixes it: an origin is a connected
    component, and adding an edge can only merge components, never split one.

    Chain: A -echo- B -lexical- C. All three must land in ONE story.
    """
    # C is a near-duplicate of B (wire syndication of B's copy) but NOT of A.
    _C = _OUTLET_B.replace("German payments group", "The German payments group")

    stories, _ = asyncio.run(cluster_stories_with_echo({
        "a": _OUTLET_A,   # PR echo of B (shared quote), lexically distant
        "b": _OUTLET_B,
        "c": _C,          # lexical near-duplicate of B
    }))
    assert len(set(stories.values())) == 1, (
        "A~B (echo) and B~C (lexical) must transitively be ONE origin; if the echo "
        "merge strands C, origins go UP and the detector manufactures the very "
        "fabrication it exists to prevent"
    )
