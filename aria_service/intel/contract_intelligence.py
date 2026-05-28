"""ARIA Contract Intelligence — self-review loop, clause library, and correction learning.

Three capabilities that elevate ARIA's contract review from competent to exceptional:

1. SELF-REVIEW LOOP: After generating a contract review, run a second LLM
   pass that audits the draft against the document. Catches the exact class
   of error where ARIA recommends adding something already present or misses
   blanks/placeholders.

2. CLAUSE LIBRARY: Standard reference clauses for defence BD contract types.
   Stored in RAG for semantic retrieval. ARIA compares "what this contract
   says" against "what a well-drafted version should say."

3. CORRECTION LEARNING: When the user corrects a review ("that clause IS in
   the document"), store it as a permanent lesson. Next time ARIA reviews a
   similar contract, she checks for that pattern first.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Optional

from . import redis_store as rs

logger = logging.getLogger("aria.contract_intelligence")

# ── Feature gate ────────────────────────────────────────────────────────────

def is_enabled() -> bool:
    val = os.getenv("ARIA_CONTRACT_INTELLIGENCE", "1") or "1"
    return val.strip().lower() not in ("0", "false", "no", "off")


# ── 1. SELF-REVIEW LOOP ────────────────────────────────────────────────────

SELF_REVIEW_PROMPT = """You are ARIA's contract review auditor. You have been given:
1. The ORIGINAL DOCUMENT text
2. ARIA's DRAFT REVIEW of that document

Your job is to audit ARIA's review for errors. Check each of these:

A. FALSE POSITIVES — Did ARIA recommend adding a clause that ALREADY EXISTS
   in the document? Search the document text for each recommendation ARIA made.
   If the clause is present, flag it: "REMOVE: recommendation to add [X] — already
   present at [quote from document]."

B. MISSED BLANKS — Are there any blank fields, "[TBD]", "________", or
   placeholder values in the document that ARIA did not flag? These are
   execution blockers.

C. MISSED CROSS-REFERENCE ERRORS — Did ARIA check every internal reference
   (e.g. "as defined in Clause 5")? Are any cross-references pointing to the
   wrong clause?

D. MEMORY CONTAMINATION — Did ARIA cite facts about this deal that do NOT
   appear in the attached document? If so, flag: "MEMORY CONTAMINATION:
   ARIA stated [X] but this does not appear in the document."

E. COMPLETENESS — Did ARIA run both Pass 1 (errors in text) and Pass 2
   (what's missing)? If Pass 2 is absent or thin, flag it.

Output format:
SELF-REVIEW FINDINGS:
- [REMOVE/ADD/FLAG]: [description] — [evidence from document]

If no issues found, state: "SELF-REVIEW: No corrections needed. Draft review is accurate."

Be ruthless. The user is relying on this review for a real commercial decision."""


# H2: document chunk size for self-review. Past bug: a fixed [:12000]
# slice silently truncated long contracts while the review claimed a
# full audit. We now run the audit over overlapping windows and merge
# their findings; anything beyond the hard cap below is still reported
# so the user knows some clauses were not seen.
_SELF_REVIEW_CHUNK_CHARS = 11000     # per pass
_SELF_REVIEW_OVERLAP_CHARS = 800     # sliding-window overlap to keep
                                     # clauses that straddle boundaries
_SELF_REVIEW_MAX_CHUNKS = 6          # hard cap — ~60k char (~15k tokens)


def _chunk_document(text: str) -> list[str]:
    if not text:
        return []
    chunks: list[str] = []
    i = 0
    n = len(text)
    step = _SELF_REVIEW_CHUNK_CHARS - _SELF_REVIEW_OVERLAP_CHARS
    while i < n and len(chunks) < _SELF_REVIEW_MAX_CHUNKS:
        chunks.append(text[i : i + _SELF_REVIEW_CHUNK_CHARS])
        i += step
    return chunks


async def self_review_contract(
    document_text: str,
    draft_review: str,
    llm,
) -> dict:
    """Run ARIA's contract review through a self-audit pass.

    Long documents are audited over overlapping windows and the
    findings are merged. If the document is larger than the hard cap
    (about 60k chars / 15k tokens), a `truncated` flag is surfaced so
    the caller knows some content was not seen.

    Returns the self-review findings + a corrected review if issues found.
    """
    if not is_enabled() or not llm or not llm.is_configured:
        return {"self_reviewed": False, "reason": "disabled or no LLM"}

    chunks = _chunk_document(document_text)
    if not chunks:
        return {"self_reviewed": False, "reason": "empty document"}

    truncated = len(document_text) > (_SELF_REVIEW_CHUNK_CHARS + (_SELF_REVIEW_MAX_CHUNKS - 1) * (_SELF_REVIEW_CHUNK_CHARS - _SELF_REVIEW_OVERLAP_CHARS))
    draft_slice = draft_review[:8000]

    all_findings: list[str] = []
    has_corrections = False
    model = ""

    def _window_prompt(idx: int, chunk: str) -> str:
        return (
            f"ORIGINAL DOCUMENT [window {idx+1}/{len(chunks)}, "
            f"chars {idx * (_SELF_REVIEW_CHUNK_CHARS - _SELF_REVIEW_OVERLAP_CHARS)}–"
            f"{idx * (_SELF_REVIEW_CHUNK_CHARS - _SELF_REVIEW_OVERLAP_CHARS) + len(chunk)}]:\n"
            f"{chunk}\n\n"
            f"ARIA'S DRAFT REVIEW:\n{draft_slice}\n\n"
            f"Audit ARIA's draft against the document WINDOW shown above. "
            f"R-F954: the draft reviews the FULL document across MANY windows — a "
            f"draft claim about a clause that is NOT in THIS window is almost "
            f"certainly covered by another window, so you MUST NOT flag it as "
            f"missing, absent, hallucinated, or 'memory contamination'. ONLY flag: "
            f"(a) a draft claim that CONTRADICTS or misquotes text visible in THIS "
            f"window, or (b) a clause present in THIS window the draft got wrong or "
            f"omitted. If the draft is consistent with everything visible here, "
            f"reply exactly 'No issues in this window.'"
        )

    # R-F983 — run the per-window audits CONCURRENTLY (bounded) instead of
    # serially. Each window is an independent audit of a DISJOINT document slice
    # with no ordering dependency, so N serial DeepSeek round-trips (the bulk of a
    # ~143s doc-review chat trace) collapse to ceil(N / concurrency). Results are
    # reassembled in window order so the merged findings read identically to the
    # old serial version. Concurrency is bounded so the provider rate limiter /
    # $300 cap still gate spend.
    try:
        _conc = max(1, int(os.getenv("ARIA_SELF_REVIEW_CONCURRENCY", "3") or "3"))
    except ValueError:
        _conc = 3
    _sem = asyncio.Semaphore(_conc)

    async def _audit_window(idx: int, chunk: str) -> tuple[int, str, bool, str]:
        async with _sem:
            try:
                result = await llm.complete(
                    SELF_REVIEW_PROMPT, _window_prompt(idx, chunk),
                    max_tokens=2000, timeout=60.0,
                )
                text = (result.text or "").strip()
                if text:
                    corr = any(kw in text.upper() for kw in
                               ("REMOVE:", "ADD:", "FLAG:", "MEMORY CONTAMINATION", "MISSED"))
                    return (idx,
                            f"── Self-review window {idx+1}/{len(chunks)} ──\n{text}",
                            corr, result.model or "")
                return (idx, "", False, result.model or "")
            except Exception as e:
                logger.warning("Contract self-review window %d/%d failed: %s", idx + 1, len(chunks), e)
                return (idx, f"── Self-review window {idx+1}/{len(chunks)} — ERROR: {e} ──", False, "")

    # Single cost-tracking context spans all concurrent windows (the gathered
    # tasks inherit it); attribution stays "contract_intelligence" as before.
    from . import cost_tracker
    with cost_tracker.feature("contract_intelligence"):
        _results = await asyncio.gather(*[_audit_window(i, c) for i, c in enumerate(chunks)])
    for _idx, _body, _corr, _mdl in sorted(_results, key=lambda r: r[0]):
        if _body:
            all_findings.append(_body)
        if _corr:
            has_corrections = True
        if not model and _mdl:
            model = _mdl

    if truncated:
        all_findings.append(
            "⚠ Document exceeds self-review hard cap; only the first "
            f"{_SELF_REVIEW_MAX_CHUNKS} windows were audited. Split the "
            "document and re-submit remaining sections if needed."
        )

    if not all_findings:
        # R-F977 (§21a): this branch returned with NO brain emission while the
        # success path below absorbs — a dark failure branch. Mirror it with
        # success=False so ARIA learns when contract self-review produces nothing
        # usable, instead of the failure being invisible to the learning loop.
        # NB: a window that *raises* appends an ERROR marker above, so this
        # branch fires specifically when every window returned EMPTY output
        # (e.g. provider returned blank completions) — provenance worded to match.
        try:
            from . import brain_hook
            await brain_hook.absorb(
                module="contract_intelligence",
                summary=f"Contract self-review produced NO findings: all {len(chunks)} windows returned empty",
                detail="every self-review window returned empty output — no audit produced",
                success=False,
                gap_type="operational:research_failure",
                gap_detail=f"contract self-review: {len(chunks)} windows all empty",
                confidence="ASSESSED",
            )
        except Exception as _bh:
            logger.debug("contract self-review failure brain_hook failed: %s", _bh)
        return {"self_reviewed": False, "reason": "all windows failed"}

    # ── Brain hook: feed self-review findings to learning ──
    try:
        from . import brain_hook
        await brain_hook.absorb(
            module="contract_intelligence",
            summary=f"Contract self-review: {len(chunks)} windows, corrections={'YES' if has_corrections else 'NO'}, truncated={truncated}",
            detail="\n\n".join(all_findings)[:3000],
            success=not has_corrections,
            confidence="ASSESSED",
        )
    except Exception as _bh:
        logger.debug("contract self-review brain_hook failed: %s", _bh)

    return {
        "self_reviewed": True,
        "has_corrections": has_corrections,
        "findings": "\n\n".join(all_findings),
        "model": model,
        "windows": len(chunks),
        "truncated": truncated,
    }


# ── 1b. CORRECTION SYNTHESIS (R-F883) ───────────────────────────────────────
# self_review_contract returns the raw window-by-window AUDIT FINDINGS — a
# critique of the draft ("REMOVE: ...", "FLAG (MISSED BLANKS): ...", "So no
# error found here"). Pre-R-F883 the chat path appended that critique verbatim
# to the user reply (live 2026-05-25: the operator saw "── Self-review window
# 1/4 ──" walls + the auditor's own deliberation). That's internal scaffolding.
# This pass takes the draft + findings + document and produces the SINGLE
# clean, decision-grade review the user should actually see.

CORRECTION_SYNTHESIS_PROMPT = """You are ARIA delivering a FINAL contract review to the user for a real commercial decision. You are given your own DRAFT review and the AUDIT FINDINGS from a self-review pass. Produce the single clean, corrected, decision-grade review the user should see.

Rules:
- DELETE any recommendation the audit flagged as a false positive or "already present".
- DELETE any statement the audit flagged as MEMORY CONTAMINATION (facts not in the attached document) — do NOT restate them.
- INCORPORATE every missed item the audit found: blank/placeholder fields ("TBA", "[___]"), and missing clauses (force majeure, governing law, dispute resolution, termination/default, liability caps, payment-security mechanics) and cross-reference errors.
- Answer the user's QUESTION directly and completely, leading with the bottom line.
- For each finding, quote verbatim from the document. If the document was truncated, say "not present in the extracted portion" — NEVER assert a clause is absent from the whole document.
- Output ONLY the final review for the user. No "draft", no "self-review", no window numbers, no meta-commentary about your own process or audit."""


async def finalize_reviewed_contract(
    *,
    user_question: str,
    draft_review: str,
    findings: str,
    document_excerpt: str,
    llm,
) -> Optional[str]:
    """R-F883 — synthesise the clean, corrected final review from the draft +
    self-review findings. Returns the user-facing review text, or None if the
    LLM is unavailable / the call fails (caller falls back to the draft alone —
    NEVER the raw findings dump)."""
    if not llm or not getattr(llm, "is_configured", False):
        return None
    try:
        from . import cost_tracker
        # R-F949 (2026-05-27) — pass the FULL document to the synthesis step.
        # The previous [:16000] slice fed only the first ~16K chars (≈ clause
        # 5.4 of a 60K agreement), so the synthesis — which writes the
        # USER-FACING review — falsely concluded "the excerpt ends at Clause
        # 5.4 / 3.1" and overrode a complete draft. The self-review audits the
        # whole document in windows; finalize must see the same. DeepSeek handles
        # a 60K doc (~12.7K tokens) + draft + findings well within its window
        # (proven live 2026-05-27). 120K covers typical contracts; larger ones
        # already carry the self-review truncation banner.
        prompt = (
            f"USER QUESTION:\n{(user_question or '').strip()[:1500]}\n\n"
            f"YOUR DRAFT REVIEW:\n{(draft_review or '').strip()[:6000]}\n\n"
            f"AUDIT FINDINGS (apply every correction):\n{(findings or '').strip()[:6000]}\n\n"
            f"FULL DOCUMENT (quote verbatim from this — review EVERY clause to the end):\n{(document_excerpt or '').strip()[:120000]}"
        )
        with cost_tracker.feature("contract_intelligence"):
            res = await llm.complete(
                CORRECTION_SYNTHESIS_PROMPT, prompt, max_tokens=4000, timeout=120.0,
            )
        out = (res.text or "").strip()
        return out or None
    except Exception as e:
        logger.warning("R-F883 contract correction synthesis failed: %s", e)
        return None


# ── R-F953 — daily contract-review self-check ───────────────────────────────
# A standing canary: run a synthetic contract review end-to-end and verify it
# (a) returns a non-empty answer, (b) is not truncated mid-document, and (c)
# reaches the LAST clause. The governing-law clause is deliberately placed PAST
# the ~16K char point that the R-F949 bug truncated at, so a regression of that
# class is caught here BEFORE the operator hits it (the recurring contract-review
# failure mode, 2026-05-27/28). Flags the brain on failure.

def build_selfcheck_contract() -> str:
    """A deterministic synthetic agreement (~20K chars) whose GOVERNING LAW
    clause sits past 16K, so any mid-document truncation regression is visible."""
    _titles = ["DEFINITIONS", "APPOINTMENT", "COMMISSION", "PAYMENT MECHANICS",
               "TERM", "TERMINATION", "LIABILITY", "CONFIDENTIALITY",
               "INTELLECTUAL PROPERTY", "ANTI-BRIBERY AND COMPLIANCE", "FORCE MAJEURE"]
    _filler = ("This provision is included for completeness and reflects standard "
               "commercial drafting practice between the parties to this Agreement. ") * 22
    parts = ["MASTER TEST AGREEMENT (ARIA self-check fixture)\n"]
    for i, t in enumerate(_titles, start=1):
        parts.append(f"\n{i}. {t}\n{_filler}\n")
    parts.append(
        "\n12. GOVERNING LAW AND DISPUTE RESOLUTION\n"
        "This Agreement is governed by and construed in accordance with the laws of "
        "England and Wales. Any dispute is finally resolved by arbitration under the "
        "LCIA Rules, seat London, three arbitrators.\n"
        "\n[SIGNATURES]\nParty A: __________   Party B: __________\n"
    )
    return "".join(parts)


async def run_contract_selfcheck(llm) -> dict:
    """Run the synthetic review end-to-end; flag the brain on regression."""
    import time
    if not llm or not getattr(llm, "is_configured", False):
        return {"ok": False, "skipped": "no_llm"}
    doc = build_selfcheck_contract()
    gov_offset = doc.find("GOVERNING LAW AND DISPUTE")
    msg = (f"Review this agreement and confirm the GOVERNING LAW clause.\n\n"
           f"[ATTACHED DOCUMENT: selfcheck_agreement.txt]\n{doc}\n[END ATTACHED DOCUMENT]")
    t0 = time.time()
    resp, err = "", ""
    try:
        from .. import aria_engine
        res = await aria_engine.aria_chat(msg, "aria_selfcheck_contract", llm, None)
        resp = (res or {}).get("response") or (res or {}).get("answer") or ""
    except Exception as e:
        err = str(e)[:200]
    elapsed = round(time.time() - t0, 1)
    low = resp.lower()
    reaches_end = ("governing law" in low) or ("england and wales" in low) or ("lcia" in low)
    ok = bool(resp.strip()) and len(resp) > 200 and reaches_end and not err
    report = {"ok": ok, "len": len(resp), "reaches_governing_law": reaches_end,
              "elapsed_s": elapsed, "gov_clause_offset": gov_offset, "error": err}
    if not ok:
        try:
            from . import brain_hook
            await brain_hook.observe_self_event(
                "contract_review_selfcheck_failed",
                (f"Daily contract self-check FAILED: len={len(resp)} "
                 f"reaches_governing_law={reaches_end} elapsed={elapsed}s err={err or 'none'}. "
                 f"Governing-law clause sits at char {gov_offset} (past the 16K R-F949 truncation point) "
                 f"— a regression of the contract-review truncation/empty class."),
                success=False,
                gap_type="contract_review_regression",
            )
        except Exception as _be:
            logger.warning("R-F953 self-check brain signal failed: %s", _be)
        logger.warning("[R-F953] contract self-check FAILED: %s", report)
    else:
        logger.info("[R-F953] contract self-check OK (%ds, %d chars)", int(elapsed), len(resp))
    return report


# ── 2. CLAUSE LIBRARY ──────────────────────────────────────────────────────
# Standard reference clauses for defence BD contract types.
# Ingested into RAG so ARIA can compare against them during review.

CLAUSE_LIBRARY = {
    "brokering_commission": {
        "title": "Standard Brokering Commission Clause",
        "content": """COMMISSION AND PAYMENT

The Principal shall pay the Broker a commission of [X]% of the Net Contract
Value of each Qualifying Transaction introduced by the Broker and accepted
by the Principal.

"Net Contract Value" means the total value of the contract between the
Principal and the End User, excluding taxes, duties, freight, insurance,
and offset obligations.

"Qualifying Transaction" means a transaction where the Broker has made a
Material Contribution to the introduction, development, or conclusion of
the contract, as evidenced by written records of the Broker's involvement.

Commission shall become due and payable within [30/60/90] days of the
Principal's receipt of payment from the End User, and shall be calculated
on amounts actually received (not invoiced).

Where the Principal receives payment in instalments, commission shall be
paid pro rata on each instalment received.

No commission shall be payable on: (a) warranty claims, (b) liquidated
damages, (c) offset obligations performed directly by the Principal,
(d) after-sales support and spares not introduced by the Broker.""",
        "tags": ["commission", "brokering", "payment", "representation"],
        "contract_type": "brokering_agreement",
    },

    "termination_clause": {
        "title": "Standard Termination Clause (Defence Brokering)",
        "content": """TERMINATION

Either party may terminate this Agreement:
(a) for convenience on [90/180] days' written notice;
(b) immediately if the other party commits a material breach and fails to
    remedy it within [30] days of written notice specifying the breach;
(c) immediately if the other party becomes insolvent, enters administration,
    or has a receiver appointed;
(d) immediately if continued performance would expose either party to
    sanctions, export control violations, or criminal liability.

TERMINATION FEE: If the Principal terminates for convenience under (a)
during the first [24] months, the Principal shall pay the Broker a
termination fee of [amount/formula — MUST BE SPECIFIED, not left blank].

SURVIVAL: The following provisions survive termination: confidentiality
(Clause [X]), commission on pipeline transactions introduced before
termination (Clause [X]), intellectual property (Clause [X]), governing
law and dispute resolution (Clause [X]).

PIPELINE PROTECTION: Commission remains payable on any Qualifying
Transaction where the Broker made a Material Contribution before the
termination date, provided the transaction completes within [24] months
of termination.""",
        "tags": ["termination", "survival", "pipeline", "termination fee"],
        "contract_type": "brokering_agreement",
    },

    "export_control_compliance": {
        "title": "Export Control and Compliance Clause (Defence)",
        "content": """EXPORT CONTROL AND COMPLIANCE

Each party represents and warrants that it shall comply with all applicable
export control laws and regulations, including but not limited to:
(a) UK Export Control Act 2002 and the Export Control Order 2008;
(b) UK Trade in Goods (Control) Order 2003 (SITCL);
(c) US International Traffic in Arms Regulations (ITAR) 22 CFR 120-130;
(d) US Export Administration Regulations (EAR) 15 CFR 730-774;
(e) EU Council Regulation (EC) No 428/2009 (Dual-Use Regulation);
(f) Any applicable sanctions regime (OFAC, EU, UN Security Council, HMT).

LICENCE RESPONSIBILITY: The party responsible for obtaining any required
export licence, brokering licence, or end-user certificate shall be
[Principal/Broker — MUST BE SPECIFIED]. The cost of licence applications
shall be borne by [specified party].

CONTROLLED GOODS: The parties acknowledge that the goods/services subject
to this Agreement [are/may be] controlled under [specified control list
entry, e.g. ML1, ML10, Category 6A] and require [specified licence type,
e.g. SIEL, OIEL, OGEL].

TERMINATION FOR COMPLIANCE: Either party may terminate this Agreement
immediately if continued performance would require a licence that has been
refused, revoked, or is unlikely to be granted based on the current
regulatory environment.

SANCTIONS SCREENING: Each party shall screen all end users, consignees,
and intermediaries against applicable sanctions lists before each
transaction. Evidence of screening shall be retained for [6] years.""",
        "tags": ["export control", "SITCL", "ITAR", "compliance", "sanctions", "licence"],
        "contract_type": "any",
    },

    "anti_bribery": {
        "title": "Anti-Bribery and Anti-Corruption Clause",
        "content": """ANTI-BRIBERY AND ANTI-CORRUPTION

Each party represents, warrants, and undertakes that:
(a) it has not and will not offer, promise, give, or authorise the giving
    of any financial or other advantage to any person in order to obtain
    or retain business or a business advantage in connection with this
    Agreement;
(b) it has and will maintain adequate procedures (as defined in Section 7
    of the UK Bribery Act 2010) to prevent bribery by persons associated
    with it;
(c) it complies with the US Foreign Corrupt Practices Act 1977 (FCPA),
    the UK Bribery Act 2010, and the OECD Convention on Combating Bribery
    of Foreign Public Officials;
(d) it will not use any commission, fee, or payment under this Agreement
    to make any facilitation payment or pay any government official;
(e) it will promptly notify the other party if it becomes aware of any
    breach or suspected breach of this clause;
(f) it will maintain accurate books and records sufficient to demonstrate
    compliance with this clause for a period of [6] years.

BREACH: Any breach of this clause shall constitute a material breach
entitling the non-breaching party to terminate immediately. No termination
fee or pipeline protection shall apply to termination for ABC breach.""",
        "tags": ["anti-bribery", "FCPA", "Bribery Act", "corruption", "compliance"],
        "contract_type": "any",
    },

    "liability_cap": {
        "title": "Limitation of Liability Clause (Brokering)",
        "content": """LIMITATION OF LIABILITY

Subject to Clause [X] (Exclusions), the total aggregate liability of each
party under or in connection with this Agreement, whether in contract, tort
(including negligence), breach of statutory duty, or otherwise, shall not
exceed [the greater of: (a) the total commission paid or payable under this
Agreement in the [12] months preceding the claim; or (b) [specified amount]].

EXCLUSIONS: Nothing in this Agreement limits liability for:
(a) death or personal injury caused by negligence;
(b) fraud or fraudulent misrepresentation;
(c) breach of the anti-bribery clause (Clause [X]);
(d) wilful default or gross negligence;
(e) breach of confidentiality obligations resulting in loss of
    classified or export-controlled information.

CONSEQUENTIAL LOSS: Neither party shall be liable for any indirect,
consequential, special, or punitive damages, loss of profit, loss of
business, or loss of anticipated savings, whether or not such loss was
foreseeable or the party had been advised of its possibility.""",
        "tags": ["liability", "limitation", "cap", "exclusions", "consequential loss"],
        "contract_type": "any",
    },

    "confidentiality": {
        "title": "Confidentiality and Non-Disclosure Clause (Defence)",
        "content": """CONFIDENTIALITY

Each party undertakes to keep confidential all Confidential Information
received from the other party and not to disclose it to any third party
without the prior written consent of the disclosing party.

"Confidential Information" means all information (whether written, oral,
electronic, or visual) disclosed by one party to the other in connection
with this Agreement, including but not limited to:
(a) commercial terms, pricing, commission rates, and payment arrangements;
(b) customer identities, contact details, and procurement requirements;
(c) technical specifications, designs, and proprietary methodologies;
(d) business strategies, market intelligence, and competitive analysis;
(e) any information marked or identified as confidential.

EXCEPTIONS: This obligation does not apply to information that:
(a) is or becomes publicly available other than through breach;
(b) was already known to the receiving party before disclosure;
(c) is independently developed without reference to the Confidential
    Information;
(d) is required to be disclosed by law, regulation, or court order
    (provided the disclosing party is notified in advance where permitted).

DURATION: This obligation survives termination for [3/5] years.

EXPORT-CONTROLLED INFORMATION: Where Confidential Information includes
export-controlled data (ITAR, EAR, UK controlled), additional security
obligations under the applicable export control regime shall apply.""",
        "tags": ["confidentiality", "NDA", "non-disclosure", "classified", "survival"],
        "contract_type": "any",
    },

    "governing_law_dispute": {
        "title": "Governing Law and Dispute Resolution (International Defence)",
        "content": """GOVERNING LAW AND DISPUTE RESOLUTION

This Agreement shall be governed by and construed in accordance with the
laws of [England and Wales / specified jurisdiction].

DISPUTE RESOLUTION:
(a) Any dispute arising out of or in connection with this Agreement shall
    first be referred to the senior management of each party for resolution
    within [30] days of written notice.
(b) If not resolved under (a), the dispute shall be referred to and finally
    resolved by arbitration under the [LCIA / ICC / SIAC] Rules.
(c) The seat of arbitration shall be [London / specified neutral venue].
(d) The language of the arbitration shall be English.
(e) The arbitral tribunal shall consist of [one/three] arbitrator(s).
(f) The award shall be final and binding.

INTERIM RELIEF: Nothing in this clause prevents either party from seeking
interim or injunctive relief from any court of competent jurisdiction.

RECOMMENDED: For Arkmurus agreements, English law + LCIA London arbitration
provides the most favourable combination of legal certainty, enforceability
(New York Convention), and venue neutrality.""",
        "tags": ["governing law", "dispute resolution", "arbitration", "LCIA", "ICC", "jurisdiction"],
        "contract_type": "any",
    },
}


async def ingest_clause_library() -> dict:
    """Ingest the clause library into RAG store for semantic retrieval."""
    from . import rag_store

    results = {}
    total_chunks = 0

    for clause_id, clause in CLAUSE_LIBRARY.items():
        try:
            # M1: clauses are TEMPLATES, not verified legal opinion —
            # retrieved chunks must carry that provenance so they aren't
            # surfaced to the user as authoritative. Confidence tag is
            # ASSESSED, not CONFIRMED, and the disclaimer is prepended to
            # the content so any downstream retrieval includes it.
            content_with_disclaimer = (
                "[ASSESSED — template clause, not legal advice. "
                "Verify with qualified counsel before use in an executed agreement.]\n\n"
                + clause["content"]
            )
            result = await rag_store.ingest_document(
                content_with_disclaimer,
                source=f"clause_library:{clause_id}",
                source_type="contract_clause_library",
                title=clause["title"],
                url=f"internal://aria/clause_library/{clause_id}",
                extra_metadata={
                    "contract_type": clause.get("contract_type", "any"),
                    "tags": ",".join(clause.get("tags", [])),
                    "confidence": "ASSESSED",
                    "provenance": "template",
                    "disclaimer": "template clause, verify with counsel",
                },
            )
            chunks = result.get("chunks", 0) if isinstance(result, dict) else 0
            total_chunks += chunks
            results[clause_id] = "OK"
        except Exception as e:
            results[clause_id] = f"ERROR: {e}"

    logger.info("Clause library ingested: %d/%d clauses, %d chunks",
                sum(1 for v in results.values() if v == "OK"),
                len(CLAUSE_LIBRARY), total_chunks)
    return {"clauses_ingested": sum(1 for v in results.values() if v == "OK"),
            "total_chunks": total_chunks, "detail": results}


# ── 3. CORRECTION LEARNING ─────────────────────────────────────────────────

CORRECTIONS_KEY = "crucix:aria:contract_corrections"


async def record_correction(
    document_name: str,
    error_type: str,
    description: str,
    lesson: str,
) -> dict:
    """Store a contract review correction as a permanent lesson.

    error_type: FALSE_POSITIVE | MISSED_BLANK | MISSED_CLAUSE |
                MEMORY_CONTAMINATION | WRONG_JURISDICTION | OTHER
    """
    correction = {
        "document": document_name,
        "error_type": error_type,
        "description": description,
        "lesson": lesson,
        "recorded_at": time.time(),
    }

    corrections = await rs.get_json(CORRECTIONS_KEY) or []
    corrections.append(correction)
    if len(corrections) > 200:
        corrections = corrections[-200:]
    await rs.set_json(CORRECTIONS_KEY, corrections, ex=180 * 86400)

    logger.info("[contract] Correction recorded: %s — %s", error_type, lesson[:100])

    # ── Brain hook: correction = negative mastery signal + lesson stored ──
    try:
        from . import brain_hook
        await brain_hook.absorb(
            module="contract_intelligence",
            summary=f"Contract correction ({error_type}) on {document_name}: {lesson[:200]}",
            detail=f"{error_type}: {description}. Lesson: {lesson}",
            entity_name=document_name,
            success=False,
            confidence="CONFIRMED",
        )
    except Exception as _bh:
        logger.debug("contract correction brain_hook failed: %s", _bh)

    return {"recorded": True, "total_corrections": len(corrections)}


async def get_corrections(limit: int = 20) -> list[dict]:
    """Retrieve recent contract review corrections."""
    corrections = await rs.get_json(CORRECTIONS_KEY) or []
    return corrections[-limit:]


async def get_correction_addendum() -> str:
    """Build a prompt addendum from recent corrections.

    Injected into the contract review prompt so ARIA avoids
    repeating the same mistakes.
    """
    corrections = await rs.get_json(CORRECTIONS_KEY) or []
    if not corrections:
        return ""

    recent = corrections[-10:]
    lines = ["LESSONS FROM PRIOR CONTRACT REVIEW ERRORS (avoid repeating):"]
    for c in recent:
        lines.append(f"- {c.get('error_type', 'ERROR')}: {c.get('lesson', '')}")

    return "\n".join(lines)
