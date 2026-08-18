"""
ARIA Local Brain — rule-based response engine that needs ZERO LLM calls.

Purpose
═══════
ARIA's full reasoning happens in DeepSeek (her configured brain). But when
DeepSeek is unreachable — rate-limited, network outage, key revoked — every
chat call would previously hard-fail with "ARIA encountered an internal error."

This module handles ~30% of typical defence-intelligence queries WITHOUT any
LLM call by routing to the local data layer:

  - Sanctions check         → fuzzy_screen() + OpenSanctions cache
  - Conflict situation      → ACLED/GDELT cache + correlate_with_procurement
  - Country risk            → static risk table (same as compliance/risk)
  - Weapon system explainer → tech_classifier database
  - Contact lookup          → contacts module
  - Competitor moves        → competitors module
  - Knowledge base lookup   → knowledge.search_knowledge() + neural recall
  - Intelligence ledger     → query_ledger() with recency weighting
  - "Show me X about Y"     → semantic_search

When the LLM is up, this module is also used as a TOOL — pre-fetching local
context before the prompt so the LLM has structured data to reason over.
When the LLM is DOWN, this module IS the response — degraded but still useful.

Usage
═════
    from .intel import local_brain

    # Try local-only first
    result = await local_brain.try_local_response(message)
    if result.get("answered"):
        return result["response"]

    # Fall through to LLM if local couldn't answer
    return await llm.complete(...)

Independence philosophy
═══════════════════════
Every line of this module runs without DeepSeek/Anthropic/OpenAI/Gemini.
The only external calls are to public data feeds (OpenSanctions, ACLED) which
are cached locally — and even those have local fallbacks.
"""
from __future__ import annotations
from .engine_wiring import wire_failure

import logging
import re
from typing import Any, Optional

from . import knowledge as kb
from . import intel_ledger
from . import contacts
from . import competitors
from . import neural_memory
from . import tech_classifier
from . import sanctions as fuzzy_sanctions
from . import conflict_tracker
from .wire import fail_wire  # R-F1788 §21 brain-wiring

logger = logging.getLogger("aria.local_brain")

# ── Intent patterns — local-resolvable queries ──────────────────────────────
# Each pattern → handler function. Patterns are ordered by specificity:
# more specific patterns checked first, more general ones last.

_PATTERNS: list[tuple[re.Pattern, str]] = [
    # Sanctions screening
    (re.compile(r"\b(?:is|are)\s+(.+?)\s+(?:on\s+the\s+)?(?:sanction(?:ed|s\s+list)?|ofac|ofsi|embargo(?:ed)?)", re.I), "sanctions"),
    (re.compile(r"\b(?:screen|check)\s+(?:if\s+)?(.+?)\s+(?:is\s+)?(?:sanctioned|on\s+(?:any\s+)?sanctions?\s+list)", re.I), "sanctions"),
    (re.compile(r"\bsanctions?\s+(?:check|screen|status)\s+(?:on|for|of)\s+(.+)", re.I), "sanctions"),

    # Conflict / kinetic events
    (re.compile(r"\bwhat'?s?\s+happening\s+in\s+([\w\s\-]+?)(?:\?|$|\s+now|\s+today|\s+lately)", re.I), "conflict"),
    (re.compile(r"\b(?:security|conflict|situation|escalation|violence)\s+(?:in|of|for)\s+([\w\s\-]+?)(?:\?|$|\s+(?:right\s+)?now)", re.I), "conflict"),
    (re.compile(r"\bis\s+([\w\s\-]+?)\s+(?:safe|stable|escalating|in\s+conflict)", re.I), "conflict"),

    # Country risk
    (re.compile(r"\b(?:country\s+)?risk\s+(?:level\s+)?(?:for|of|in)\s+([\w\s\-]+)", re.I), "country_risk"),
    (re.compile(r"\bhow\s+risky\s+is\s+([\w\s\-]+?)(?:\?|$)", re.I), "country_risk"),

    # Weapon system explainer
    (re.compile(r"\bwhat\s+is\s+(?:a|an|the)\s+([\w\-]+(?:\s+[\w\-]+){0,3})\s*\??$", re.I), "tech_explain"),
    (re.compile(r"\b(?:tell\s+me\s+about|specs?\s+(?:of|for)|specifications?\s+of)\s+(?:the\s+)?([\w\-]+(?:\s+[\w\-]+){0,3})", re.I), "tech_explain"),

    # Contact lookup
    (re.compile(r"\b(?:who\s+do\s+(?:we|i|aria)\s+know|contacts?|humint)\s+(?:in|for)\s+([\w\s\-]+?)(?:\?|$)", re.I), "contacts"),
    (re.compile(r"\bwho\s+is\s+(?:our|the)\s+(?:contact|person|liaison)\s+(?:in|for|at)\s+([\w\s\-]+)", re.I), "contacts"),

    # Competitor moves
    (re.compile(r"\b(?:what\s+(?:is|are))?\s*(?:our\s+)?competitors?\s+(?:doing|up\s+to|moves?)\s+(?:in|for)?\s*([\w\s\-]*)", re.I), "competitors"),

    # Ledger / signal queries
    (re.compile(r"\b(?:recent|latest|hot|new)\s+(?:signals?|intel|news|updates?)\s+(?:on|about|for|in)\s+(.+)", re.I), "ledger"),

    # Tech classification of free text
    (re.compile(r"\bclassify\s+(?:this|these)\s*[:\-]?\s*(.+)", re.I | re.S), "classify"),
]

# R-F511 jurisdiction-qualifier regex — promoted to module scope by R-F517,
# expanded with embargoed/sanctioned destination countries by R-F524.
#
# When intent=="sanctions" and the user names ANY recognised jurisdiction
# (regulator, regulator-country, or sanctioned destination country) we yield
# to the LLM so clause 26 can fire with per-regime per-jurisdiction answer.
#
# R-F524 (2026-05-14) — live WhatsApp 13:55-13:57 BST:
#   "Is Yemen under sanctions?" — local_brain regex captured "Yemen under"
#   as entity → fuzzy_screen 0 matches → "Sanctions screen — Yemen under"
#   wrong-entity headline. Yemen wasn't in the pre-R-F524 qualifier list
#   (which only covered major regulators + Western/G7 jurisdictions).
#   "which Government party is under sanctions in Yemen?" — captured just
#   "under" as entity → fuzzy matches "Under Secretary", "UNDER TECHNOLOGIES
#   INC.", etc. → garbage answer served.
#
# Fix: add destination countries that commonly come up in sanctions/embargo
# questions — UN/EU/OFSI/OFAC embargo targets plus broad-question regional
# names. The list is intentionally wider than just "embargoed today" because
# the FIX is the yield-to-LLM; false-positive yields just trade local-brain
# speed for an LLM call, never produce wrong answers.
_JURISDICTION_QUALIFIER = re.compile(
    r"\b(?:"
    # Regulators
    r"OFSI|OFAC|SDN|SECO|DFAT|MoFA|FCDO|"
    r"UN(?:\s+Security\s+Council|SC)?|"
    r"Entity\s+List|MilCorps|DDTC|"
    # Western / G7
    r"UK|U\.K\.|United\s+Kingdom|Britain|British|"
    r"US|U\.S\.|USA|United\s+States|American|"
    r"EU|European\s+Union|"
    r"Australia|Australian|"
    r"Japan|Japanese|"
    r"Switzerland|Swiss|"
    r"Norway|Norwegian|"
    r"Canada|Canadian|"
    r"Germany|German|"
    r"France|French|"
    r"Israel|Israeli|"
    # Major non-Western players
    r"China|Chinese|PRC|"
    r"Russia|Russian|"
    r"Iran|Iranian|"
    r"North\s+Korea|DPRK|"
    # R-F524 — UN/EU/OFSI/OFAC embargoed or comprehensively-sanctioned
    # destination countries. Operator routinely asks "is X sanctioned?"
    # about these as part of pre-deal screening.
    r"Yemen|Yemeni|"
    r"Syria|Syrian|"
    r"Libya|Libyan|"
    r"Sudan|Sudanese|"
    r"South\s+Sudan|"
    r"Somalia|Somali|"
    r"Myanmar|Burma|Burmese|"
    r"Lebanon|Lebanese|Hezbollah|"
    r"Iraq|Iraqi|"
    r"Afghanistan|Afghan|Taliban|"
    r"Eritrea|Eritrean|"
    r"DRC|Democratic\s+Republic\s+of\s+(?:the\s+)?Congo|Congolese|"
    r"CAR|Central\s+African\s+Republic|"
    r"Mali|Malian|"
    r"Burkina\s+Faso|"
    r"Niger|Nigerien|"
    r"Cuba|Cuban|"
    r"Venezuela|Venezuelan|"
    r"Nicaragua|Nicaraguan|"
    r"Belarus|Belarusian|"
    r"Zimbabwe|Zimbabwean|"
    # R-F524 — additional countries that operators commonly screen against
    # (high traffic in defence/dual-use trade, often subject to regime-
    # specific restrictions even when not under blanket embargo).
    r"Saudi\s+Arabia|Saudi|"
    r"UAE|United\s+Arab\s+Emirates|Emirati|"
    r"Egypt|Egyptian|"
    r"Turkey|Turkish|Turkiye|"
    r"Pakistan|Pakistani|"
    r"India|Indian|"
    r"Brazil|Brazilian|"
    r"Angola|Angolan|"
    r"Mozambique|Mozambican|"
    r"Nigeria|Nigerian|"
    r"Ethiopia|Ethiopian|"
    r"Kenya|Kenyan|"
    r"Ukraine|Ukrainian|"
    r"Hong\s+Kong|"
    r"Taiwan|Taiwanese"
    r")\b",
    re.IGNORECASE,
)


# ── Output formatters ──────────────────────────────────────────────────────

def _fmt_sanctions(name: str, result: dict) -> str:
    # ── R-F3690 — never-false-clean on the LOCAL-BRAIN fast path ────────────
    #
    # This printed a green tick and "No matches in ... 200+ lists" whenever
    # `matches` was falsy, without ever reading `screened`, `source_unavailable`
    # or `error`. Breaker-open, quota-exhausted-with-an-empty-local-store, and
    # `not_entity_shaped` all rendered as an authoritative clearance naming 200+
    # lists that were never queried.
    #
    # It matters more here than anywhere else: local_brain answers WITHOUT the
    # LLM, so it bypasses `sanctions_claim_guard` entirely — that guard only
    # shapes LLM context (aria_engine.py:4156, :5090). This is the R-F2143
    # contract re-opened on a different entry point, so the check has to live
    # in the formatter itself.
    _screened = (
        isinstance(result, dict)
        and result.get("screened") is True
        and not result.get("error")
        and not result.get("source_unavailable")
    )
    if not _screened:
        _why = (result.get("error") or "source unavailable") if isinstance(result, dict) else "no result"
        return (
            f"⚠️ *Sanctions screen — {name}: COULD NOT VERIFY*\n\n"
            f"The screen did NOT run ({str(_why)[:80]}). This is *not* a clean result "
            f"and must not be read as one.\n\n"
            f"_No list was queried. Re-run once screening is available, or screen "
            f"manually against OFAC/OFSI/EU/UN before acting._"
        )
    if not result.get("matches"):
        return (
            f"✅ *Sanctions screen — {name}*\n\n"
            f"No matches in OpenSanctions consolidated dataset (OFAC/OFSI/EU/UN/200+ lists).\n"
            f"Variants checked: {len(result.get('variants_tried', []))}\n\n"
            f"_Pre-screen only — verify against authoritative sources before action._"
        )
    blocked = result.get("blocked", False)
    head = "⛔" if blocked else "⚠️"
    lines = [f"{head} *Sanctions screen — {name}*", ""]
    if blocked:
        lines.append(f"*BLOCKING MATCH* — top score {result.get('top_score', 0)}")
    else:
        lines.append(f"Possible matches found — top score {result.get('top_score', 0)}")
    lines.append("")
    for m in (result.get("matches") or [])[:5]:
        lines.append(f"• *{m.get('name','?')}* — {m.get('list','?')} (score {m.get('score',0)})")
        if m.get("countries"):
            lines.append(f"  Country: {', '.join(m['countries'][:3])}")
    lines.append("")
    lines.append("_Source: OpenSanctions. Manual verification required._")
    return "\n".join(lines)


def _fmt_conflict(country: str, summary: dict) -> str:
    if summary.get("total_events", 0) == 0:
        return f"🟢 *{country}* — no kinetic events in the recorded window. Country appears quiet (or out of ACLED/GDELT coverage)."
    label = summary.get("escalation_label", "?")
    score = summary.get("escalation_score", 0)
    icon = {"CRITICAL": "🔴", "HIGH": "🟠", "MODERATE": "🟡", "LOW": "🟢"}.get(label, "⚪")
    lines = [
        f"{icon} *{country}* — escalation {score}/100 ({label})",
        "",
        f"Events ({summary.get('days', '?')}d): {summary.get('total_events', 0)} | Fatalities: {summary.get('fatalities', 0)} | Momentum: {summary.get('momentum', '?')}",
    ]
    if summary.get("by_type"):
        top_types = list(summary["by_type"].items())[:4]
        lines.append("Event mix: " + ", ".join(f"{k} {v}" for k, v in top_types))
    if summary.get("top_locations"):
        lines.append("Hotspots: " + ", ".join(l["location"] for l in summary["top_locations"][:3]))
    lines.append("")
    if summary.get("recent_events"):
        lines.append("*Most recent:*")
        for e in summary["recent_events"][:3]:
            lines.append(f"• [{e.get('date','?')}] {e.get('type','?')} — {(e.get('notes') or '')[:140]}")
    return "\n".join(lines)


def _fmt_tech(designation: str, info: dict) -> str:
    if not info.get("found"):
        partial = info.get("partial_matches", [])
        if partial:
            return f"❓ *{designation}* not in technical database. Closest: {', '.join(partial[:5])}"
        return f"❓ *{designation}* not in ARIA's weapon-system database. Try /code to teach me."
    lines = [f"🛠 *{designation}*"]
    if info.get("type"):     lines.append(f"Type: {info['type']}")
    if info.get("country"):  lines.append(f"Country: {info['country']}")
    if info.get("oem"):      lines.append(f"OEM: {info['oem']}")
    if info.get("ml"):       lines.append(f"UK ML category: {info['ml']}")
    if info.get("eccn"):     lines.append(f"US ECCN: {info['eccn']}")
    if info.get("controlled"): lines.append("⚠️ Export-controlled item")
    if info.get("embargo_risk"):
        lines.append(f"⛔ Embargo risk: {info['embargo_risk']}")
    if info.get("mtcr"):     lines.append(f"MTCR: {info['mtcr']}")
    return "\n".join(lines)


def _fmt_contacts(country: str) -> str:
    """Look up contacts in the local module — pure data, no LLM."""
    try:
        ctx = contacts.get_contact_context(country)
        if not ctx or not ctx.strip():
            return f"ℹ️ No contacts on file for {country}. Add via /api/aria/contacts."
        return f"*Contacts — {country}*\n\n{ctx[:2000]}"
    except Exception as e:
        logger.warning("local_brain contacts lookup failed: %s", e)
        return f"⚠️ Contact lookup failed: {e}"


def _fmt_competitors(market: str) -> str:
    try:
        ctx = competitors.get_competitor_context(market or "")
        if not ctx or not ctx.strip():
            return f"ℹ️ No competitor moves recorded{' for ' + market if market else ''}."
        return f"*Competitor activity{' — ' + market if market else ''}*\n\n{ctx[:2000]}"
    except Exception as e:
        logger.warning("local_brain competitors lookup failed: %s", e)
        return f"⚠️ Competitor lookup failed: {e}"


def _fmt_ledger(query: str) -> str:
    try:
        result = intel_ledger.query_ledger(query)
        if not result or not result.strip():
            return f"ℹ️ No recent signals matching '{query}'. ARIA's ledger is empty for this topic."
        return f"*Ledger signals — {query}*\n{result[:2200]}"
    except Exception as e:
        logger.warning("local_brain ledger lookup failed: %s", e)
        return f"⚠️ Ledger query failed: {e}"


def _fmt_country_risk(country: str) -> str:
    """Static country risk table — exact same logic as /api/aria/compliance/risk."""
    EMBARGOED = {"RU","BY","IR","KP","SY","CU","VE","CF","CD","ER","IQ","LY","ML","SO","SS","SD","YE","AF","HT","MM","NI","ZW","CN"}
    HIGH_RISK = {"GW","CM","NE","BF","TD","PK","EG","TR","IN","ET"}
    MEDIUM_RISK = {"AO","MZ","NG","SN","CI","UG","ID","VN","CO","PE","SA","AE","JO"}
    NAME_TO_ISO = {
        "russia":"RU","belarus":"BY","iran":"IR","north korea":"KP","syria":"SY",
        "cuba":"CU","venezuela":"VE","drc":"CD","congo":"CD","libya":"LY","mali":"ML",
        "somalia":"SO","sudan":"SD","yemen":"YE","afghanistan":"AF","myanmar":"MM",
        "burma":"MM","zimbabwe":"ZW","china":"CN","angola":"AO","mozambique":"MZ",
        "nigeria":"NG","senegal":"SN","cameroon":"CM","niger":"NE","chad":"TD",
        "burkina faso":"BF","pakistan":"PK","egypt":"EG","turkey":"TR","ethiopia":"ET",
        "guinea-bissau":"GW","cape verde":"CV","ivory coast":"CI","côte d'ivoire":"CI",
        "uganda":"UG","indonesia":"ID","vietnam":"VN","colombia":"CO","peru":"PE",
        "saudi arabia":"SA","uae":"AE","jordan":"JO",
    }
    iso = country.upper() if len(country) == 2 else NAME_TO_ISO.get(country.lower().strip(), country.upper()[:2])
    if iso in EMBARGOED:
        return f"🔴 *{country}* — HIGH risk\n\nUnder international arms embargo. Most defence exports prohibited.\nRegimes: UN SC, EU, UK OFSI."
    if iso in HIGH_RISK:
        return f"🟠 *{country}* — HIGH risk\n\nEnhanced due diligence required. End-user verification + diversion risk assessment + SITCL likely required."
    if iso in MEDIUM_RISK:
        return f"🟡 *{country}* — MEDIUM risk\n\nStandard defence exports permitted but SITCL with end-use certificate required."
    return f"🟢 *{country}* — LOW risk\n\nLow-risk destination. Standard export controls apply."


# ── Public API ──────────────────────────────────────────────────────────────

@fail_wire(module="local_brain", gap_type="engine_failure")
async def try_local_response(message: str, *,
                             exclude_topic: str | None = None) -> dict:
    """Attempt to answer a message using ONLY local data. No LLM call.

    Args:
        exclude_topic: when set, RAG retrieval will exclude facts whose topic
            starts with this prefix. Used by self_quiz to prevent quiz-gaming
            (retrieving the quizzed case's own answer).

    Returns:
        {
            "answered": bool,
            "response": str | None,
            "intent": str | None,
            "source": "local_brain",
            "degraded": bool,  # True if this is a fallback from a failed LLM
            "rag_context": str | None,  # R-F1743: reasoning-only context
        }
    """
    if not message or len(message.strip()) < 3:
        return {"answered": False, "response": None, "intent": None}

    msg = message.strip()

    # YIELD when a tool has already run. chat_ep appends a
    # [TOOL: <name>] / [I have already run the appropriate tool]
    # block containing the tool output to the message before handing
    # it to aria_chat. local_brain must NOT pattern-match against
    # that combined text and return a parallel local answer — doing
    # so discards the tool result entirely. This bug was observed on
    # a Serban Industries SRL DD run where dd_orchestrator.ran
    # successfully, built a full ARK-DD markdown report, and then
    # local_brain's sanctions pattern matched the user's "sanctions
    # check on the company" phrase and hijacked the response with a
    # noise match on "the company". Local_brain's job is to resolve
    # single-pattern queries the cloud LLM would otherwise waste
    # tokens on — NEVER to override a tool that already ran.
    if "[TOOL:" in msg or "[I have already run the appropriate tool" in msg:
        return {"answered": False, "response": None, "intent": None}

    # R-F514 (2026-05-14) — YIELD when the message carries the
    # comprehension or scratchpad pre-prompt. chat_ep prepends a
    # `[COMPREHENSION PASS — Clause 21...]` block (and appends a
    # `[CLAUSE 22 — Think Before Speak]` block) before handing
    # message_for_llm to aria_chat → reasoning_router →
    # try_local_response. The comprehension prefix at
    # comprehension.py:415-417 literally contains the substring
    # "is HIGH-STAKES and you are NOT 100% confident...sanctions
    # status" — exactly the shape the sanctions regex below tries
    # to capture. Without this guard the regex matches "is HIGH-
    # STAKES" and lazily captures up to the next "sanctions"
    # keyword IN THE SAME PREFIX, producing a ~150-char garbage
    # entity name ("HIGH-STAKES and you are NOT 100% confident,
    # state what you would need to confirm — do NOT fabricate
    # verifiable facts (jurisdictions, financials,") that becomes
    # the headline of a bogus sanctions screen. Live failure 2026-
    # 05-14 verified on /chat — the same prefix-leak shape has
    # likely been firing on every non-trivial /chat sanctions query
    # since the comprehension pass landed (0c641c3, 2026-04-18).
    # The cleanest fix is to never run local_brain's patterns when
    # the prompt prefix is present — those messages are by
    # definition non-trivial and worth the LLM round-trip anyway
    # (comprehension prefix is built only for non-trivial inputs
    # per chat_ep:6499 `if not _comp_analysis.is_trivial`). The
    # /chat/stream path does not have this issue (no prefix
    # prepended) so it is unaffected.
    if (
        "[COMPREHENSION PASS" in msg
        or "[END COMPREHENSION PASS]" in msg
        or "USER MESSAGE FOLLOWS:" in msg
        or "[CLAUSE 22 — Think Before Speak]" in msg
        or "<scratchpad>" in msg
    ):
        return {"answered": False, "response": None, "intent": None}

    # YIELD to the DD orchestrator when the message asks for a full
    # due-diligence run. local_brain resolves single-tool local queries
    # cheaply (sanctions screen, country risk, tech explain, etc.) and
    # returns immediately on first pattern hit — but a message that
    # ALSO contains a "DD on X" request should fall through to the
    # main chat pipeline where _detect_tool_intent routes to
    # dd_orchestrator. Without this guard, a compound message like
    # "run a full DD on X; please also screen the company for
    # sanctions" short-circuits into a standalone sanctions screen
    # and the DD never fires. Observed on a real Serban Industries
    # SRL run 2026-04-11 where the sanctions screen matched "the
    # company" as the entity name and returned garbage.
    _DD_YIELD_RE = re.compile(
        r"\b(?:"
        r"(?:full\s+|comprehensive\s+|ark[\-_]?)?dd\s+(?:report\s+)?(?:on|for|about)\s+|"
        r"due\s+diligence\s+(?:on|for|about)\s+|"
        r"ark[\-_]?dd\s+(?:on|for|about)\s+|"
        r"orchestrate\s+dd\s+(?:on|for|about)\s+|"
        r"run\s+a?\s*(?:full\s+)?(?:background|compliance|dd)\s+(?:check|report)\s+(?:on|for|about)\s+"
        r")\S",
        re.IGNORECASE,
    )
    if _DD_YIELD_RE.search(msg):
        return {"answered": False, "response": None, "intent": None}

    # Generic-pronoun / self-reference captures that should never be
    # treated as an entity name. These show up when a user writes
    # "screen the company" / "check it" / "run a check on them" —
    # the sanctions pattern captures the pronoun and fires a
    # meaningless screen. Reject at the argument-validation step.
    _INVALID_ENTITY_CAPTURES = {
        "it", "them", "they", "the company", "the entity", "the broker",
        "the supplier", "the buyer", "the seller", "this company",
        "this entity", "this broker", "this supplier", "this buyer",
        "this seller", "that company", "that entity", "us", "we",
        "the end user", "the end-user", "the counterparty",
        "the intermediary", "the party", "the other party", "all of them",
        "all of it", "everyone", "anyone", "the subject", "subject",
    }

    # R-F517: _JURISDICTION_QUALIFIER moved to module scope; see definition
    # near _PATTERNS. The R-F511 commit message explains the live failure
    # (2026-05-14 08:55 BST Hikvision/UK) that drives the yield-to-LLM path.

    # R-F1740/R-F1743: Semantic RAG retrieval — query the knowledge base for
    # facts relevant to this question BEFORE pattern matching. This makes
    # ingested facts (OFAC SDN, UK OFSI, EU/UN sanctions) available to the
    # local reasoning stack during self_quiz, so coverage feeds mastery.
    # The retrieved facts are stored in _rag_context as a separate field
    # (NOT appended to the visible response) for quiz-mode reasoning use.
    # When exclude_topic is set, facts whose topic starts with that prefix
    # are filtered out to prevent quiz-gaming.
    _rag_context: str | None = None
    try:
        from .rag_store import search as _rag_search
        _rag_results = await _rag_search(msg, top_k=5, min_similarity=0.4)
        if _rag_results:
            _rag_parts = []
            for r in _rag_results[:5]:
                text = (r.get("text") or "").strip()
                score = r.get("score", 0)
                if not text or len(text) < 50:
                    continue
                # R-F1743: guard against quiz-gaming — exclude the quizzed
                # case's own topic from RAG retrieval so the local stack
                # must reason from domain knowledge, not echo the answer.
                topic = (r.get("topic") or "").strip()
                if exclude_topic and topic.startswith(exclude_topic):
                    continue
                _rag_parts.append(f"[RAG: {text[:300]} (score={score:.2f})]")
            if _rag_parts:
                _rag_context = "\n".join(_rag_parts)
    except Exception as _rag_err:
        logger.debug("[R-F1740] RAG retrieval failed (non-fatal): %s", _rag_err)

    for pattern, intent in _PATTERNS:
        m = pattern.search(msg)
        if not m:
            continue
        try:
            arg = (m.group(1) if m.groups() else "").strip().rstrip(".,?!;:")
            if not arg or len(arg) < 2:
                continue
            # Skip pronoun / self-reference captures on intent types
            # where the entity name matters (sanctions/screen/contacts)
            if intent in ("sanctions", "contacts") and arg.lower() in _INVALID_ENTITY_CAPTURES:
                continue

            if intent == "sanctions":
                # R-F511 — yield jurisdiction-scoped questions to LLM.
                # See _JURISDICTION_QUALIFIER comment above. Without this,
                # "Is Hikvision sanctioned in the UK?" gets a flat multi-
                # regime BLOCKING MATCH that confuses procurement
                # restrictions and Companies House presence with actual
                # OFSI listing, and R-F510 clause 26 never runs.
                if _JURISDICTION_QUALIFIER.search(msg):
                    return {"answered": False, "response": None, "intent": None}
                result = await fuzzy_sanctions.fuzzy_screen(arg)
                _resp = _fmt_sanctions(arg, result)
                # R-F1743: RAG context is passed as a separate field for
                # reasoning/quiz use, NOT appended to the visible response.
                # The raw [RAG:...] dump polluted the user-facing answer and
                # inflated similarity scores in self_quiz.
                return {
                    "answered": True,
                    "response": _resp,
                    "rag_context": _rag_context,  # reasoning-only, not visible
                    "intent": intent,
                    "source": "local_brain",
                }

            if intent == "conflict":
                result = await conflict_tracker.get_recent_events(arg, days=30, limit=15)
                return {
                    "answered": True,
                    "response": _fmt_conflict(arg, result),
                    "intent": intent,
                    "source": "local_brain",
                }

            if intent == "country_risk":
                return {
                    "answered": True,
                    "response": _fmt_country_risk(arg),
                    "intent": intent,
                    "source": "local_brain",
                }

            if intent == "tech_explain":
                info = tech_classifier.explain_item(arg)
                if info.get("found") or info.get("partial_matches"):
                    return {
                        "answered": True,
                        "response": _fmt_tech(arg, info),
                        "intent": intent,
                        "source": "local_brain",
                    }
                continue  # not in DB — fall through

            if intent == "contacts":
                return {
                    "answered": True,
                    "response": _fmt_contacts(arg),
                    "intent": intent,
                    "source": "local_brain",
                }

            if intent == "competitors":
                return {
                    "answered": True,
                    "response": _fmt_competitors(arg),
                    "intent": intent,
                    "source": "local_brain",
                }

            if intent == "ledger":
                return {
                    "answered": True,
                    "response": _fmt_ledger(arg),
                    "intent": intent,
                    "source": "local_brain",
                }

            if intent == "classify":
                result = tech_classifier.classify_text(arg)
                lines = [f"📋 *Classification result*", ""]
                lines.append(f"Summary: {result.get('raw_summary')}")
                if result.get("ml_categories"):
                    lines.append(f"ML categories: {', '.join(result['ml_categories'])}")
                if result.get("controlled_items_count", 0) > 0:
                    lines.append(f"Controlled items: {result['controlled_items_count']}")
                if result.get("embargo_risks"):
                    lines.append(f"⚠️ Embargo risks: {'; '.join(result['embargo_risks'])}")
                return {
                    "answered": True,
                    "response": "\n".join(lines),
                    "intent": intent,
                    "source": "local_brain",
                }
        except Exception as e:
            logger.warning("local_brain handler %s failed: %s", intent, e)
            # R-F1745 — wire failure to brain so the coder can see broken intents
            try:
                from .engine_wiring import wire_failure as _wf
                _wf(module="local_brain", detail=f"handler {intent} failed: {e}",
                    gap_type="no_symbolic_rule", source="local_brain:try_local_response")
            except Exception:
                pass
            continue

    return {"answered": False, "response": None, "intent": None}


@fail_wire(module="local_brain", gap_type="engine_failure")
async def degraded_response(message: str, reason: str = "LLM unavailable") -> dict:
    """Build a degraded response when no LLM is available.

    Tries:
      1. try_local_response() for rule-routable queries
      2. Falls back to a multi-source local intelligence brief built from
         knowledge base + neural recall + ledger + sanctions cache

    Always returns SOMETHING — even if just a "ARIA is in degraded mode but
    here's what I know from local data" report. Never raises.
    """
    # Counter — every invocation is a signal that the LLM chain failed.
    # High-frequency invocation is the early warning that provider keys /
    # billing / network are degraded. Fire-and-forget. Added 2026-04-17
    # as part of the "never dies" resilience pass.
    try:
        from . import redis_store as _rs
        import asyncio as _asyncio
        async def _bump():
            try:
                current = await _rs.get("crucix:local_brain:invocations_24h")
                n = int(current) if current is not None else 0
                await _rs.set("crucix:local_brain:invocations_24h", n + 1, ex=86400)
            except Exception:
                pass
        try:
            _asyncio.get_running_loop().create_task(_bump())
        except RuntimeError:
            pass
    except Exception:
        pass
    # R-F1745 — wire degraded mode activation to brain
    try:
        from .engine_wiring import wire_failure as _wf
        _wf(module="local_brain", detail=f"Degraded mode: {reason}",
            gap_type="source_failure", source="local_brain:degraded_response")
    except Exception:
        pass

    # Step 1: Try the deterministic rule router
    local = await try_local_response(message)
    if local.get("answered"):
        local["degraded"] = True
        local["degradation_reason"] = reason
        local["response"] = (
            f"⚠️ _ARIA in degraded mode ({reason}). Answering from local data only — "
            f"no reasoning, just retrieval._\n\n{local['response']}"
        )
        return local

    # Step 2: Multi-source local brief — best-effort retrieval from every layer
    sections: list[str] = []
    sections.append(f"⚠️ *ARIA degraded mode* — {reason}")
    sections.append("Cannot reason without my brain. Here is what I have in local storage on this topic:\n")

    # Knowledge base lexical search
    try:
        # R-F4141 (C-171) — 2.28s O(corpus) scan; must not run on the loop.
        import asyncio as _aio
        kb_hit = await _aio.to_thread(kb.search_knowledge, message)
        if kb_hit and kb_hit.strip():
            sections.append(kb_hit[:1500])
    except Exception as e:
        logger.debug("local_brain kb search failed: %s", e)

    # Neural memory associative recall
    try:
        neural = await neural_memory.recall(message, depth=2, max_results=10)
        if neural and neural.get("neurons"):
            lines = ["*Neural associations*:"]
            for n in neural["neurons"][:8]:
                conn = ", ".join(c["concept"] for c in n.get("connections", [])[:3])
                lines.append(f"• {n['concept']} ({n['category']}) — linked: {conn}")
            sections.append("\n".join(lines))
    except Exception as e:
        logger.debug("local_brain neural recall failed: %s", e)

    # Intel ledger time-weighted
    try:
        ledger = intel_ledger.query_ledger(message)
        if ledger and ledger.strip():
            sections.append(ledger[:1500])
    except Exception as e:
        logger.debug("local_brain ledger query failed: %s", e)

    sections.append("")
    sections.append("_Restore my brain for full reasoning. Use /independence to check status._")

    return {
        "answered": True,
        "response": "\n\n".join(sections),
        "intent": "degraded_brief",
        "source": "local_brain",
        "degraded": True,
        "degradation_reason": reason,
    }


@fail_wire(module="local_brain", gap_type="engine_failure")
def get_capability_surface() -> dict:
    """Report what local_brain can answer without any LLM call."""
    # R-F996 — wire to brain
    from .engine_wiring import wire_success
    wire_success(
        module="local_brain",
        summary="Get Capability Surface",
        source_id="local_brain:R-F996",
    )

    return {
        "intents_supported": [
            "sanctions_screen",
            "conflict_situation",
            "country_risk",
            "weapon_system_explain",
            "contact_lookup",
            "competitor_moves",
            "intel_ledger_query",
            "tech_text_classification",
        ],
        "data_sources": [
            "OpenSanctions (cached)",
            "ACLED + GDELT (cached 6h)",
            "Static country risk table",
            "Tech classifier database (60+ weapon systems)",
            "Local contacts module",
            "Competitor moves module",
            "Intel ledger (30d rolling)",
            "Knowledge base (Redis)",
            "Neural memory (Redis)",
        ],
        "llm_calls_required": 0,
        "fallback_for_when": "DeepSeek/main LLM unreachable, rate-limited, or unconfigured",
    }

# R-F2538: R-F2119 import-time wire_failure("module shutdown") block removed — it fired a FALSE engine_failure gap on every import (not at shutdown); do not re-add.
