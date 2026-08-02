"""R-F3649 — a citation is checked against a NORMALISED allowlist, so it must be
normalised too.

THE DEFECT, FOUND IN A LIVE RESULT. `_independent_sources` builds the citable
allowlist from result URLs and strips the `www.` prefix (`re.sub(r"^www\\.", ...)`)
while lowercasing. Both citation checks in `validate_trace` then compared the
model's citation RAW against that normalised set:

    if cited.strip() not in allowed:          # generic grounding check
    if cited.strip() not in indep:            # news outlet independence gate

So a model that cited `www.reuters.com`, for a payload whose result URL was
literally `https://www.reuters.com/...`, was scored as citing something "no tool
result contains" — while `reuters.com` passed. The producer normalised and the
consumer did not.

WHAT IT COST. On the 2026-08-02 tool-use cycle (168 held-out rows), rows 1 and 91
failed for this reason and NO other:

    row  1 tooluse_adverse      cites 'www.reuters.com'           available ['money.cnn.com', 'reuters.com', 'uk.reuters.com']
    row 91 tooluse_news_impact  cites 'www.dassault-aviation.com' available ['dassault-aviation.com', 'finance.yahoo.com']

That understated the trained honesty rate as 147/168 = 0.875 when it was
149/168 = 0.887, and it counted against the two axes that regressed. The same
validator gates corpus construction, so it was also rejecting correctly-grounded
training rows.

WHY THIS IS A CORRECTION AND NOT A RELAXATION. `www.X` and `X` are the same host,
so normalising them cannot admit anything a tool did not return. The test below
pins that: a genuinely absent outlet is STILL rejected, and a distinct subdomain
(`uk.reuters.com`) is STILL distinct — the fix is exact-match on a canonical
spelling, not a fuzzy match.
"""
from __future__ import annotations

import pytest

from scripts.train import build_tooluse_corpus as B


# The live shape: result URLs carry `www.`, so the allowlist is built www-stripped.
TWO_INDEPENDENT = {
    "query": "Serco contract award",
    "results": [
        {"title": "Serco wins MoD contract", "source": "aria_search",
         "url": "https://www.reuters.com/serco-mod", "snippet": "Serco was awarded a contract."},
        {"title": "Serco secures defence deal", "source": "aria_search",
         "url": "https://www.ft.com/serco-defence", "snippet": "Serco secured a defence deal."},
    ],
}


def _trace_citing(*cites: str) -> dict:
    """A real news trace whose final answer cites exactly `cites`.

    Built by the SHIPPING builder, then only the final answer is swapped, so the
    trace stays structurally valid and the citation check is what is under test.
    """
    t = B.build_news_impact_trace("Serco Group plc", TWO_INDEPENDENT)
    suffix = " ".join(f"[from {c}]" for c in cites)
    final = (
        "Serco was awarded an MoD contract, reported by two independent outlets "
        f"{suffix}. The award is a revenue event; it does not by itself change "
        "control, ownership or sanctions exposure."
    )
    for m in reversed(t["messages"]):
        if m.get("role") == "assistant" and not m.get("tool_calls"):
            m["content"] = final
            break
    return t


def _citation_errors(trace: dict) -> list[str]:
    return [e for e in B.validate_trace(trace)
            if "no tool result contains" in e or "independent source" in e]


# ── the capability: the live failure, replayed ─────────────────────────────

def test_www_prefixed_citation_of_a_returned_outlet_is_accepted():
    """Row 1 / row 91 of the 2026-08-02 cycle. This is the user-visible symptom."""
    assert _citation_errors(_trace_citing("www.reuters.com", "www.ft.com")) == []


def test_bare_host_still_accepted():
    """The spelling that always worked must keep working."""
    assert _citation_errors(_trace_citing("reuters.com", "ft.com")) == []


def test_mixed_spellings_in_one_answer_are_both_accepted():
    assert _citation_errors(_trace_citing("www.reuters.com", "ft.com")) == []


def test_uppercase_host_is_accepted():
    assert _citation_errors(_trace_citing("WWW.Reuters.COM", "FT.com")) == []


# ── the guard: this must not become a fuzzy match ──────────────────────────

def test_outlet_the_search_never_returned_is_still_rejected():
    """The fix must not turn a fabricated citation into a pass."""
    errs = _citation_errors(_trace_citing("www.nytimes.com"))
    assert errs, "a fabricated outlet must still be caught"
    assert any("nytimes.com" in e for e in errs)


def test_distinct_subdomain_is_not_collapsed_into_the_parent():
    """`uk.reuters.com` is a different URL; only the `www.` prefix is canonical."""
    errs = _citation_errors(_trace_citing("uk.reuters.com"))
    assert errs, "a subdomain the search did not return must still be caught"


# ── the normaliser itself ──────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("www.reuters.com", "reuters.com"),
    ("reuters.com", "reuters.com"),
    ("WWW.Reuters.COM", "reuters.com"),
    ("  www.ft.com  ", "ft.com"),
    ("uk.reuters.com", "uk.reuters.com"),
    ("wwwtf.com", "wwwtf.com"),      # only a real `www.` prefix is stripped
    ("", ""),
    (None, ""),
])
def test_norm_cite(raw, expected):
    assert B._norm_cite(raw) == expected


# ── proof the test detects the ORIGINAL defect ─────────────────────────────

def test_without_normalisation_the_live_failure_reappears(monkeypatch):
    """Pin that these tests would FAIL on the pre-fix code.

    §3c requires a capability test that fails before the fix. Restoring the old
    raw comparison (identity normalisation) must bring the exact live error back —
    if this passes, the tests above are not testing what they claim to.
    """
    monkeypatch.setattr(B, "_norm_cite", lambda s: str(s or "").strip())
    errs = _citation_errors(_trace_citing("www.reuters.com", "www.ft.com"))
    assert errs, "expected the pre-fix code to reject a www-prefixed citation"
    assert any("www.reuters.com" in e for e in errs)
