"""ARIA Negotiation Principles — conditional Tier D system prompt addendum.

Distills the negotiation-tradecraft material from corpus Tier D (Harvard
Program on Negotiation, HBR negotiation strategy collection, Fisher/Ury
"Getting to Yes", Voss "Never Split the Difference") into a condensed
operating set ARIA's LLM is given ONLY when the user is asking about a
deal, approach, counterparty, or BD strategy decision.

Why conditional, not always-on
══════════════════════════════
Unlike `analytic_principles.py` (which is always injected because every
substantive reply benefits from the tradecraft), negotiation guidance is
narrow-domain. Injecting it on every reply would dilute the LLM's
attention against the analytic principles, which are higher leverage on
the typical query. So this module fires only when the message clearly
looks like a negotiation/strategy intent — same conservative-detector
pattern used by `pmesii.py` for country assessments.

Length budget
═════════════
~1500 chars when active. The conditional injection means we have more
budget than the always-on `analytic_principles.py` (which has to share
the prompt with every other addendum on every reply).

Behind ARIA_NEGOTIATION_PRINCIPLES env var (default ON). Disabled → no-op.
"""
from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger("aria.negotiation_principles")


# Conservative intent detector. Fires on:
#   - Explicit negotiation verbs ("negotiate", "negotiation", "negotiating")
#   - BD lifecycle terms tied to closing ("close the deal", "deal close",
#     "deal review", "approach strategy", "approach plan", "pricing
#     strategy", "counter-offer", "counter offer", "concession")
#   - Strategy framing ("BATNA", "ZOPA", "opening offer", "anchor price",
#     "walk-away", "walkaway", "reservation price")
#   - Direct asks ("how should I approach", "what should I offer",
#     "how do I negotiate", "best approach to", "negotiation strategy
#     for", "approach plan for")
#
# Does NOT fire on generic mentions of "deal" or "approach" — those words
# are too common in normal BD chatter. Requires one of the high-signal
# phrases above to avoid noise.
_NEGOTIATION_RE = re.compile(
    r"\b("
    r"negotiat(?:e|ion|ing|or)|"
    r"batna|zopa|"
    r"opening offer|anchor (?:price|number)|"
    r"reservation price|walk[-\s]?away|"
    r"counter[-\s]?offer|concession|trade[-\s]?off|"
    r"close the deal|deal close|deal review|"
    r"pricing strategy|"
    r"approach (?:strategy|plan)|"
    r"how (?:should i|do i) approach|"
    r"how (?:should i|do i) negotiate|"
    r"what should i offer|"
    r"negotiation strategy|"
    r"best approach to"
    r")\b",
    re.IGNORECASE,
)


_NEGOTIATION_PRINCIPLES_BLOCK = """NEGOTIATION & APPROACH TRADECRAFT — apply when the user is asking about a deal, approach, or counterparty engagement

These are the load-bearing principles from the Harvard Program on Negotiation, Fisher & Ury "Getting to Yes", Voss "Never Split the Difference", and the HBR negotiation collection. Apply BEFORE recommending an opening move, a price, or a meeting plan.

INTERESTS, NOT POSITIONS
1. Map the OTHER SIDE's interests before stating your own. What does THEY actually need (revenue, political cover, technology transfer, training, schedule certainty)? Their stated position is rarely identical to their underlying interest.
2. Identify YOUR interests with the same discipline. Distinguish "must have" from "want" from "would be nice". The recommendation should defend the must-haves and trade the nice-to-haves.

BATNA AND LEVERAGE
3. Name your BATNA (Best Alternative To a Negotiated Agreement) explicitly. If you can't, you have no real walk-away point and you'll concede too much.
4. Estimate the OTHER SIDE's BATNA. Their willingness to concede is bounded by it. If their BATNA is bad (no other supplier, urgent deadline, political pressure to close) you have leverage they may not realise.
5. State the ZOPA (Zone of Possible Agreement) — the overlap between their walk-away and yours. If there is no ZOPA, the deal is not closeable today; the recommendation should be to change the variables (scope, timing, packaging), not to push harder on price.

ANCHORING AND OPENING MOVES
6. The first concrete number ANCHORS the negotiation. If the price/scope is well-supported, opening first is usually correct. If it isn't, ask a calibrated question first ("what does success on this look like for you?") and let the other side anchor.
7. Justify the opening number with an external standard (market benchmark, peer deal, published rate card). Anchors backed by a standard hold under counter-pressure; arbitrary anchors do not.

CONCESSIONS AND TRADES
8. Never give a concession without getting one. Every concession should be a trade — frame as "if you can do X, I can do Y". Unilateral concessions teach the counterparty that pressure works.
9. Concede on lower-value items first. Save the high-value trades for late in the conversation when the relationship is warmer and the cost of walking away is higher for both sides.

RELATIONSHIP DISCIPLINE
10. Separate the PEOPLE from the PROBLEM. Be hard on the substance, soft on the relationship. A counterparty you embarrassed today is a counterparty who will block you on the next deal — even if you "won".
11. Respect the other side's NEED TO LOOK GOOD to their internal stakeholders. Help them craft a story they can tell their boss. Deals close when both sides can defend the outcome internally.

OUTPUT DISCIPLINE FOR THIS REPLY
When the user is asking about an approach or deal, structure your reply as:
  - **Bottom line:** the recommended move in one sentence
  - **Their interests (assessed):** 2-4 bullets, with [CONFIRMED/PROBABLE/ASSESSED] tags
  - **Your interests:** must-haves vs. tradeable
  - **Your BATNA / their BATNA:** explicit, even if uncertain
  - **Recommended opening:** anchor + the external standard supporting it
  - **Pre-planned concessions:** what you can give and what you should ask for in return
  - **Risks / red flags:** what would make you walk away

THE DO NOTHING OPTION (mandatory)
12. Every recommendation involving a deal, approach, partnership, or commitment MUST present DO NOTHING as a named, evaluated option alongside the active options. Do nothing is not the absence of a decision — it is a real strategic choice with its own probability of success, opportunity cost, and second-order effects. If walking away preserves optionality, conserves resources, or sidesteps a downside that the active options carry, say so explicitly. The fact that the user asked for an "approach" or "next step" does NOT mean an active option is the right answer; sometimes the highest-value move is to wait, decline, or redirect attention elsewhere. Frame the comparison as: Option A (active move 1) / Option B (active move 2) / Option C — DO NOTHING (with explicit rationale and conditions under which it is the right choice). The recommendation MUST then state which option is preferred AND the key assumption that would have to flip for the recommendation to change.

NEVER recommend a price, term, or move that you cannot defend with explicit interests + BATNA reasoning. If you do not have enough context, ASK for it before giving the recommendation."""


def is_enabled() -> bool:
    """Feature flag — default ON. Set ARIA_NEGOTIATION_PRINCIPLES=0 to disable."""
    val = os.getenv("ARIA_NEGOTIATION_PRINCIPLES", "1") or "1"
    return val.strip().lower() not in ("0", "false", "no", "off")


def detect_negotiation_intent(message: str) -> bool:
    """Return True if the message looks like a negotiation/approach question.

    Conservative — only fires on explicit negotiation vocabulary or clearly
    framed strategy asks. General BD chatter that happens to mention "deal"
    or "approach" without one of the high-signal phrases does not trigger.
    """
    if not message or not isinstance(message, str):
        return False
    if not is_enabled():
        return False
    return bool(_NEGOTIATION_RE.search(message))


def addendum() -> str:
    """Return the negotiation-principles addendum text, or empty string if
    the feature flag is disabled. Note that callers should ALSO check
    `detect_negotiation_intent(message)` before calling this — the addendum
    is conditional, not always-on. This function only enforces the env-var
    feature flag, not the intent gate.
    """
    if not is_enabled():
        return ""
    return _NEGOTIATION_PRINCIPLES_BLOCK


def block_length() -> int:
    """For monitoring — how many chars the addendum adds when active."""
    return len(_NEGOTIATION_PRINCIPLES_BLOCK)
