"""R-F4133 (C-168) — knowledge recall returned facts that matched NO query word,
ranked above facts that did, because the popularity boost was added before the
relevance threshold was tested.

`_rank_knowledge_facts` scored each fact like this:

    score = 0
    for w in words:
        if w in text:
            score += 3
    score += min(f.get("accessCount", 0), 5)   # <-- added unconditionally
    if score > 0:
        scored.append((score, f))

`accessCount` is not a recall counter. All three of its bumps live in
`store_fact` and fire on **re-absorption** — the same content or topic being
stored again — which is what ARIA's crawl and reading loops do constantly (§28
measured the production mix at roughly one new fact per nine re-absorbs).

So the threshold `score > 0` was satisfied by popularity alone, and the ordering
was worse than the inclusion:

  * a fact matching ONE query word scores 3
  * a fact matching NOTHING, re-absorbed 5+ times, scores 5

The irrelevant fact wins outright, and on a tie the stable sort hands the slot to
whichever appeared earlier in the corpus. `search_knowledge` renders the result
into the chat prompt under the header "[ARIA KNOWLEDGE BASE — verified facts]",
so unrelated rows were presented to the model as established fact about the
subject being asked about.

**Measured on production 2026-08-17**, sampling three 8 MB windows of the live
416 MB corpus (567,720 facts):

    window @0 MB     11,368 facts   accessCount>=1  3.8%   max 44
    window @198 MB   11,663 facts   accessCount>=1 11.7%   max 848
    window @384 MB   12,162 facts   accessCount>=1 16.8%   max 3,593

≈10.8% overall, i.e. **~61,000 facts entered the candidate set of every single
query** regardless of the query, and the maxima show the +5 cap is reached
comfortably.

It is self-worsening in the same way as C-95 and C-166: re-absorption is how
ARIA reads, so the noise floor rises the more she learns. Under §7 (no eviction)
it never falls.

The boost itself is not the defect and is kept — popularity is a reasonable
tie-breaker BETWEEN RELEVANT FACTS. It simply must not manufacture relevance.
"""
from __future__ import annotations

from aria_service.intel import knowledge as k


def _corpus():
    """One relevant fact among 300 popular irrelevant ones — the live shape."""
    facts = [
        {"id": f"f{i}", "topic": f"topic {i}",
         "content": f"unrelated filler about widgets number {i}",
         "accessCount": 3 if i % 2 == 0 else 0, "confidence": 0.9, "createdAt": "2026-01-01"}
        for i in range(300)
    ]
    facts.append({"id": "rel", "topic": "rosoboronexport",
                  "content": "rosoboronexport supplies air-defence systems",
                  "accessCount": 0, "confidence": 0.9, "createdAt": "2026-01-01"})
    return facts


def _install(monkeypatch, facts):
    monkeypatch.setattr(k, "_cache", {"facts": facts}, raising=False)
    k._search_lc.clear()
    monkeypatch.setattr(k, "_search_lc_facts_id", 0, raising=False)


def test_a_query_that_matches_nothing_returns_nothing(monkeypatch):
    """The headline symptom. Before the fix this returned 10 facts, none of
    which contained a single query word — and search_knowledge rendered them
    into the prompt as verified facts about the subject."""
    _install(monkeypatch, _corpus())
    out = k._rank_knowledge_facts("zzzqqxx nonexistentterm", 10)
    assert out == [], (
        f"recall invented {len(out)} 'verified facts' for a query none of them "
        f"match: {[f.get('id') for f in out][:5]}")


def test_the_relevant_fact_is_not_displaced_by_popular_noise(monkeypatch):
    """The damaging case: the answer exists and is pushed out of the results.

    Before the fix the single matching fact scored 3 while 150 non-matching
    facts scored 3 (0 + accessCount 3), and the stable sort gave every one of
    the ten slots to the earlier-indexed noise."""
    _install(monkeypatch, _corpus())
    ids = [f.get("id") for f in k._rank_knowledge_facts("rosoboronexport", 10)]
    assert ids and ids[0] == "rel", (
        f"the only fact containing the query word ranked {ids.index('rel') + 1 if 'rel' in ids else 'nowhere'}; "
        f"top of the list was {ids[:3]}")


def test_popularity_still_breaks_ties_between_RELEVANT_facts(monkeypatch):
    """The boost is kept, not removed. Two facts match the query equally; the
    re-absorbed one should still win. A fix that simply deleted the boost would
    pass the two tests above and silently drop this behaviour."""
    facts = [
        {"id": "quiet", "topic": "sanctions", "content": "sanctions guidance",
         "accessCount": 0, "confidence": 0.9, "createdAt": "2026-01-01"},
        {"id": "popular", "topic": "sanctions", "content": "sanctions guidance",
         "accessCount": 4, "confidence": 0.9, "createdAt": "2026-01-01"},
    ]
    _install(monkeypatch, facts)
    ids = [f.get("id") for f in k._rank_knowledge_facts("sanctions", 10)]
    assert ids[0] == "popular", f"popularity tie-break lost: {ids}"


def test_a_matching_fact_always_outranks_a_non_matching_one(monkeypatch):
    """The invariant, stated directly: no amount of re-absorption should let a
    fact that matches nothing beat one that matches something. accessCount 3593
    is the real maximum observed in the live corpus."""
    facts = [
        {"id": "noise", "topic": "widgets", "content": "widgets and gaskets",
         "accessCount": 3593, "confidence": 0.9, "createdAt": "2026-01-01"},
        {"id": "hit", "topic": "embargo", "content": "embargo details",
         "accessCount": 0, "confidence": 0.9, "createdAt": "2026-01-01"},
    ]
    _install(monkeypatch, facts)
    ids = [f.get("id") for f in k._rank_knowledge_facts("embargo", 10)]
    assert ids == ["hit"], (
        f"a fact re-absorbed 3593 times and matching nothing was served: {ids}")


def test_substring_matching_is_preserved(monkeypatch):
    """Guard against the 'obvious' optimisation.

    Scoring is a SUBSTRING test (`w in text`), not a word test: "export"
    legitimately matches "rosoboronexport". C-166 recommended replacing the
    O(corpus) scan with a word-level inverted index — that would silently narrow
    recall, which on an adverse-media path is the R-F3857 failure (an emptied
    result set reads as CLEAN). Any future index must be a candidate SUPERSET
    verified by this same substring test.
    """
    _install(monkeypatch, [
        {"id": "rel", "topic": "rosoboronexport",
         "content": "rosoboronexport supplies air-defence systems",
         "accessCount": 0, "confidence": 0.9, "createdAt": "2026-01-01"},
    ])
    assert [f["id"] for f in k._rank_knowledge_facts("export", 10)] == ["rel"]


def test_search_knowledge_emits_no_verified_facts_block_when_nothing_matches(monkeypatch):
    """The user-visible outcome (§3c): the capability, not the helper.

    search_knowledge feeds the chat prompt. On a no-match query it must return
    an empty string so no block is injected at all — not a header followed by
    unrelated rows."""
    _install(monkeypatch, _corpus())
    assert k.search_knowledge("zzzqqxx nonexistentterm") == ""
