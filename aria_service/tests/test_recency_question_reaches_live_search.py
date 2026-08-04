"""A question about something happening NOW must reach live search.

MEASURED — live WhatsApp, 2026-08-04 09:09.

    operator: "How is the fires in Madrid and Bordeaux?"
    ARIA:     "I don't have live news or real-time incident data in front of me
               right now, so I'm not going to guess at conditions, evacuations,
               or air quality — that would be irresponsible."
              "Want me to pull a current situation brief for both areas?"

She has Brave Search. It had answered DD queries forty minutes earlier in the
same process. The message never reached a tool, so the engine answered from
static knowledge — and the reply is scrupulously honest about what it was handed
while being wrong about what she can do. The offer at the end is the tell: she
knew the capability existed and simply had not been routed to it.

WHY IT MISSED. _BRAVE_QA_TRIGGER_RE requires a question word FOLLOWED BY a
linking verb, and admits `how` only before many/much/long/old/big/tall/far — a
deliberate dodge for "how are you". That narrowing excludes every
"how is/are the <thing>" question, and "latest news on X" has no leading
question word at all. Probed against the live detector, all returned NO TOOL:

    "How are the fires in Madrid and Bordeaux?"
    "latest news on the Bordeaux fires"
    "what's the current situation with wildfires in Spain"

while "what is happening with the wildfires in Madrid" routed correctly. The
difference is phrasing, not need.

THE PROPERTY THAT MATTERS IS RECENCY — has the answer changed since training —
not whether the sentence opens with an approved verb pair. Matching on surface
form instead of the underlying property is the same defect this codebase found
in the sanctions matcher (a shared generic noun read as identity evidence) and
in the DD skipped-layer scalars (a default read as a measurement).

The guard against over-firing is that `how is/are the` REQUIRES the article, and
that all four pre-existing exclusions still apply unchanged.

NOTE: no R-number — data/r_number_reservations.json is the peer agent's ledger.
"""
from __future__ import annotations

import pytest

from aria_service.routes import aria as R


RECENCY_QUESTIONS = [
    "How is the fires in Madrid and Bordeaux?",       # verbatim, as sent
    "How are the fires in Madrid and Bordeaux?",
    "latest news on the Bordeaux fires",
    "what's the current situation with wildfires in Spain",
    "any update on the Madrid evacuation",
    "is the airport still closed",
    "what is happening with the wildfires in Madrid",  # already worked; must stay
]


@pytest.mark.parametrize("q", RECENCY_QUESTIONS)
def test_a_recency_question_routes_to_live_search(q):
    intent = R._detect_tool_intent(q)
    assert intent, (
        f"no tool for {q!r} — ARIA answers from training data and reports having "
        f"no live access, while holding a working Brave key"
    )
    assert intent.get("tool") == "brave_answer", (
        f"{q!r} routed to {intent.get('tool')!r}; a current-events question "
        f"belongs on the cheap grounded-search path"
    )


def test_the_verbatim_incident_message_is_covered():
    """Pin the exact string the operator sent, grammar and all."""
    intent = R._detect_tool_intent("How is the fires in Madrid and Bordeaux?")
    assert intent["tool"] == "brave_answer"
    assert intent["_reason"] == "recency_qa", (
        "should qualify on RECENCY, not on question-shape — the shape check is "
        "exactly what it fails"
    )


# ── Over-firing guards. Each of these was already correct and must stay so. ──

@pytest.mark.parametrize("q", [
    "how are you",
    "how are things",
    "how is it going",
])
def test_pleasantries_still_reach_no_tool(q):
    """`how is/are the` requires the article precisely so these keep falling through."""
    intent = R._detect_tool_intent(q)
    assert (intent or {}).get("tool") != "brave_answer", (
        f"{q!r} would spend a paid search on small talk"
    )


@pytest.mark.parametrize("q", [
    "what is the time in Portugal",
    "what time is it",
    "today's date",
])
def test_the_clock_is_never_searched(q):
    """R-F3592 — the clock is in her system prompt; searching for it is worse."""
    intent = R._detect_tool_intent(q)
    assert (intent or {}).get("tool") != "brave_answer", (
        f"{q!r} re-opened the R-F3592 hole: a paid call for something the "
        f"process already holds"
    )


@pytest.mark.parametrize("q,expected_not", [
    ("run a dd on Acme Ltd", "brave_answer"),
    ("screen Acme for sanctions", "brave_answer"),
    ("what is your memory status", "brave_answer"),
    ("how is your brain", "brave_answer"),
])
def test_specialist_paths_are_not_diverted(q, expected_not):
    """Heavier tools and self-introspection keep their own routes."""
    intent = R._detect_tool_intent(q)
    assert (intent or {}).get("tool") != expected_not, (
        f"{q!r} was diverted to web search away from its specialist tool"
    )
