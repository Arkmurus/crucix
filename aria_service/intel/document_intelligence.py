"""
Document Intelligence — universal structured extraction for any document.

Takes the OCR/extraction output of any uploaded document and produces:
  1. A form classification (e.g. HK NSC1, UK CS01, generic_invoice)
  2. A structured JSON of canonical fields for that form type
  3. A red-flag analysis (declarative rules + LLM judgement)
  4. A markdown overview ready to surface in WA / chat
  5. Persistence: facts to knowledge base, full doc to RAG

Designed to be additive — wired in after the existing read_document
extraction so it cannot break the existing pipeline. If LLM extraction
fails, the function returns None and the caller behaves as before.

Form catalogue covers the highest-yield filings in DD work:
  - Hong Kong Companies Registry: NSC1, ND2A/B, ND4, NAR1, NNC1, NR1
  - UK Companies House: IN01, AP01-04, TM01-02, CS01, SH01, NM01, PSC01-09
  - Generic types: invoice, contract, tender_notice, financial_statement,
    cv_resume, certificate, sanctions_extract, kyc_id, generic_document

For unknown documents, falls back to a generic schema that still pulls
parties, dates, monetary values, and a structured summary.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional

from . import document_corrections as _corr
from .knowledge import store_fact
from .llm_json import parse_llm_json

logger = logging.getLogger("aria.doc_intel")


# ── FORM CATALOGUE ──────────────────────────────────────────────────────────
# Each entry tells the extractor (a) what jurisdiction this is, (b) what the
# canonical fields are, and (c) what red flags to look for. Schemas are kept
# concise — the LLM fills them via JSON-mode prompting.

FORM_CATALOGUE: dict[str, dict[str, Any]] = {
    # ── Hong Kong Companies Registry ─────────────────────────────────────────
    "HK_NSC1": {
        "jurisdiction": "HK",
        "name": "Return of Allotment",
        "regulator": "Hong Kong Companies Registry",
        "tier": 1,
        "schema": {
            "company_name_en": "string",
            "company_name_cn": "string",
            "company_number": "string",
            "br_number": "string|null",
            "allotment_date": "YYYY-MM-DD",
            "filing_date": "YYYY-MM-DD|null",
            "currency": "ISO 4217",
            "total_increase": "number",
            "share_class": "string",
            "shares_allotted_cash": "number",
            "shares_allotted_non_cash": "number",
            "amount_paid_per_share": "number",
            "amount_unpaid_per_share": "number",
            "non_cash_consideration_description": "string|null",
            "allottees": [
                {"name": "string", "address": "string", "shares": "number", "percent": "number"}
            ],
            "post_issue_total_shares": "number",
            "post_issue_total_paid": "number",
            "signed_by": "string",
            "signer_capacity": "Director|Company Secretary",
            "presentor_name": "string",
            "presentor_address": "string",
            "presentor_email": "string|null",
        },
    },
    "HK_ND4": {
        "jurisdiction": "HK",
        "name": "Notice of Resignation of Company Secretary and/or Director",
        "regulator": "Hong Kong Companies Registry",
        "tier": 1,
        "schema": {
            "company_name_en": "string",
            "company_name_cn": "string|null",
            "company_number": "string|null",
            "br_number": "string",
            "resigner_capacity": "Director|Company Secretary|Alternate Director",
            "resigner_is_natural_person": "boolean",
            "resigner_name_en": "string",
            "resigner_name_cn": "string|null",
            "resignation_date": "YYYY-MM-DD",
            "filing_date_received": "YYYY-MM-DD|null",
            "continues_as_alternate": "boolean|null",
            "presentor_name": "string",
            "presentor_address": "string",
            "presentor_email": "string|null",
        },
    },
    "HK_ND2A": {
        "jurisdiction": "HK",
        "name": "Notice of Change of Director / Reserve Director",
        "regulator": "Hong Kong Companies Registry",
        "tier": 1,
        "schema": {
            "company_name_en": "string",
            "company_number": "string|null",
            "br_number": "string",
            "change_type": "appointment|cessation|particulars",
            "officer_name": "string",
            "officer_capacity": "string",
            "effective_date": "YYYY-MM-DD",
            "presentor_name": "string",
        },
    },
    "HK_NAR1": {
        "jurisdiction": "HK",
        "name": "Annual Return",
        "regulator": "Hong Kong Companies Registry",
        "tier": 1,
        "schema": {
            "company_name_en": "string",
            "company_number": "string",
            "br_number": "string",
            "as_at_date": "YYYY-MM-DD",
            "registered_office": "string",
            "directors": [{"name": "string", "address": "string", "type": "natural|corporate"}],
            "secretary": {"name": "string", "address": "string"},
            "members": [{"name": "string", "address": "string", "shares": "number", "class": "string"}],
            "issued_capital": [{"class": "string", "currency": "string", "total_shares": "number", "total_paid": "number"}],
        },
    },
    "HK_NNC1": {
        "jurisdiction": "HK",
        "name": "Incorporation Form (Private Company)",
        "regulator": "Hong Kong Companies Registry",
        "tier": 1,
        "schema": {
            "proposed_company_name_en": "string",
            "proposed_company_name_cn": "string|null",
            "type": "private|guarantee",
            "registered_office_proposed": "string",
            "founders": [{"name": "string", "address": "string", "shares": "number"}],
            "first_directors": [{"name": "string", "address": "string"}],
            "first_secretary": {"name": "string", "address": "string"},
            "share_capital": {"currency": "string", "total_shares": "number", "amount_per_share": "number"},
        },
    },
    "HK_NR1": {
        "jurisdiction": "HK",
        "name": "Statement of Particulars of Charge",
        "regulator": "Hong Kong Companies Registry",
        "tier": 1,
        "schema": {
            "company_name_en": "string",
            "company_number": "string",
            "charge_creation_date": "YYYY-MM-DD",
            "charge_amount": "number",
            "charge_currency": "string",
            "chargee_name": "string",
            "chargee_address": "string",
            "secured_property_description": "string",
            "instrument_type": "string",
        },
    },
    # ── UK Companies House ───────────────────────────────────────────────────
    "UK_IN01": {
        "jurisdiction": "UK",
        "name": "Application to Register a Company",
        "regulator": "Companies House",
        "tier": 1,
        "schema": {
            "proposed_company_name": "string",
            "company_type": "string",
            "registered_office": "string",
            "sic_codes": ["string"],
            "subscribers": [{"name": "string", "address": "string", "shares": "number"}],
            "first_directors": [{"name": "string", "dob": "YYYY-MM-DD", "address": "string", "nationality": "string"}],
            "first_secretary": {"name": "string", "address": "string"},
            "share_capital": {"currency": "string", "total_shares": "number", "nominal_per_share": "number"},
            "psc_statements": ["string"],
        },
    },
    "UK_AP01": {
        "jurisdiction": "UK",
        "name": "Appointment of Director",
        "regulator": "Companies House",
        "tier": 1,
        "schema": {
            "company_name": "string",
            "company_number": "string",
            "appointment_date": "YYYY-MM-DD",
            "officer_name": "string",
            "former_names": "string|null",
            "officer_dob": "YYYY-MM-DD",
            "officer_nationality": "string",
            "officer_occupation": "string|null",
            "officer_residential_address": "string",
            "officer_service_address": "string",
            "officer_country_of_residence": "string",
        },
    },
    "UK_AP02": {
        "jurisdiction": "UK",
        "name": "Appointment of Corporate Director",
        "regulator": "Companies House",
        "tier": 1,
        "schema": {
            "company_name": "string",
            "company_number": "string",
            "appointment_date": "YYYY-MM-DD",
            "corporate_officer_name": "string",
            "corporate_officer_address": "string",
            "place_of_registration": "string",
            "registration_number": "string",
            "legal_form": "string",
        },
    },
    "UK_TM01": {
        "jurisdiction": "UK",
        "name": "Termination of Appointment of Director",
        "regulator": "Companies House",
        "tier": 1,
        "schema": {
            "company_name": "string",
            "company_number": "string",
            "termination_date": "YYYY-MM-DD",
            "officer_name": "string",
            "officer_dob": "YYYY-MM-DD|null",
        },
    },
    "UK_TM02": {
        "jurisdiction": "UK",
        "name": "Termination of Appointment of Secretary",
        "regulator": "Companies House",
        "tier": 1,
        "schema": {
            "company_name": "string",
            "company_number": "string",
            "termination_date": "YYYY-MM-DD",
            "officer_name": "string",
        },
    },
    "UK_CS01": {
        "jurisdiction": "UK",
        "name": "Confirmation Statement (Annual)",
        "regulator": "Companies House",
        "tier": 1,
        "schema": {
            "company_name": "string",
            "company_number": "string",
            "review_period_end": "YYYY-MM-DD",
            "registered_office": "string",
            "sic_codes": ["string"],
            "directors": [{"name": "string", "dob": "YYYY-MM-DD"}],
            "secretary": "string|null",
            "issued_capital": {"currency": "string", "total_shares": "number", "aggregate_nominal": "number"},
            "shareholders": [{"name": "string", "shares": "number", "class": "string"}],
            "psc": [{"name": "string", "nature_of_control": ["string"]}],
        },
    },
    "UK_SH01": {
        "jurisdiction": "UK",
        "name": "Return of Allotment of Shares",
        "regulator": "Companies House",
        "tier": 1,
        "schema": {
            "company_name": "string",
            "company_number": "string",
            "allotment_date_from": "YYYY-MM-DD",
            "allotment_date_to": "YYYY-MM-DD|null",
            "currency": "string",
            "shares": [{"class": "string", "number": "number", "nominal_value": "number", "amount_paid": "number", "amount_unpaid": "number"}],
            "non_cash_consideration_description": "string|null",
        },
    },
    "UK_PSC01": {
        "jurisdiction": "UK",
        "name": "Notice of Individual Person with Significant Control",
        "regulator": "Companies House",
        "tier": 1,
        "schema": {
            "company_name": "string",
            "company_number": "string",
            "psc_name": "string",
            "psc_dob": "YYYY-MM-DD",
            "psc_nationality": "string",
            "psc_residential_address": "string|null",
            "psc_service_address": "string",
            "nature_of_control": ["string"],
            "registrable_date": "YYYY-MM-DD",
        },
    },
    # ── Generic document types ───────────────────────────────────────────────
    "GEN_INVOICE": {
        "jurisdiction": "ANY",
        "name": "Commercial Invoice",
        "regulator": None,
        "tier": 3,
        "schema": {
            "invoice_number": "string",
            "issue_date": "YYYY-MM-DD",
            "due_date": "YYYY-MM-DD|null",
            "seller_name": "string",
            "seller_address": "string",
            "buyer_name": "string",
            "buyer_address": "string",
            "currency": "string",
            "subtotal": "number",
            "tax": "number|null",
            "total": "number",
            "line_items": [{"description": "string", "quantity": "number", "unit_price": "number", "total": "number"}],
            "payment_terms": "string|null",
            "bank_details": "string|null",
        },
    },
    "GEN_CONTRACT": {
        "jurisdiction": "ANY",
        "name": "Contract / Agreement",
        "regulator": None,
        "tier": 2,
        "schema": {
            "contract_title": "string",
            "execution_date": "YYYY-MM-DD|null",
            "effective_date": "YYYY-MM-DD|null",
            "expiry_date": "YYYY-MM-DD|null",
            "parties": [{"name": "string", "role": "string", "address": "string"}],
            "subject_matter": "string",
            "consideration_value": "number|null",
            "consideration_currency": "string|null",
            "governing_law": "string|null",
            "jurisdiction": "string|null",
            "termination_clause": "string|null",
            "key_obligations": ["string"],
            "key_risks": ["string"],
        },
    },
    "GEN_TENDER": {
        "jurisdiction": "ANY",
        "name": "Tender Notice / RFP",
        "regulator": None,
        "tier": 2,
        "schema": {
            "tender_reference": "string",
            "issuing_authority": "string",
            "issuing_country": "string",
            "publication_date": "YYYY-MM-DD|null",
            "submission_deadline": "YYYY-MM-DD|null",
            "subject": "string",
            "estimated_value": "number|null",
            "currency": "string|null",
            "category": "string",
            "eligibility_criteria": ["string"],
            "evaluation_criteria": ["string"],
            "contact_email": "string|null",
        },
    },
    "GEN_FINANCIAL": {
        "jurisdiction": "ANY",
        "name": "Financial Statement",
        "regulator": None,
        "tier": 2,
        "schema": {
            "entity_name": "string",
            "period_end": "YYYY-MM-DD",
            "currency": "string",
            "revenue": "number|null",
            "gross_profit": "number|null",
            "operating_profit": "number|null",
            "net_profit": "number|null",
            "total_assets": "number|null",
            "total_liabilities": "number|null",
            "shareholders_equity": "number|null",
            "cash_equivalents": "number|null",
            "auditor": "string|null",
            "audit_opinion": "string|null",
        },
    },
    "GEN_CV": {
        "jurisdiction": "ANY",
        "name": "CV / Résumé",
        "regulator": None,
        "tier": 3,
        "schema": {
            "name": "string",
            "current_role": "string|null",
            "current_employer": "string|null",
            "email": "string|null",
            "phone": "string|null",
            "location": "string|null",
            "summary": "string|null",
            "experience": [{"employer": "string", "role": "string", "from": "string", "to": "string|null", "summary": "string|null"}],
            "education": [{"institution": "string", "qualification": "string", "year": "string|null"}],
            "languages": ["string"],
            "skills": ["string"],
        },
    },
    "GEN_KYC_ID": {
        "jurisdiction": "ANY",
        "name": "KYC Identity Document",
        "regulator": None,
        "tier": 1,
        "schema": {
            "document_type": "passport|national_id|driving_licence|residence_permit",
            "issuing_country": "string",
            "document_number": "string",
            "full_name": "string",
            "date_of_birth": "YYYY-MM-DD",
            "nationality": "string",
            "issue_date": "YYYY-MM-DD|null",
            "expiry_date": "YYYY-MM-DD|null",
            "place_of_birth": "string|null",
        },
    },
    "GEN_CERT_INCORP": {
        "jurisdiction": "ANY",
        "name": "Certificate of Incorporation",
        "regulator": None,
        "tier": 1,
        "schema": {
            "company_name": "string",
            "company_number": "string",
            "incorporation_date": "YYYY-MM-DD",
            "jurisdiction": "string",
            "company_type": "string|null",
            "issuing_authority": "string",
        },
    },
    "GEN_DOCUMENT": {
        "jurisdiction": "ANY",
        "name": "Generic Document",
        "regulator": None,
        "tier": 4,
        "schema": {
            "document_type_guess": "string",
            "subject": "string",
            "primary_date": "YYYY-MM-DD|null",
            "parties": [{"name": "string", "role": "string"}],
            "monetary_values": [{"amount": "number", "currency": "string", "context": "string"}],
            "key_facts": ["string"],
            "open_questions": ["string"],
            "arkmurus_relevance": "string",
        },
    },
}


# ── CLASSIFIER ──────────────────────────────────────────────────────────────

# Filename / OCR-text patterns that strongly imply a form code. Order matters:
# the first match wins. Patterns are case-insensitive.
_FORM_HINTS: list[tuple[str, str]] = [
    # HK forms — the form code appears top-right on every official form
    (r"\bForm\s*NSC\s*1\b|Return of Allotment", "HK_NSC1"),
    (r"\bForm\s*ND\s*4\b|Notice of Resignation of Company Secretary", "HK_ND4"),
    (r"\bForm\s*ND\s*2A\b|Change of Director|Reserve Director", "HK_ND2A"),
    (r"\bForm\s*NAR\s*1\b|Annual Return\b.*\bHong Kong|Companies Registry.*Annual Return", "HK_NAR1"),
    (r"\bForm\s*NNC\s*1\b|Incorporation Form.*Private Company", "HK_NNC1"),
    (r"\bForm\s*NR\s*1\b|Statement of Particulars of Charge", "HK_NR1"),
    # UK Companies House forms
    (r"\bIN01\b|Application to register a company", "UK_IN01"),
    (r"\bAP01\b|Appointment of director\b(?!.*corporate)", "UK_AP01"),
    (r"\bAP02\b|Appointment of corporate director", "UK_AP02"),
    (r"\bTM01\b|Termination of appointment of director", "UK_TM01"),
    (r"\bTM02\b|Termination of appointment of secretary", "UK_TM02"),
    (r"\bCS01\b|Confirmation statement", "UK_CS01"),
    (r"\bSH01\b|Return of allotment of shares", "UK_SH01"),
    (r"\bPSC01\b|Notice of individual person with significant control", "UK_PSC01"),
    # Generic document types — only matched when no registry form code present
    (r"^invoice\b|invoice\s+(?:no|number|#)|tax invoice|commercial invoice", "GEN_INVOICE"),
    (r"\bagreement\b|\bcontract\b|memorandum of understanding|terms and conditions", "GEN_CONTRACT"),
    (r"\b(invitation to tender|request for proposal|RFP|tender notice|RFQ)\b", "GEN_TENDER"),
    (r"income statement|balance sheet|statement of financial position|profit and loss|annual accounts", "GEN_FINANCIAL"),
    (r"^curriculum vitae|^résumé|^resume\b", "GEN_CV"),
    (r"passport|national identity card|driving licence|driver license|residence permit", "GEN_KYC_ID"),
    (r"certificate of incorporation|certificate of formation", "GEN_CERT_INCORP"),
]


def classify_document(text: str, filename: str = "") -> str:
    """Return a form code from FORM_CATALOGUE, or 'GEN_DOCUMENT' as fallback.

    Two-stage: filename match first (cheap and high precision when the user
    names the file sensibly), then a regex sweep of the first ~3000 chars of
    OCR text (form code is always near the top). LLM disambiguation is left
    to the extractor — if the structured extraction fails for the chosen
    form, the caller can re-run with GEN_DOCUMENT.
    """
    # Search both filename + first 3000 chars of body. 3000 is enough to catch
    # the form code on the cover page even when OCR is noisy.
    haystack = f"{filename}\n{text[:3000]}"
    for pattern, code in _FORM_HINTS:
        if re.search(pattern, haystack, flags=re.IGNORECASE):
            return code
    return "GEN_DOCUMENT"


# ── EXTRACTOR ───────────────────────────────────────────────────────────────

_EXTRACT_SYSTEM = (
    "You are a forensic document extraction engine for a defence intelligence "
    "platform. Read the OCR'd document text and output STRICT JSON matching "
    "the requested schema. Rules:\n"
    "  - Only output JSON. No prose, no markdown fences, no commentary.\n"
    "  - Use null for any field you cannot determine — never guess.\n"
    "  - Dates in ISO-8601 (YYYY-MM-DD).\n"
    "  - Numbers as JSON numbers (no thousands separators, no currency symbols).\n"
    "  - Preserve names exactly as printed (capitalisation included).\n"
    "  - For lists, include every row visible — do not truncate.\n"
)


def _build_extract_prompt(text: str, form_code: str) -> str:
    spec = FORM_CATALOGUE[form_code]
    schema_json = json.dumps(spec["schema"], indent=2, ensure_ascii=False)
    return (
        f"FORM TYPE: {form_code} — {spec['name']}\n"
        f"JURISDICTION: {spec['jurisdiction']}\n"
        f"REGULATOR: {spec.get('regulator') or 'N/A'}\n\n"
        f"OUTPUT SCHEMA (return JSON matching this shape exactly):\n"
        f"{schema_json}\n\n"
        f"DOCUMENT TEXT (OCR — may contain artefacts, dual-language Chinese/English, "
        f"or table fragments — extract from the English columns when bilingual):\n"
        f"---BEGIN DOCUMENT---\n"
        f"{text[:60000]}\n"
        f"---END DOCUMENT---\n\n"
        f"Return ONLY the JSON object."
    )


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*|\s*```", re.IGNORECASE)


def _strip_fences(s: str) -> str:
    return _JSON_FENCE_RE.sub("", s.strip())


async def extract_structured(
    text: str,
    form_code: str,
    llm: Any,
    *,
    max_tokens: int = 3000,
) -> Optional[dict]:
    """Run the LLM against the form-specific schema. Returns parsed dict or None.

    Tries one extraction. On parse failure, attempts a single repair pass
    (asks the model to fix its JSON) before giving up.

    Few-shot learning: if previous extractions of the same form have been
    verified or corrected by the team, those are pulled from the corrections
    store and injected into the system prompt as the gold reference.
    """
    if form_code not in FORM_CATALOGUE:
        form_code = "GEN_DOCUMENT"
    if not llm or not getattr(llm, "is_configured", False):
        logger.warning("doc_intel: LLM not configured — skipping extraction")
        return None

    prompt = _build_extract_prompt(text, form_code)
    # Inject few-shot examples from the corrections store so each extraction
    # benefits from the team's prior fixes on the same form type.
    sysmsg = _EXTRACT_SYSTEM
    try:
        examples = await _corr.few_shot_examples(form_code)
        if examples:
            sysmsg = _EXTRACT_SYSTEM + "\n" + _corr.render_few_shot_block(examples)
            logger.info("doc_intel: injected %d few-shot examples for %s", len(examples), form_code)
    except Exception as e:
        logger.debug("doc_intel: few-shot injection skipped (%s)", e)

    try:
        from . import cost_tracker
        with cost_tracker.feature("document_intelligence"):
            result = await llm.complete(sysmsg, prompt, max_tokens=max_tokens, timeout=120.0)
    except Exception as e:
        logger.warning("doc_intel: LLM call failed for %s — %s", form_code, e)
        return None

    parsed = parse_llm_json(result.text or "", source='document_intelligence')
    if isinstance(parsed, dict):
        return parsed
    # Local repair cascade exhausted — try one LLM-based repair pass.
    logger.info("doc_intel: JSON parse failed for %s, attempting repair", form_code)
    repair_prompt = (
        "Your previous response was not valid JSON. Re-emit ONLY the JSON object "
        "(no fences, no commentary), strictly matching the schema:\n\n"
        f"{result.text[:8000]}"
    )
    try:
        from . import cost_tracker as _ct
        with _ct.feature("document_intelligence"):
            repaired = await llm.complete(_EXTRACT_SYSTEM, repair_prompt, max_tokens=max_tokens, timeout=60.0)
        return parse_llm_json(repaired.text or "", source='document_intelligence')
    except Exception as e:
        logger.warning("doc_intel: repair pass failed for %s — %s", form_code, e)
        return None


# ── RED-FLAG ANALYSIS ───────────────────────────────────────────────────────

def _days_between(d1: str, d2: str) -> Optional[int]:
    try:
        return (datetime.fromisoformat(d2) - datetime.fromisoformat(d1)).days
    except Exception:
        return None


def _addr_normalise(a: Optional[str]) -> str:
    if not a:
        return ""
    return re.sub(r"[\s,.\-/]+", " ", a.strip().lower())


def analyse_redflags(structured: dict, form_code: str) -> list[dict]:
    """Apply form-specific + universal red-flag rules to the extracted JSON.

    Each flag is {severity: 'low|medium|high', code, message}. Rules are
    intentionally simple — high precision over recall. The LLM-based
    overview can add narrative red flags on top.
    """
    flags: list[dict] = []
    if not structured:
        return flags

    # ── HK NSC1 (Return of Allotment) ──────────────────────────────────────
    if form_code == "HK_NSC1":
        allottees = structured.get("allottees") or []
        # Compute percentages where missing
        total = sum(int(a.get("shares") or 0) for a in allottees) or 1
        for a in allottees:
            if a.get("percent") in (None, "", 0) and a.get("shares"):
                a["percent"] = round(100 * int(a["shares"]) / total, 2)

        # Concentration check
        if allottees:
            top = max(allottees, key=lambda a: int(a.get("shares") or 0))
            if (top.get("shares") or 0) / total >= 0.5:
                flags.append({
                    "severity": "medium",
                    "code": "concentrated_ownership",
                    "message": f"{top.get('name')} holds {round(100*(top.get('shares') or 0)/total,1)}% — single controlling shareholder.",
                })

        # Same-address nominee check (allottee at presentor address)
        presentor_addr = _addr_normalise(structured.get("presentor_address"))
        if presentor_addr:
            for a in allottees:
                if presentor_addr and presentor_addr in _addr_normalise(a.get("address")):
                    flags.append({
                        "severity": "high",
                        "code": "same_address_as_filer",
                        "message": f"Allottee '{a.get('name')}' shares the registered address of the corporate-services filer ({structured.get('presentor_name')}). Classic nominee/proxy indicator — UBO must be traced separately.",
                    })

        # Non-cash consideration without description
        if (structured.get("shares_allotted_non_cash") or 0) > 0 and not structured.get("non_cash_consideration_description"):
            flags.append({
                "severity": "high",
                "code": "non_cash_no_description",
                "message": "Shares allotted for non-cash consideration without a documented description — AML/corporate fraud risk indicator.",
            })

        # Tiny capital
        if (structured.get("post_issue_total_paid") or 0) > 0 and (structured.get("post_issue_total_paid") or 0) <= 10_000:
            flags.append({
                "severity": "low",
                "code": "low_capitalisation",
                "message": f"Total paid-up capital is only {structured.get('currency','?')} {structured.get('post_issue_total_paid')}. Cannot independently back significant investment commitments.",
            })

    # ── HK ND4 (Resignation) ───────────────────────────────────────────────
    if form_code == "HK_ND4":
        rdate = structured.get("resignation_date")
        fdate = structured.get("filing_date_received")
        if rdate and fdate:
            gap = _days_between(rdate, fdate)
            if gap is not None and gap > 15:
                flags.append({
                    "severity": "medium",
                    "code": "late_filing_hk",
                    "message": f"Filed {gap} days after resignation. HK Companies Ordinance s.645 requires notification within 15 days. Late filing — possible compliance neglect or deferred disclosure.",
                })
        if structured.get("resigner_capacity") in ("Director", "Company Secretary"):
            flags.append({
                "severity": "low",
                "code": "officer_resignation_event",
                "message": f"{structured.get('resigner_capacity')} resignation — verify whether a successor was appointed in a contemporaneous ND2A/ND3 filing. Vacant company-secretary slot in HK is itself a compliance breach (s.474).",
            })

    # ── UK CS01 (Confirmation Statement) ───────────────────────────────────
    if form_code == "UK_CS01":
        directors = structured.get("directors") or []
        if not directors:
            flags.append({"severity": "high", "code": "no_directors_confirmed", "message": "Confirmation statement lists zero directors — highly unusual."})
        if not structured.get("psc"):
            flags.append({
                "severity": "medium",
                "code": "no_psc_listed",
                "message": "No Persons of Significant Control declared. Either a true 'no PSC' case or an undisclosed control gap — verify against PSC01-09 filings.",
            })

    # ── UK SH01 / HK NSC1 shared: foreign-only allottees ───────────────────
    if form_code in ("HK_NSC1",):
        allottees = structured.get("allottees") or []
        addresses = [a.get("address","") for a in allottees if a.get("address")]
        countries = set()
        for addr in addresses:
            for kw, c in (("hong kong","HK"),("singapore","SG"),("united kingdom","UK"),("united states","US"),("usa","US"),("dubai","AE"),("uae","AE"),("british virgin islands","VG"),("cayman","KY"),("seychelles","SC"),("panama","PA"),("luxembourg","LU"),("switzerland","CH"),("hungary","HU"),("china","CN")):
                if kw in addr.lower():
                    countries.add(c); break
        if len(countries) >= 3:
            flags.append({
                "severity": "low",
                "code": "multi_jurisdiction_spread",
                "message": f"Shareholders span {len(countries)} jurisdictions ({', '.join(sorted(countries))}) — typical of UBO opacity / tax structuring rather than a real operating cap table.",
            })

    # ── Universal: missing entity number ───────────────────────────────────
    keys_for_id = ("company_number", "br_number", "registration_number")
    if FORM_CATALOGUE.get(form_code, {}).get("tier", 4) <= 2:
        if not any(structured.get(k) for k in keys_for_id):
            flags.append({"severity": "medium", "code": "no_entity_number", "message": "No company/registration number extracted — limits cross-filing matching."})

    return flags


# ── MARKDOWN OVERVIEW RENDERER ──────────────────────────────────────────────

_SEVERITY_BADGE = {"high": "🔴", "medium": "🟠", "low": "🟡"}


def render_overview(form_code: str, structured: dict, redflags: list[dict]) -> str:
    """Build a WhatsApp-friendly markdown overview from the extracted JSON.

    Form-specific renderers for the highest-yield filings; generic table
    fallback for everything else.
    """
    spec = FORM_CATALOGUE.get(form_code, FORM_CATALOGUE["GEN_DOCUMENT"])
    header = f"📄 *{spec['name']}* — *{spec['jurisdiction']}*"

    body_lines: list[str] = [header, ""]

    if form_code == "HK_NSC1":
        body_lines += [
            f"*Company:* {structured.get('company_name_en','?')}",
            f"*HK Co. No.:* `{structured.get('company_number','?')}`"
            + (f"  |  *BR No.:* `{structured.get('br_number')}`" if structured.get('br_number') else ''),
            f"*Allotment date:* {structured.get('allotment_date','?')}",
            f"*Shares allotted:* {structured.get('shares_allotted_cash',0)} cash + {structured.get('shares_allotted_non_cash',0) or 0} non-cash",
            f"*Currency / per-share:* {structured.get('currency','?')} {structured.get('amount_paid_per_share','?')} paid",
            "",
            "*Allottees:*",
        ]
        for a in (structured.get("allottees") or []):
            pct = f" ({a.get('percent')}%)" if a.get('percent') is not None else ""
            body_lines.append(f"  • *{a.get('name','?')}* — {a.get('shares','?')} shares{pct}")
            if a.get('address'):
                body_lines.append(f"    {a['address']}")
        body_lines += [
            "",
            f"*Signed by:* {structured.get('signed_by','?')} ({structured.get('signer_capacity','?')})",
            f"*Filed by:* {structured.get('presentor_name','?')}"
            + (f" — {structured.get('presentor_email')}" if structured.get('presentor_email') else ''),
        ]

    elif form_code == "HK_ND4":
        who = structured.get("resigner_name_en") or structured.get("resigner_name_cn") or "?"
        body_lines += [
            f"*Company:* {structured.get('company_name_en','?')}",
            f"*BR No.:* `{structured.get('br_number','?')}`",
            f"*Resigning:* {who} ({'natural person' if structured.get('resigner_is_natural_person') else 'body corporate'})",
            f"*Capacity:* {structured.get('resigner_capacity','?')}",
            f"*Resignation date:* {structured.get('resignation_date','?')}",
            f"*Filing received:* {structured.get('filing_date_received','?')}",
        ]

    elif form_code == "UK_CS01":
        body_lines += [
            f"*Company:* {structured.get('company_name','?')}  |  No. `{structured.get('company_number','?')}`",
            f"*Statement period end:* {structured.get('review_period_end','?')}",
            f"*Registered office:* {structured.get('registered_office','?')}",
            f"*Directors:* {len(structured.get('directors') or [])} on record",
            f"*PSC:* {len(structured.get('psc') or [])} declared",
        ]

    elif form_code == "UK_SH01":
        body_lines += [
            f"*Company:* {structured.get('company_name','?')}  |  No. `{structured.get('company_number','?')}`",
            f"*Allotment from:* {structured.get('allotment_date_from','?')}"
            + (f" to {structured.get('allotment_date_to')}" if structured.get('allotment_date_to') else ''),
        ]
        for s in (structured.get("shares") or []):
            body_lines.append(f"  • {s.get('class','?')}: {s.get('number')} @ {s.get('nominal_value')} {structured.get('currency','')}")

    elif form_code == "GEN_INVOICE":
        body_lines += [
            f"*Invoice #:* {structured.get('invoice_number','?')}",
            f"*Issued:* {structured.get('issue_date','?')}"
            + (f" | *Due:* {structured.get('due_date')}" if structured.get('due_date') else ''),
            f"*Seller:* {structured.get('seller_name','?')}",
            f"*Buyer:* {structured.get('buyer_name','?')}",
            f"*Total:* {structured.get('currency','?')} {structured.get('total','?')}",
        ]

    elif form_code == "GEN_CONTRACT":
        body_lines += [
            f"*Contract:* {structured.get('contract_title','?')}",
            f"*Signed:* {structured.get('execution_date','?')}  |  *Effective:* {structured.get('effective_date','?')}",
            f"*Governing law:* {structured.get('governing_law','?')}",
            "*Parties:*",
        ]
        for p in (structured.get("parties") or []):
            body_lines.append(f"  • *{p.get('name','?')}* ({p.get('role','?')})")

    elif form_code == "GEN_TENDER":
        body_lines += [
            f"*Tender ref:* {structured.get('tender_reference','?')}",
            f"*Issued by:* {structured.get('issuing_authority','?')} ({structured.get('issuing_country','?')})",
            f"*Subject:* {structured.get('subject','?')}",
            f"*Deadline:* {structured.get('submission_deadline','?')}",
            f"*Estimated value:* {structured.get('currency','?')} {structured.get('estimated_value','?')}",
        ]

    elif form_code == "GEN_KYC_ID":
        body_lines += [
            f"*Document:* {structured.get('document_type','?')} ({structured.get('issuing_country','?')})",
            f"*Number:* `{structured.get('document_number','?')}`",
            f"*Holder:* {structured.get('full_name','?')}, DOB {structured.get('date_of_birth','?')}, nationality {structured.get('nationality','?')}",
            f"*Validity:* {structured.get('issue_date','?')} → {structured.get('expiry_date','?')}",
        ]

    else:
        # Generic fallback — just dump the top-level scalar fields.
        body_lines.append("*Extracted fields:*")
        for k, v in structured.items():
            if isinstance(v, (str, int, float)) and v not in (None, "", []):
                body_lines.append(f"  • *{k}:* {v}")

    if redflags:
        body_lines += ["", "🚩 *Red flags*"]
        for f in redflags:
            badge = _SEVERITY_BADGE.get(f.get("severity"), "•")
            body_lines.append(f"  {badge} *{f['code']}* — {f['message']}")

    return "\n".join(body_lines)


# ── PERSISTENCE ─────────────────────────────────────────────────────────────

async def persist_filing(structured: dict, form_code: str, source: str) -> None:
    """Write the most useful facts from a registry filing into the knowledge
    base. Each entity / officer / shareholder becomes a Tier-1 fact when
    the form is from an official regulator.

    Best-effort: any sub-call failure is logged and swallowed — a partial
    persistence is better than blocking the user reply.
    """
    if not structured:
        return
    spec = FORM_CATALOGUE.get(form_code, {})
    is_official = spec.get("tier", 4) == 1 and spec.get("regulator")
    confidence = "CONFIRMED" if is_official else "PROBABLE"
    regulator = spec.get("regulator", "document")

    company_name = (
        structured.get("company_name_en")
        or structured.get("company_name")
        or structured.get("entity_name")
        or structured.get("proposed_company_name")
    )
    company_number = structured.get("company_number") or structured.get("br_number") or structured.get("registration_number")

    # F48 2026-04-29: collect facts here so one add_facts_batch handles
    # all the RAG ingest at the end. A single registry filing produces
    # up to ~60 facts (entity + 20 directors + 30 shareholders + 10
    # PSCs); the previous code path ran 60 separate model.encode calls.
    # F83 2026-04-29: same accumulator pattern for the in-memory
    # semantic index — one model.encode batch instead of N.
    rag_batch: list[dict] = []
    semantic_batch: list[tuple[str, str, dict | None]] = []

    async def _store(topic: str, content: str, fact_type: str = "", entity_name: str = ""):
        try:
            sf_result = await store_fact(
                topic=topic, content=content, source=source, confidence=confidence,
                fact_type=fact_type, entity_name=entity_name or company_name or "",
                entity_type="company" if entity_name == company_name else "",
                skip_rag_ingest=True,
                skip_semantic_index=True,
            )
            rag_batch.append({
                "topic": topic,
                "content": content,
                "confidence": confidence,
                "source": source,
            })
            fid = (sf_result or {}).get("fact_id")
            if fid:
                semantic_batch.append((
                    fid,
                    f"{topic} {content}",
                    {"confidence": confidence},
                ))
        except Exception as e:
            logger.debug("doc_intel persist: %s — %s", topic, e)

    # Anchor fact: the entity itself
    if company_name:
        await _store(
            topic=f"Entity: {company_name}",
            content=f"{regulator} filing {form_code} confirms entity '{company_name}'"
                    + (f" (no. {company_number})" if company_number else "")
                    + f". Source filing: {source}.",
            fact_type="CORPORATE_REGISTRATION",
            entity_name=company_name,
        )

    # Officers
    for d in (structured.get("directors") or [])[:20]:
        n = d.get("name") if isinstance(d, dict) else None
        if n:
            await _store(
                topic=f"Director: {n} of {company_name}",
                content=f"{n} listed as director of {company_name} per {form_code}.",
                fact_type="OFFICER_APPOINTMENT", entity_name=n,
            )
    if structured.get("officer_name"):  # AP01 / TM01 single-officer forms
        n = structured["officer_name"]
        change = "appointed" if "AP" in form_code else ("terminated" if "TM" in form_code else "noted")
        await _store(
            topic=f"Officer change: {n} at {company_name}",
            content=f"{n} {change} as officer of {company_name} per {form_code} on {structured.get('appointment_date') or structured.get('termination_date') or '?'}.",
            fact_type="OFFICER_APPOINTMENT", entity_name=n,
        )
    if form_code == "HK_ND4":
        n = structured.get("resigner_name_en") or structured.get("resigner_name_cn")
        if n:
            await _store(
                topic=f"Resignation: {n} from {company_name}",
                content=f"{n} resigned as {structured.get('resigner_capacity','officer')} of {company_name} on {structured.get('resignation_date','?')}.",
                fact_type="OFFICER_APPOINTMENT", entity_name=n,
            )

    # Shareholders / allottees
    holders = structured.get("allottees") or structured.get("members") or structured.get("shareholders") or []
    for h in holders[:30]:
        if not isinstance(h, dict):
            continue
        n = h.get("name")
        if not n:
            continue
        shares = h.get("shares") or h.get("number")
        await _store(
            topic=f"Shareholder: {n} of {company_name}",
            content=f"{n} holds {shares or '?'} shares in {company_name} per {form_code}.",
            fact_type="BENEFICIAL_OWNERSHIP", entity_name=n,
        )

    # PSCs
    for p in (structured.get("psc") or [])[:10]:
        if isinstance(p, dict) and p.get("name"):
            await _store(
                topic=f"PSC: {p['name']} of {company_name}",
                content=f"{p['name']} declared as Person of Significant Control of {company_name}: "
                        f"{', '.join(p.get('nature_of_control') or [])}.",
                fact_type="BENEFICIAL_OWNERSHIP", entity_name=p["name"],
            )

    # F48 / F83: flush both batched indexes in one model.encode pass
    # each. add_facts_batch + index_facts_batch are no-op-safe on
    # empty input, so the early-return paths above (no company_name,
    # no entries) silently skip these.
    if rag_batch:
        try:
            from . import rag_store as _rag
            await _rag.add_facts_batch(rag_batch)
        except Exception as e:
            logger.debug("doc_intel rag_store.add_facts_batch failed: %s", e)
    if semantic_batch:
        try:
            from . import semantic_search as _ss
            import asyncio as _aio
            await _aio.to_thread(_ss.index_facts_batch, semantic_batch)
        except Exception as e:
            logger.debug("doc_intel semantic_search.index_facts_batch failed: %s", e)


# ── ORCHESTRATOR ────────────────────────────────────────────────────────────

async def process_document(
    *,
    text: str,
    filename: str,
    source: str,
    llm: Any,
) -> Optional[dict]:
    """Full pipeline: classify → extract → analyse → render → persist.

    Returns:
        {
          "form_code": "HK_NSC1",
          "form_name": "Return of Allotment",
          "jurisdiction": "HK",
          "structured": {...},
          "red_flags": [...],
          "overview_markdown": "..."
        }
    Or None if extraction failed entirely (caller falls back to raw text).
    """
    if not text or len(text.strip()) < 30:
        return None

    form_code = classify_document(text, filename)
    structured = await extract_structured(text, form_code, llm)
    if not structured and form_code != "GEN_DOCUMENT":
        # Specific form failed — retry as generic so we still produce something
        logger.info("doc_intel: %s extraction failed, retrying as GEN_DOCUMENT", form_code)
        form_code = "GEN_DOCUMENT"
        structured = await extract_structured(text, form_code, llm)
    if not structured:
        return None

    redflags = analyse_redflags(structured, form_code)
    overview_body = render_overview(form_code, structured, redflags)

    # Mint a stable id and prepend the draft / verified / corrected marker so
    # nothing leaves WA looking definitive without human sign-off, and the
    # team can refer back via /docverify <id> or /docfix <id> <field>: <value>.
    extraction_id = _corr.new_extraction_id()
    spec = FORM_CATALOGUE.get(form_code, FORM_CATALOGUE["GEN_DOCUMENT"])
    needs_review = spec.get("tier", 4) >= 2 or any(f.get("severity") == "high" for f in redflags)
    status_line = (
        "📝 *DRAFT — awaiting human verification*"
        if needs_review
        else "🟢 *Tier-1 official source — auto-confirmed pending review*"
    )
    overview = (
        f"{status_line}\n*ID:* `{extraction_id}`  ·  reply `/docverify {extraction_id}` "
        f"or `/docfix {extraction_id} <field>: <value>`\n\n{overview_body}"
    )

    # Persist (best-effort) to the knowledge base AND the corrections store
    try:
        await persist_filing(structured, form_code, source)
    except Exception as e:
        logger.debug("doc_intel persist outer-error: %s", e)
    try:
        await _corr.save_extraction(
            extraction_id=extraction_id, form_code=form_code,
            filename=filename, source=source,
            structured=structured, overview_markdown=overview,
        )
    except Exception as e:
        logger.debug("doc_intel corrections-store save failed: %s", e)

    return {
        "extraction_id": extraction_id,
        "form_code": form_code,
        "form_name": spec["name"],
        "jurisdiction": spec["jurisdiction"],
        "tier": spec.get("tier", 4),
        "needs_review": needs_review,
        "structured": structured,
        "red_flags": redflags,
        "overview_markdown": overview,
    }
