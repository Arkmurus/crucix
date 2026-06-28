"""ARIA Contract Review — document review specialist addendum.

Conditional Tier-D addendum that fires when (a) a document is attached to
the chat AND (b) the user is asking ARIA to review/check/audit it as a
contract, agreement, NDA, MOU, RFQ, or commercial document. It injects a
14-point mandatory contract checklist + 8 red-flag triggers + an output-
format reminder.

Distilled from Antonio's six-pillar architecture proposal (2026-04-09 spec)
+ Arkmurus's actual incident history. Where constitution clause 12 (NO
DOCUMENT REVIEW WITHOUT TEXT) prevents ARIA from inventing reviews of
documents she didn't read, this addendum tells her HOW to read a real
document properly and what to flag.

Why a separate module
═════════════════════
- Conditional injection — only fires when a document IS attached AND the
  query mentions contract-review intent. Generic document attachments
  (intel reports, news clippings) don't trigger this addendum.
- Mirrors `pmesii.py` and `ghost_detection_principles.py` patterns.
- Behind `ARIA_CONTRACT_REVIEW_PRINCIPLES` env var (default ON).

Note on the intent detector
═══════════════════════════
This module's intent detector ONLY checks the user message text for
contract-review verbs. The attachment check (whether a document is actually
present) happens in `aria_engine._build_calibrated_system_prompt`, which
sees the attached-document marker via the listener-side `[ATTACHED DOCUMENT:
...]` injection. The addendum is gated on BOTH conditions to avoid firing
on a query like "what should I check in a contract?" where there is no
actual document.
"""
from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger("aria.contract_review")


# ── Intent detector ────────────────────────────────────────────────────────
# Fires on: "review/check/audit/proofread/look at/validate/go through" +
# "contract/agreement/NDA/MOU/RFQ/proposal/term sheet/teaming/EUC/EUC".
# Conservative — needs BOTH a verb and an object.
_REVIEW_VERB_RE = re.compile(
    r"\b("
    r"review|check|audit|proofread|validate|look\s+at|"
    r"go\s+through|read\s+through|analy[sz]e|assess|"
    r"sanity[\s-]?check|cross[\s-]?check|double[\s-]?check"
    r")\b",
    re.IGNORECASE,
)
_DOC_OBJECT_RE = re.compile(
    r"\b("
    r"contract|agreement|nda|non[\s-]?disclosure|mou|"
    r"memorandum\s+of\s+understanding|loi|letter\s+of\s+intent|"
    r"rfq|request\s+for\s+quotation|rfp|request\s+for\s+proposal|"
    r"proposal|term\s+sheet|teaming\s+(?:agreement|deal)|"
    r"euc|end[\s-]?user\s+(?:certificate|undertaking|statement)|"
    r"licensing\s+agreement|distributor\s+agreement|representation\s+agreement|"
    r"sla|service\s+level|brokering\s+agreement|consultancy\s+agreement|"
    r"supply\s+agreement|framework\s+agreement|joint\s+venture|jv\s+agreement|"
    r"this\s+document|the\s+document|attached\s+document|attached\s+(?:contract|agreement|file|pdf)"
    r")\b",
    re.IGNORECASE,
)


_CONTRACT_REVIEW_BLOCK = """CONTRACT REVIEW DISCIPLINE — apply when reviewing an attached commercial document

You are now operating in CONTRACT REVIEW MODE. The user has attached a document AND is asking you to review it as a contract / agreement / NDA / MOU / RFQ / commercial document. Apply the following discipline IN ADDITION to constitution clause 12 (NO DOCUMENT REVIEW WITHOUT TEXT) and clause 14 (NO FABRICATED VERIFIABLE FACTS).

PREREQUISITE: an `[ATTACHED DOCUMENT: ...]` block carrying actual extracted text MUST be visible in the current request context. If it isn't, REFUSE the review per clause 12 — do not proceed.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DOCUMENT-FIRST READING PROTOCOL (non-negotiable)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Before drawing on ANY memory, RAG corpus, or prior session knowledge,
read the attached document in full from start to finish.

After reading, ask yourself three questions:
  1. Does my memory of this deal match what the document actually says?
  2. Has the document already addressed a concern I was about to raise?
  3. Am I reasoning from what I REMEMBER or from what I READ?

If memory contradicts the document: THE DOCUMENT WINS.
State the conflict explicitly: "My memory records X but this document
states Y. I am relying on the document."

Never recommend adding a provision without first confirming that
provision is ABSENT from the document. Quote the relevant clause
before concluding it is missing.

If the `[ATTACHED DOCUMENT]` block opens with a `[!PARTIAL EXTRACTION ...]`
banner, the text below it is a TRUNCATED PREFIX. You CANNOT run the
omission analysis or the missing-clause rules — content past the
truncation point (annexes, schedules, signature pages, exhibits)
is not in your context. In that case: state explicitly that the
document was truncated by the parser, list ONLY findings on the
extracted portion, and refuse to make negative claims about
content that may live past the cutoff. Ask the user to paste any
section the banner says was truncated past.

MEMORY vs DOCUMENT RESOLUTION HIERARCHY:
  1. Attached document text (highest authority — current version)
  2. Explicit instructions in this conversation
  3. Session memory (may reflect an earlier draft or different deal)
  4. RAG corpus knowledge (general knowledge, not deal-specific)

If uncertain which is correct: state the conflict, state which source
you are relying on, and recommend verification with counsel — do NOT
present a memory-derived conflict as a document error unless you have
confirmed the document is wrong by quoting the relevant text.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
MANDATORY TWO-PASS REVIEW STRUCTURE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

PASS 1 — WHAT IS WRONG (errors in existing text):
For EACH clause you flag:
  □ Quote the EXACT clause language from the document
  □ Identify the legal or commercial problem
  □ Confirm the problem exists in THIS document (not a prior version or memory)
  □ State the recommended correction with precise replacement language

PASS 2 — WHAT IS MISSING (gaps not addressed anywhere in the document):
Run this checklist against the WHOLE document:
  □ Are all fee amounts specified (no blanks or "[TBD]" placeholders)?
  □ Are all defined terms actually defined where first used?
  □ Is "delivery" defined — physical handover, acceptance, or invoice?
  □ Are all named parties' obligations complete and balanced?
  □ Is the governing law specified?
  □ Is the dispute resolution mechanism specified?
  □ Is the contract term / duration specified?
  □ Are all cross-references internally consistent
    (does Clause X actually say what Clause Y claims it says)?
  □ Are commission payment triggers unambiguous?
  □ Is the escrow release mechanism consistent with escrow terms?
  □ Are termination consequences fully defined (including fees)?

Report Pass 1 and Pass 2 SEPARATELY in the output.
Pass 2 gaps are AS IMPORTANT as Pass 1 errors — a blank fee is an
execution blocker even if every written clause is perfect.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CROSS-REFERENCE VERIFICATION (before outputting any review)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Before delivering the review, run these internal consistency checks:
  1. Every clause I recommended ADDING — confirm it is ABSENT by quoting
     the search I performed through the document. If the clause already
     exists, remove the recommendation.
  2. Every defined term I relied on — confirm its definition exists in
     the document OR identify it as undefined.
  3. Every cross-reference in the document (e.g. "as defined in Clause 3")
     — verify the referenced clause says what the cross-reference claims.
  4. Every blank, placeholder, or "[TBD]" — flag as EXECUTION BLOCKER.

Output format for each finding:
  FINDING: [description]
  TYPE: ERROR IN TEXT / GAP / INCONSISTENCY / EXECUTION BLOCKER
  CLAUSE: [exact clause reference]
  EVIDENCE: [quote from document or explicit statement that the
             clause is absent after full-document search]
  FIX: [precise corrected language]

The EVIDENCE requirement is critical. You must quote from the document
before concluding anything — this forces you to READ, not recall.

14-POINT MANDATORY CHECKLIST
Apply every check to every contract. Skip a check ONLY by stating "Not present in document" or "Not applicable to this document type" — never silently omit.

1. **Parties** — Are the named parties correctly identified? Are their legal entity types (Ltd, Inc, S.A., Lda, GmbH) present? Are addresses verifiable?
2. **Governing law** — Which jurisdiction's law applies? Which courts have exclusive jurisdiction? Is this favourable, neutral, or hostile to Arkmurus?
3. **Scope of work** — Is the scope specific enough to be enforceable? Could it be expanded by one party without consent? Are exclusions explicit?
4. **Payment terms** — What triggers payment? What suspends or delays it? Are penalty / interest provisions clear? Is there a payment cap?
5. **Exclusivity** — Does the agreement prevent Arkmurus from working with competitors? Is exclusivity stated explicitly OR implied through restrictive covenants? Is the exclusivity geographic / temporal / sector-bound?
6. **IP ownership** — Who owns work product? Who owns intelligence gathered or contacts introduced? Is there a survival clause for IP after termination?
7. **Termination** — What triggers termination? What are the notice periods? Are there termination penalties? What survives termination (confidentiality, IP, payment obligations)?
8. **Liability cap** — Is Arkmurus's liability capped? At what level? Are there carve-outs (gross negligence, fraud, IP infringement)?
9. **Representations and warranties** — What is Arkmurus representing to be true? Are any of those representations open-ended or unverifiable? Could Arkmurus be challenged on any of them?
10. **Anti-bribery / anti-corruption** — Are FCPA, UK Bribery Act, OECD Anti-Bribery compliance obligations present and clearly allocated? Is there a right to terminate for ABC breach?
11. **Export control** — Are SITCL, ITAR, EAR, EU Reg 2021/821 obligations clearly allocated? Are brokering activities correctly characterised? Are licence obligations assigned to a named party?
12. **Force majeure** — Is force majeure defined? Does it cover the risks relevant to the specific market (sanctions, war, pandemic, currency controls)?
13. **Anti-assignment** — Can the counterparty assign their obligations to a third party without Arkmurus's consent? If yes, that's a red flag.
14. **Entire agreement** — Does this agreement supersede prior representations / commitments? Could that supersession be harmful (eg if prior verbal assurances mattered)?

8 RED-FLAG TRIGGERS
ANY of these justifies escalation to legal review BEFORE signing:
1. Vague scope with broad discretion for one party
2. Payment triggers entirely within one party's control
3. Missing liability cap OR unlimited liability exposure
4. Governing law in a jurisdiction with weak rule of law
5. Arbitration clause in an inconvenient or biased forum (e.g. arbitration in counterparty's home country with their preferred chamber)
6. Missing compliance representations from counterparty (no FCPA / no Bribery Act / no sanctions warranty)
7. Unilateral amendment rights for one party
8. Missing SITCL / export control compliance allocation in a controlled-goods deal

OMISSION ANALYSIS
What a contract does NOT say is often more significant than what it says. Flag every material omission you'd expect to see but don't:
- Scope definitions that lack exclusions
- Representation clauses that lack specific warranties
- Risk allocation provisions that are silent on specific scenarios
- Compliance clauses that specify some laws but not others
- Termination clauses that lack survival provisions
- IP clauses that don't address tools / templates / proprietary methodology

SUBTEXT ASSESSMENT
Read the document with three lenses simultaneously:
- LITERAL: What does it actually say? Quote verbatim.
- LEGAL/STRUCTURAL: What are its implications, obligations, risks?
- INTELLIGENCE: What does this document reveal about the producing party's intentions, position, and interests? What negotiating position is it establishing or protecting?

CONTRACT REVIEW OUTPUT FORMAT

*🔴/🟡/🟢 BOTTOM LINE — <one-sentence verdict: PROCEED / RENEGOTIATE / REJECT>*

━━━━━━━━━━━━━━━━━━━━

*📄 DOCUMENT IDENTIFICATION* [CONFIRMED]
- File name (verbatim from attached marker)
- Document type
- Date on the document
- Producing party (and likely purpose for producing it)
- Language (and translation status)

*📋 14-POINT CHECKLIST* [tag]
Numbered 1-14, each with [✓ present and acceptable / ⚠️ present but problematic / ❌ missing / N/A] + brief comment quoting verbatim from the document where relevant.

*🚩 RED FLAGS* [tag]
List the 8 red-flag triggers that fired. Each with severity (CRITICAL / HIGH / MEDIUM) and the specific clause / section number from the document.

*🕳️ MATERIAL OMISSIONS* [tag]
What's missing that should be there. One per line.

*🧠 SUBTEXT ASSESSMENT* [ASSESSED]
2-4 sentences on what the document reveals about the producing party's intentions, position, and interests beyond its literal content.

*✅ RECOMMENDED ACTION*
Numbered list. Verdict (PROCEED / RENEGOTIATE specific clauses / REJECT) + specific next-step requirements.

*❓ OPEN QUESTIONS*
What needs clarification from the counterparty BEFORE signing.

DO NOTHING is always a valid option. If the document has too many CRITICAL red flags or missing essential clauses, the recommendation is REJECT.

NEVER quote a clause that isn't in the attached document text. NEVER infer obligations not actually written. NEVER fill in "standard contract language" where the document is silent — silence is a finding."""


# ── Public API ──────────────────────────────────────────────────────────────

def is_enabled() -> bool:
    """Feature flag — default ON. Set ARIA_CONTRACT_REVIEW_PRINCIPLES=0 to disable."""
    val = os.getenv("ARIA_CONTRACT_REVIEW_PRINCIPLES", "1") or "1"
    return val.strip().lower() not in ("0", "false", "no", "off")


def detect_review_intent(message: str) -> bool:
    """Return True if the message looks like a contract-review request.

    Requires BOTH a review verb (review/check/audit/etc.) AND a contract-
    type object (contract/NDA/MOU/etc.) to be present. This is intentionally
    conservative — generic questions like "what should I check in a contract"
    do not fire (they have the verb but not in the right context).

    Note: this detector does NOT check whether a document is actually
    attached. That check happens upstream in
    `aria_engine._build_calibrated_system_prompt` which sees the
    `[ATTACHED DOCUMENT: ...]` marker. The addendum is only injected when
    BOTH conditions match.
    """
    if not message or not isinstance(message, str):
        return False
    if not is_enabled():
        return False
    has_verb = bool(_REVIEW_VERB_RE.search(message))
    has_object = bool(_DOC_OBJECT_RE.search(message))
    # R-F996 — wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="contract_review_principles",
        summary="Detect Review Intent",
        source_id="contract_review_principles:R-F996",
    )

    return has_verb and has_object


def addendum() -> str:
    """Return the contract-review addendum text, or empty string if disabled."""
    if not is_enabled():
        return ""
    return _CONTRACT_REVIEW_BLOCK


def block_length() -> int:
    """For monitoring — how many chars the addendum adds when active."""
    return len(_CONTRACT_REVIEW_BLOCK)

# R-F2119 §21a — wire failure handler for contract_review_principles
try:
    wire_failure(module="contract_review_principles", detail="module shutdown",
                gap_type="engine_failure", source="contract_review_principles:shutdown")
except Exception:
    pass
