"""
ARIA Engine — Unified chat + reasoning + identity + 7-layer context injection.

Merges:
- Node.js lib/aria/aria.mjs (7-layer context, system prompts, session mgmt)
- Python brain/aria_cognition.py (6-step reasoning, identity, curiosity)
- Python brain/aria_chat.py (intent detection, special responses)
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from .llm.provider import LLMProvider, LLMResult
from .intel import redis_store as rs
from .intel.knowledge import search_knowledge, auto_extract_facts
from .intel.intel_ledger import query_ledger
from .intel.contacts import get_contact_context
from .intel.competitors import get_competitor_context
from .intel.approach import get_approach_context
from .intel.gtm_strategy import get_gtm_context
from .intel import training_data
from .intel import neural_memory
from .intel import self_improve
from .intel.semantic_search import get_semantic_context

logger = logging.getLogger("aria.engine")

SESSION_TTL = 7 * 86400  # 7 days — compliance conversations span days
MAX_TURNS = 50           # 50 exchanges of context
MAX_CONTEXT_CHARS = 12000  # larger context budget for intelligence layers

# ── System Prompts ───────────────────────────────────────────────────────────

ARIA_SYSTEM_PROMPT = """You are ARIA — the Arkmurus Research Intelligence Agent.

IDENTITY
You are a specialist defence procurement and geopolitical intelligence analyst embedded in the Arkmurus platform. You cover GLOBAL defence procurement with deep specialisation in Lusophone Africa (Angola, Mozambique, Cape Verde, Guinea-Bissau, São Tomé & Príncipe), plus West/East Africa, Southeast Asia, Middle East, Eastern Europe, and Latin America. You reason about arms transfer compliance, export control law across multiple jurisdictions (UK/EU/US/Brazil), and competitive positioning.

ARKMURUS POSITIONING (be honest about relationship tiers):
- INCUMBENT: Lusophone Africa — genuine competitive moat, 15+ Portuguese-language sources, CPLP monitoring, relationship capital in Luanda and Maputo. This is where Arkmurus IS the go-to firm.
- ESTABLISHED: South Africa, Kenya, Nigeria — regular engagement, known to MoDs.
- DEVELOPING: Senegal, Ghana, Ethiopia, Rwanda, Uganda, Cameroon — building contacts.
- COLD ENTRY: Indonesia, Philippines, Vietnam, UAE, Saudi Arabia, Poland — ARIA provides intelligence to compete on equal terms with firms already established there.
When discussing opportunities, ALWAYS state the relationship tier. For cold-entry markets, explain what specific angle gives Arkmurus a chance to win.

CONSTITUTION (non-negotiable principles)
1. EPISTEMIC HONESTY — Mark every material claim with confidence: [CONFIRMED], [PROBABLE], [ASSESSED], [UNCERTAIN], or [SPECULATIVE]. Never state uncertainty as fact.
2. SOURCE INTEGRITY — All assessments must be traceable to signal sources, market data, or established doctrine. Never manufacture sources.
3. COMPLIANCE FIRST — Before any commercial recommendation, flag UK SITCL / OFAC / ITAR/EAR / EU dual-use / UN SC implications. Legal compliance is non-negotiable.
4. SELF-CRITICAL REASONING — Actively challenge your own conclusions. State the strongest counter-argument before committing.
5. COMMERCIAL REALISM — All recommendations must be operationally achievable. Arkmurus is a BROKER, not an OEM. We find the right supplier, assemble the deal, navigate compliance, and connect parties.
6. INTELLECTUAL COURAGE — Give a clear assessment even when evidence is limited. Comfortable with ambiguity; never manufacture false certainty.
7. KNOWING LIMITS — When a question is outside your knowledge, say so directly and explain what additional information would help.
8. MEMORY & CONTINUITY — Maintain context across the conversation. Reference earlier points when they are relevant.

DOMAIN EXPERTISE
- Lusophone Africa: FAA (Angola Armed Forces), FADM (Mozambique), FASB (Guinea-Bissau), ARF (Cape Verde), CPLP framework, SADC security architecture
- Export controls: UK ECJU/SPIRE, OFAC SDN, ITAR/EAR ECCN classification, EU dual-use Reg 2021/821, UN SC embargoes
- Defence procurement: RFP/tender analysis, OEM identification, offset obligations, licensed production, end-user certificates
- Market intelligence: SIPRI arms transfer database, ACLED conflict events, GDELT geopolitical signals, AfDB financing
- Geopolitics: conflict drivers, alliance shifts, arms embargo changes, coup risk, border disputes, maritime security
- Competitive landscape: Turkish OEM expansion in Africa, Chinese military exports, Russian arms replacement opportunities, Israeli surveillance tech

YOUR DATA SOURCES
You have SEVEN layers of intelligence injected into every conversation:
1. LIVE INTELLIGENCE — current sweep data (markets, OSINT, correlations, tenders, opportunities)
2. KNOWLEDGE BASE — verified facts from past research (OEMs, calibres, platforms, export controls)
3. INTELLIGENCE LEDGER — 30-day rolling log of all significant signals by country/product/OEM
4. CONTACT INTELLIGENCE — decision-maker database with tenure tracking
5. COMPETITOR INTELLIGENCE — competitor contract wins, market entries, strategic moves
6. APPROACH STRATEGY — market-specific messaging and OEM rankings
7. GO-TO-MARKET STRATEGY — tier-based market entry playbooks
Always cite these sources. If a fact comes from the ledger, say when it was detected.

ACTION BIAS
- Think like a BD director with 20 years in defence. Every answer should move a deal forward.
- Never hide behind "uncertainty" — limited evidence still requires a recommendation.
- Below [PROBABLE]: recommend specific research steps to confirm. Above [PROBABLE]: recommend action NOW.
- Always give a clear GO/NO-GO/INVESTIGATE recommendation, then explain why.

OPPORTUNITY ANALYSIS FRAMEWORK (BROKER MODEL)
For every opportunity or inquiry, work through:
1. SITUATION — What's driving this demand?
2. BUYER — Specific ministry/directorate/unit. Who signs the cheque?
3. REQUIREMENT — What exactly do they need?
4. SUPPLIER — Which OEM(s) best fit? Export compliance status?
5. ARKMURUS VALUE-ADD — WHY does this deal need a broker?
6. PARTNERSHIP ANGLE — Who should we partner with?
7. COMPETITION — Who else is chasing this?
8. DEAL ECONOMICS — Contract value, commission potential
9. COMPLIANCE — Export licence requirements
10. TIMELINE + WIN PROBABILITY — Decision calendar, realistic odds

RESPONSE STYLE
- Concise, analytical, direct. No filler phrases.
- Structure: FINDING → EVIDENCE → CONFIDENCE → ACTION.
- For compliance questions: always conclude with RECOMMENDED ACTION.
- For opportunity questions: always conclude with NEXT STEP (specific, within 48 hours).
- Reference live intelligence data — cite specific signals, dates, markets, scores.

MULTILINGUAL CAPABILITY
- You are fluent in English, Portuguese, French, Spanish, and Arabic.
- Default language is English. If the user writes in another language, respond in that language automatically.
- For Lusophone Africa contexts, use correct Portuguese terminology: "Ministério da Defesa", "Forças Armadas", "Orçamento Geral do Estado", "licença de exportação", "utilizador final".
- You can translate defence procurement terms across languages and should do so when bridging communication between parties.
- When discussing CPLP markets, prefer Portuguese names for institutions, ranks, and procurement concepts.

ANALYTICAL FRAMEWORKS
When asked about COMPLIANCE, structure your answer as:
  (1) Classification — what export control category does this item fall under?
  (2) Licensing route — which licence type and jurisdiction applies?
  (3) Risk factors — sanctions, end-use concerns, diversion risk, human rights
  (4) Recommendation — GO / NO-GO / INVESTIGATE, with specific next steps

When asked about a DEAL OPPORTUNITY, structure as:
  (1) Market context — political/economic drivers, budget cycle, urgency
  (2) Competitive landscape — who else is chasing this, their advantages
  (3) Relationship tier — Arkmurus standing in this market (Incumbent/Established/Developing/Cold Entry)
  (4) Entry strategy — specific actions, partners, timeline
  (5) Compliance flags — export control and sanctions considerations

For ALL substantive assessments:
- Provide a confidence level (0-100%) alongside your epistemic status tag.
- Distinguish clearly between FACTS (sourced from data) and ASSESSMENTS (your analysis).
- Challenge your own conclusions — note what evidence would invalidate your assessment.

COMMUNICATION STYLE
- Write like a senior intelligence analyst briefing a CEO — authoritative but concise.
- Use bullet points for actionable items.
- Bold key findings and risk flags using **bold** markdown.
- For longer responses, include a **BOTTOM LINE** summary at the end.
- Use intelligence community notation: [CONFIRMED], [PROBABLE], [POSSIBLE], [UNCERTAIN].
- When you do not know something, say so clearly and suggest specific steps to find out.

LEARNING POSTURE
- You are continuously learning. When you learn new facts from conversations, tag them with confidence levels.
- When your knowledge is corrected by a user, update immediately and thank them.
- You aspire to match the depth and thoroughness of the best AI assistants. Each conversation makes you sharper.

INVESTIGATION METHODOLOGY
When investigating an entity (person, company, or network), follow this protocol:

PERSON INVESTIGATION:
1. IDENTITY VERIFICATION — Cross-reference name across: LinkedIn, corporate registries, sanctions lists, PEP databases, news archives. Flag name variants, aliases, transliterations.
2. PROFESSIONAL NETWORK — Map: current employer, previous roles, board memberships, advisory positions. Identify decision-making authority and procurement influence.
3. PERSONAL CONNECTIONS — Identify: family business interests, political affiliations, military service history, educational background (military academies signal defence connections).
4. FINANCIAL INDICATORS — Look for: unusual wealth indicators, property holdings in multiple jurisdictions, shell company directorships, offshore structures.
5. RED FLAGS — Check: sanctions list proximity (1st/2nd degree connections to sanctioned entities), PEP status, adverse media, litigation history, regulatory actions.
6. CROSS-REFERENCE — Verify every claim from at least 2 independent sources. Note single-source claims as [UNVERIFIED].

COMPANY INVESTIGATION:
1. CORPORATE STRUCTURE — Map: parent company, subsidiaries, JVs, beneficial owners (follow the 25% UBO threshold). Check corporate registry in country of incorporation.
2. OWNERSHIP CHAIN — Trace ownership through layers: nominee directors, shell companies, trust structures. Flag circular ownership or opaque structures.
3. SANCTIONS EXPOSURE — Screen: company name + all name variants + parent + subsidiaries + directors + UBOs against OFAC/OFSI/EU/UN lists. Apply 50% ownership rule.
4. BUSINESS RELATIONSHIPS — Map: key customers, suppliers, partners, agents, intermediaries. Identify defence ministry connections, government contracts, offset partners.
5. FINANCIAL HEALTH — Check: annual accounts (Companies House, SEC filings, local registry), credit ratings, litigation, unpaid judgments, bankruptcy risk.
6. COMPLIANCE HISTORY — Search: previous export control violations, debarment lists (World Bank, ADB, EU), previous sanctions, anti-corruption investigations.
7. MEDIA & REPUTATION — Adverse media search: corruption allegations, human rights concerns, environmental violations, political scandals, investigative journalism mentions.

NETWORK ANALYSIS:
1. MAP THE WEB — Build a relationship graph: who knows who, through which entity, what role.
2. IDENTIFY GATEKEEPERS — Who controls access to the decision-maker? Who are the trusted advisors?
3. FIND HIDDEN CONNECTIONS — Same addresses, shared directorships, overlapping beneficial owners, co-investments, family ties, military academy cohorts.
4. ASSESS INFLUENCE FLOWS — Who influences procurement decisions? Who signs off? Who has veto power?
5. FLAG RISKS — Sanctioned nodes in the network (even 2nd/3rd degree), PEP connections, conflict of interest patterns.

CROSS-REFERENCING RULES:
- NEVER rely on a single source for factual claims
- Corporate registries > self-reported data (websites, LinkedIn)
- Government sanctions lists > news reports > social media
- Recent data > historical data (but note patterns over time)
- Absence of information IS information (why is there no public data on this entity?)
- When sources conflict, report BOTH versions with your assessment of which is more credible

OSINT TECHNIQUES:
- Company registries: Companies House (UK), SEC EDGAR (US), OpenCorporates (global), local registries
- Sanctions: OFAC SDN, OFSI, EU Consolidated, UN SC, OpenSanctions
- Procurement: DSCA FMS notifications, UN procurement, TED (EU tenders), national portals
- Corporate intel: annual reports, credit agencies, bankruptcy filings, UBO registries
- People: LinkedIn (job history), corporate filings (directorships), news archives, court records
- Adverse media: Google News, LexisNexis patterns, investigative journalism (OCCRP, ICIJ)
- Geospatial: vessel tracking (AIS), flight tracking (ADS-B), satellite imagery (Sentinel)
- Financial: property registries, offshore leaks databases (ICIJ), beneficial ownership registers"""

ARIA_THINK_SYSTEM = f"""{ARIA_SYSTEM_PROMPT}

DEEP REASONING PROTOCOL
You are about to perform a full 6-step intelligence analysis. Structure your response EXACTLY as follows (use these headers):

## ORIENTATION
What type of question is this? What domain expertise applies? What are the key uncertainties?

## INVENTORY
What signals, data, or prior knowledge is relevant? What is missing?

## REASONING
Step-by-step analysis. Show your work. Cross-reference multiple lines of evidence.

## CHALLENGE
What is the strongest counter-argument to your emerging conclusion? What would change your assessment?

## CONCLUSION
Clear statement of finding. Confidence level. Epistemic status tag.

## ACTION
Specific, actionable next step for Arkmurus. Who does what, by when.

## METACOGNITION
Self-grade (A/B/C/D), biggest knowledge gap, what would improve this assessment."""


# ── Intel Context Builder ────────────────────────────────────────────────────

def _build_intel_context(intel_data: dict | None) -> str:
    """Build live intelligence context string from sweep data."""
    if not intel_data:
        return ""
    parts: list[str] = []

    # Market snapshot
    vix = (intel_data.get("markets") or {}).get("vix", {}).get("value")
    brent = (intel_data.get("energy") or {}).get("brent")
    if vix or brent:
        parts.append(f"MARKET SNAPSHOT: VIX {vix or '?'} | Brent ${brent or '?'}")

    # Urgent OSINT
    urgent = (intel_data.get("tg") or {}).get("urgent") or []
    if urgent:
        items = [f"- [{s.get('channel','OSINT')}] {(s.get('text',''))[:180]}" for s in urgent[:6]]
        parts.append(f"OSINT SIGNALS ({len(urgent)} urgent):\n" + "\n".join(items))

    # Correlations
    corrs = intel_data.get("correlations") or []
    if corrs:
        items = [f"- {c.get('region','')} [{c.get('severity','')}]: {(c.get('topSignals',[{}])[0].get('text',''))[:150]}" for c in corrs[:5]]
        parts.append(f"REGIONAL CORRELATIONS:\n" + "\n".join(items))

    # Defence news
    news = intel_data.get("defenseNews") or []
    if news:
        items = [f"- {d.get('title','')}" for d in news[:5]]
        parts.append(f"DEFENCE NEWS ({len(news)} items):\n" + "\n".join(items))

    # Opportunities
    opps = intel_data.get("opportunities") or []
    if opps:
        items = [
            f"- {o.get('market','')} (Score {o.get('score',0)}/100, Tier {o.get('tier','?')}) — "
            f"{', '.join((o.get('procurementNeeds') or [])[:3])} | {o.get('complianceStatus','')}"
            for o in opps[:8]
        ]
        parts.append(f"TOP OPPORTUNITIES:\n" + "\n".join(items))

    # Tenders
    tenders = intel_data.get("procurementTenders") or {}
    tender_items = tenders.get("items") or [] if isinstance(tenders, dict) else []
    if tender_items:
        items = [f"- {t.get('title') or t.get('text','')} [{t.get('source','')}]" for t in tender_items[:6]]
        parts.append(f"ACTIVE TENDERS ({len(tender_items)}):\n" + "\n".join(items))

    # ACLED conflict
    acled = intel_data.get("acled") or {}
    if acled.get("totalEvents", 0) > 0:
        s = f"CONFLICT DATA: {acled.get('totalEvents',0)} events, {acled.get('totalFatalities',0)} fatalities"
        top = acled.get("topCountries") or []
        if top:
            s += f" | Top: {', '.join(c.get('country','') + '(' + str(c.get('events',0)) + ')' for c in top[:5])}"
        parts.append(s)

    # Brain priority
    brain = (intel_data.get("bdIntelligence") or {}).get("brain") or {}
    wp = brain.get("weeklyPriority") or {}
    if wp.get("action"):
        parts.append(f"BRAIN TOP PRIORITY: {wp['action']} [{wp.get('market','')}] — {wp.get('whyNow','')}")

    # Metadata
    meta = intel_data.get("meta") or {}
    if meta.get("timestamp"):
        parts.append(f"DATA AS OF: {meta['timestamp']} | Sources: {meta.get('sourcesOk',0)}/{meta.get('sourcesQueried',0)} OK")

    if not parts:
        return ""
    return "\n\n[LIVE INTELLIGENCE — Crucix platform data, updated this sweep]\n" + "\n\n".join(parts)


# Neural memory needs async but context builder is sync — use contextvars for thread safety
import contextvars
_neural_ctx_var: contextvars.ContextVar[str] = contextvars.ContextVar("neural_ctx", default="")


# ── Language Detection ──────────────────────────────────────────────────────

_PT_WORDS = {"como", "qual", "sobre", "defesa", "armas", "governo", "ministério",
             "forças", "armadas", "obrigado", "olá", "preciso", "também", "país"}
_FR_WORDS = {"comment", "quel", "défense", "gouvernement", "ministère", "également",
             "bonjour", "merci", "aussi", "besoin", "militaire", "armée"}
_ES_WORDS = {"cómo", "cuál", "defensa", "gobierno", "ministerio", "también",
             "hola", "gracias", "necesito", "ejército", "fuerzas", "armadas"}


def _detect_language_hint(message: str) -> str:
    """Return a language hint string to prepend to the user prompt, or empty."""
    lower = message.lower()
    words = set(re.findall(r"\w+", lower))

    # Arabic script detection (Unicode range)
    if re.search(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]+", message):
        return "[User is writing in Arabic — respond in Arabic]\n"

    pt_hits = len(words & _PT_WORDS)
    fr_hits = len(words & _FR_WORDS)
    es_hits = len(words & _ES_WORDS)

    best = max(pt_hits, fr_hits, es_hits)
    if best < 2:
        return ""
    if pt_hits == best:
        return "[User is writing in Portuguese — respond in Portuguese]\n"
    if fr_hits == best:
        return "[User is writing in French — respond in French]\n"
    return "[User is writing in Spanish — respond in Spanish]\n"

def _sync_neural_context(message: str) -> str:
    """Return per-request neural context set before context building."""
    return _neural_ctx_var.get("")


def _build_7_layer_context(message: str, intel_data: dict | None) -> str:
    """Build all 8 intelligence layers (7 + neural memory), budget-capped."""
    layer_fns = [
        ("live_intel",  lambda: _build_intel_context(intel_data)),
        ("knowledge",   lambda: search_knowledge(message)),
        ("ledger",      lambda: query_ledger(message)),
        ("contacts",    lambda: get_contact_context(message)),
        ("competitors", lambda: get_competitor_context(message)),
        ("approach",    lambda: get_approach_context(message)),
        ("gtm",         lambda: get_gtm_context(message)),
        ("neural",      lambda: _sync_neural_context(message)),
        ("semantic",    lambda: get_semantic_context(message)),
    ]
    total = ""
    for name, fn in layer_fns:
        try:
            layer = fn()
            if not layer:
                continue
            if len(total) + len(layer) > MAX_CONTEXT_CHARS:
                continue  # skip this layer but try smaller ones below
            total += layer
        except Exception as e:
            logger.warning("Context layer '%s' failed: %s", name, e)
    return total


# ── Session Management ───────────────────────────────────────────────────────

async def _get_session(session_id: str) -> dict:
    key = f"crucix:aria:session:{session_id}"
    data = await rs.get_json(key)
    return data or {"messages": [], "createdAt": time.time()}


async def _save_session(session_id: str, session: dict) -> None:
    key = f"crucix:aria:session:{session_id}"
    await rs.set_json(key, session, ex=SESSION_TTL)


# ── Identity ─────────────────────────────────────────────────────────────────

IDENTITY_KEY = "crucix:brain:aria:identity"

async def get_identity() -> dict:
    data = await rs.get_json(IDENTITY_KEY)
    if data:
        return data
    return {
        "name": "ARIA",
        "full_name": "Arkmurus Research Intelligence Agent",
        "status": "online",
        "age_days": 0,
        "total_sweeps": 0,
        "total_leads": 0,
        "domains_mastered": [
            "Lusophone Africa defence procurement",
            "UK export control compliance",
            "OSINT source assessment",
            "Counterparty due diligence",
        ],
        "known_biases": [
            "May over-weight Angola/Mozambique due to training data",
            "Lusophone sources stronger than Anglophone Africa",
        ],
        "curiosity_threads": [],
        "strongest_skill": "Pattern recognition across Lusophone Africa signals",
        "admitted_weakness": "Thin on competitor tracking and contact intelligence",
    }


# ── Parse Think Response ─────────────────────────────────────────────────────

def _parse_think_response(text: str, question: str, duration_ms: int) -> dict:
    """Parse the structured 6-step think response."""
    def extract(header: str, next_headers: list[str]) -> str:
        pattern = rf"##\s*{header}[\s\S]*?\n([\s\S]*?)(?=##\s*(?:{'|'.join(next_headers)})|$)"
        m = re.search(pattern, text, re.IGNORECASE)
        return m.group(1).strip() if m else ""

    all_h = ["ORIENTATION", "INVENTORY", "REASONING", "CHALLENGE", "CONCLUSION", "ACTION", "METACOGNITION"]
    sections = {}
    for i, h in enumerate(all_h):
        sections[h.lower()] = extract(h, all_h[i + 1:])

    conclusion = sections.get("conclusion", "")
    epistemic = "ASSESSED"
    for tag in ["CONFIRMED", "PROBABLE", "UNCERTAIN", "SPECULATIVE"]:
        if f"[{tag}]" in conclusion.upper():
            epistemic = tag
            break

    conf_match = re.search(r"(\d{1,3})%\s*confidence", conclusion, re.IGNORECASE)
    confidence = int(conf_match.group(1)) if conf_match else 55

    meta_text = sections.get("metacognition", "")
    grade_match = re.search(r"\b([A-D])\b", meta_text)
    self_grade = grade_match.group(1) if grade_match else "B"

    gap_match = re.search(r"(?:gap|missing|would improve)[^\n.]*[:\s]+([^\n.]+)", meta_text, re.IGNORECASE)
    biggest_gap = gap_match.group(1).strip() if gap_match else ""

    return {
        "question": question,
        "orientation": sections.get("orientation", ""),
        "inventory": sections.get("inventory", ""),
        "reasoning": sections.get("reasoning", ""),
        "challenge": sections.get("challenge", ""),
        "conclusion": {
            "statement": conclusion or text,
            "epistemic_status": epistemic,
            "confidence": confidence,
            "key_assumption": "",
            "action": {"what": sections.get("action", "")},
        },
        "metacognition": {
            "self_grade": self_grade,
            "biggest_gap": biggest_gap,
        },
        "duration_ms": duration_ms,
        "full_text": text,
    }


# ── Public API ───────────────────────────────────────────────────────────────

async def aria_chat(
    message: str,
    session_id: str,
    llm: LLMProvider,
    intel_data: dict | None = None,
) -> dict:
    """Multi-turn chat with ARIA, 8-layer context injection (7 intel + neural memory)."""
    if not llm or not llm.is_configured:
        return {
            "response": "ARIA requires an LLM to be configured. Set LLM_PROVIDER and LLM_API_KEY.",
            "session_id": session_id,
            "fallback": True,
        }

    # Detect self-improvement requests ("improve your X", "fix your Y", etc.)
    improvement_request = self_improve.detect_self_improvement_request(message)
    if improvement_request:
        try:
            plan = await self_improve.handle_self_improvement_chat(message, llm)
            if plan and plan.get("detected"):
                # If there's a concrete plan with files, execute it
                if plan.get("plan") and not plan.get("needs_approval", True):
                    exec_results = await self_improve.execute_improvement_plan(plan["plan"], llm)
                    staged_count = sum(1 for r in exec_results if r.get("staged"))
                    response = plan.get("response", "")
                    if staged_count:
                        response += f"\n\nI've staged {staged_count} improvement(s). "
                        response += "Safe changes (bug fixes) will auto-deploy. "
                        response += "Larger changes are staged for your review at /api/aria/self/staged."
                    return {
                        "response": response,
                        "session_id": session_id,
                        "self_improvement": {
                            "type": improvement_request,
                            "plan": plan.get("plan", []),
                            "results": exec_results,
                        },
                    }
                else:
                    # Return the plan for approval
                    response = plan.get("response", "I understand you want me to improve.")
                    if plan.get("plan"):
                        response += "\n\nHere's my plan:\n"
                        for i, step in enumerate(plan["plan"], 1):
                            response += f"  {i}. **{step.get('file', '?')}** — {step.get('change', '?')} (Risk: {step.get('risk', '?')})\n"
                        response += "\nShall I proceed? Say 'yes, improve' to execute."
                    return {
                        "response": response,
                        "session_id": session_id,
                        "self_improvement": {
                            "type": improvement_request,
                            "plan": plan.get("plan", []),
                            "awaiting_approval": True,
                        },
                    }
        except Exception as e:
            logger.warning("Self-improvement chat handling failed: %s", e)
            # Fall through to normal chat

    session = await _get_session(session_id)
    history = (session.get("messages") or [])[-MAX_TURNS * 2:]

    # Pre-fetch neural memory (async) and set per-request context
    try:
        neural_ctx = await neural_memory.get_neural_context(message)
        _neural_ctx_var.set(neural_ctx)
    except Exception as e:
        logger.warning("Neural recall failed: %s", e)
        _neural_ctx_var.set("")

    # Build 8-layer context (7 intel + neural memory)
    context = _build_7_layer_context(message, intel_data)

    # Detect language and add hint
    lang_hint = _detect_language_hint(message)

    # Format conversation — recent turns in full, older turns summarised
    if history:
        recent_cutoff = 10 * 2  # last 10 exchanges in full detail
        if len(history) > recent_cutoff:
            older = history[:-recent_cutoff]
            recent = history[-recent_cutoff:]
            # Compress older history to key points only
            older_summary = "\n".join(
                f"- {'User asked' if m['role'] == 'user' else 'ARIA said'}: {m['content'][:150]}"
                for m in older
            )
            recent_formatted = "\n\n".join(
                f"{'User' if m['role'] == 'user' else 'ARIA'}: {m['content']}"
                for m in recent
            )
            user_prompt = (
                f"{lang_hint}"
                f"[Earlier in conversation — summary]\n{older_summary}\n\n"
                f"[Recent conversation]\n{recent_formatted}\n\n"
                f"[Current message]\nUser: {message}{context}"
            )
        else:
            formatted = "\n\n".join(
                f"{'User' if m['role'] == 'user' else 'ARIA'}: {m['content']}"
                for m in history
            )
            user_prompt = f"{lang_hint}[Previous conversation]\n{formatted}\n\n[Current message]\nUser: {message}{context}"
    else:
        user_prompt = f"{lang_hint}{message}{context}"

    try:
        result = await llm.complete(ARIA_SYSTEM_PROMPT, user_prompt, max_tokens=4000, timeout=120.0)
        response_text = result.text
    except Exception as e:
        # Record error for autonomous self-improvement
        try:
            await self_improve.record_error(
                "llm_error", str(e), "aria_engine.py", "aria_chat"
            )
        except Exception:
            pass
        logger.error("ARIA LLM error: %s", e)
        return {
            "response": "ARIA encountered an internal error. Please try again.",
            "session_id": session_id,
            "error": True,
        }

    # Update session
    history.append({"role": "user", "content": message})
    history.append({"role": "aria", "content": response_text})
    session["messages"] = history[-MAX_TURNS * 2:]
    session["updatedAt"] = time.time()
    await _save_session(session_id, session)

    # Auto-extract facts (non-blocking)
    try:
        await auto_extract_facts(message, response_text)
    except Exception as e:
        logger.warning("Auto-extract facts failed: %s", e)

    # Grow neural network from conversation (non-blocking)
    try:
        combined = f"{message} {response_text}"
        await neural_memory.learn_from_text(combined, source=f"chat:{session_id}", llm=llm)
    except Exception as e:
        logger.warning("Neural learning failed: %s", e)

    # Record for training
    try:
        await training_data.record_conversation(
            ARIA_SYSTEM_PROMPT, message, response_text,
            {"hadIntelContext": bool(intel_data), "contextLength": len(context)},
        )
    except Exception as e:
        logger.warning("Training data record failed: %s", e)

    return {
        "response": response_text,
        "session_id": session_id,
        "turn": len(history) // 2,
    }


async def aria_think(
    question: str,
    context: dict | None,
    llm: LLMProvider,
    intel_data: dict | None = None,
) -> dict:
    """Deep 6-step reasoning chain."""
    if not llm or not llm.is_configured:
        return {"error": "ARIA requires an LLM to be configured. Set LLM_PROVIDER and LLM_API_KEY."}

    intel_context = _build_intel_context(intel_data)
    context_str = ""
    if context and isinstance(context, dict) and context:
        context_str = f"\n\nExplicit context:\n{json.dumps(context, indent=2)[:2000]}"

    user_prompt = f"Question for deep analysis: {question}{context_str}{intel_context}\n\nPlease work through all 6 steps of the reasoning protocol in full."

    start = time.time()
    try:
        result = await llm.complete(ARIA_THINK_SYSTEM, user_prompt, max_tokens=3000, timeout=90.0)
        text = result.text
    except Exception as e:
        return {"error": f"ARIA reasoning failed: {e}"}

    duration_ms = int((time.time() - start) * 1000)
    parsed = _parse_think_response(text, question, duration_ms)

    # Record for training
    try:
        await training_data.record_think_response(question, parsed)
    except Exception:
        pass

    return parsed
