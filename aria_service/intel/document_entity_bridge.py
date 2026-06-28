"""Document-to-entity bridge.

Extracted from the F3 / SERBAN DD learning 2026-04-17 23:15: when the
operator attaches a corporate-registry document (Sunbiz, Companies
House, Handelsregister, etc.) and asks "run DD on this company",
the DD orchestrator was previously running on the literal pronoun
phrase ("this company within the attached document") because the
`[ATTACHED DOCUMENT]` block was only consumed by the LLM layer —
never parsed by the DD pipeline.

Consequence: every rule-based detector I built today (Sunbiz lookup,
virtual-office registry, FinCEN BOI caveat, sanctions propagation,
bright-lines) was blind, because they all key on a real entity name
+ jurisdiction + address. The LLM recovered by reasoning from the
document text, but the structured intel layer missed its chance to
contribute hard signals.

This module is the bridge:
  - detect when the DD captured entity is a pronoun reference
  - extract name / jurisdiction / address / registration number /
    incorporation date from common registry formats in the attached
    document block
  - populate a proper DD target dict so every downstream detector
    fires on real data

Supported formats:
  - US Florida Sunbiz "Detail by Entity Name"
  - US Delaware / other states (generic)
  - UK Companies House "Company overview"
  - Germany Handelsregister
  - Romania ONRC / ANAF
  - Generic fallback (takes first plausible company-shape name)
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("aria.intel.document_entity_bridge")


# ═══════════════════════════════════════════════════════════════════════
# Pronoun detection
# ═══════════════════════════════════════════════════════════════════════
#
# Names that indicate "use the attached document as the entity source".
# Exact-match and substring-match both supported because phrasing varies:
#   "this company"
#   "the company in the attached document"
#   "this entity"

_PRONOUN_PHRASES: tuple[str, ...] = (
    "this company",
    "this entity",
    "this firm",
    "this business",
    "this counterparty",
    "this organisation",
    "this organization",
    "this party",
    "the company",
    "the entity",
    "the firm",
    "the business",
    "the counterparty",
    "the party",
    "this attached company",
    "the attached company",
    "company in the document",
    "entity in the document",
    "company in the attached document",
    "entity in the attached document",
    "company within the attached document",
    "entity within the attached document",
    "company within the document",
    "entity within the document",
    "the document",
    "this document",
)


def is_pronoun_reference(entity_name: str) -> bool:
    """Return True if the name looks like a pronoun reference rather
    than a real entity name. Used by the DD intent path to decide
    whether to consult the attached document for the real entity."""
    if not entity_name:
        return False
    clean = entity_name.lower().strip().rstrip(".,;:!?")
    # Exact match
    if clean in _PRONOUN_PHRASES:
        return True
    # Substring match — catches "DD on this company please" variations
    for phrase in _PRONOUN_PHRASES:
        if phrase in clean:
            return True
    return False


# ═══════════════════════════════════════════════════════════════════════
# Extraction patterns
# ═══════════════════════════════════════════════════════════════════════

# Extract the raw document text from a chat message.
_ATTACHED_DOC_RE = re.compile(
    r"\[ATTACHED\s+DOCUMENT[^\]]*\](.*?)\[END\s+ATTACHED\s+DOCUMENT\]",
    re.IGNORECASE | re.DOTALL,
)


# ── Company-name patterns ─────────────────────────────────────────────
# Each pattern yields the entity name as capture group 1.
_NAME_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Florida Sunbiz: line format "Entity Name: ABC COMPANY LLC"
    re.compile(r"(?:^|\n)\s*(?:Entity|Corporation|Company|Legal|Full\s+Legal)\s*Name\s*:\s*([A-Z][A-Z0-9&.,\s'/\-]{3,120})(?=\n|$)", re.IGNORECASE | re.MULTILINE),
    # "Name: XYZ Ltd"
    re.compile(r"(?:^|\n)\s*Name\s*:\s*([A-Z][A-Za-z0-9&.,\s'/\-]{3,120}(?:LLC|Ltd|Inc|Corp|SRL|SA|AG|GmbH|NV|BV|SAS|PLC|LLP|LP))(?=\n|[.,])", re.IGNORECASE | re.MULTILINE),
    # UK Companies House: "Company name: Acme Industries Limited"
    re.compile(r"(?:^|\n)\s*Company\s+name\s*:\s*([A-Z][A-Za-z0-9&.,\s'/\-]{3,120})(?=\n|$)", re.IGNORECASE | re.MULTILINE),
    # UK Companies House overview: "Company overview for ACME INDUSTRIES LIMITED"
    re.compile(r"Company\s+overview\s+for\s+([A-Z][A-Za-z0-9&.,\s'/\-]{3,120})(?=\n|$)", re.IGNORECASE | re.MULTILINE),
    # Free-standing company name line (all caps ending in LLC/INC/CORP/etc.)
    # Tightened to avoid matching the document header ("DETAIL BY ENTITY NAME")
    re.compile(r"(?:^|\n)\s*([A-Z][A-Z0-9&.,\s'/\-]{3,120}?(?:\s+(?:LLC|INC|CORP|CORPORATION|LTD|LIMITED|SRL|SA|AG|GMBH|NV|BV|SAS|PLC|LLP|LP)\.?))(?=\s*\n|$)", re.MULTILINE),
)


# ── Registration-number patterns ──────────────────────────────────────
_REG_NUMBER_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # Florida Sunbiz — Document Number: L20000121167
    ("US-FL", re.compile(r"Document\s+Number\s*:\s*([A-Z0-9]{6,20})", re.IGNORECASE)),
    # FEI/EIN — used by many US states
    ("US",    re.compile(r"FEI(?:/EIN)?\s*(?:Number)?\s*:\s*(\d{2}-?\d{7})", re.IGNORECASE)),
    # UK Companies House — Company number: 12345678
    ("GB",    re.compile(r"Company\s+number\s*:\s*(\d{6,10}|[A-Z]{2}\d{6})", re.IGNORECASE)),
    # Germany Handelsregister — "HRB 12345 Frankfurt"
    ("DE",    re.compile(r"\b(HR[ABG]\s*\d{3,8}|GnR\s*\d+|PR\s*\d+|VR\s*\d+)\b", re.IGNORECASE)),
    # Romania CUI / CIF — RO12345678
    ("RO",    re.compile(r"\b(?:CUI|CIF|RO)\s*:?\s*(\d{5,10})\b", re.IGNORECASE)),
    # Poland NIP / KRS
    ("PL",    re.compile(r"\b(?:NIP|KRS)\s*:?\s*(\d{6,13})\b", re.IGNORECASE)),
    # Brazil CNPJ
    ("BR",    re.compile(r"\bCNPJ\s*:?\s*(\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2})\b", re.IGNORECASE)),
)


# ── Jurisdiction markers ──────────────────────────────────────────────
_JURISDICTION_HINTS: tuple[tuple[str, str], tuple[str, str], ...] = (
    ("US-FL", "US"), ("US-DE", "US"), ("US-NY", "US"), ("US-CA", "US"),
    ("US-TX", "US"), ("US-NV", "US"), ("US-WY", "US"),
)

# State-in-address regex:  "City, XX 12345" → XX
_US_STATE_ZIP_RE = re.compile(r"\b([A-Z]{2})\s+\d{5}(?:-\d{4})?\b")

# Full jurisdiction keywords in free text
_JURISDICTION_KEYWORDS = {
    "florida": "US", "fl department of state": "US",
    "delaware": "US", "delaware secretary of state": "US",
    "new york": "US", "california": "US", "texas": "US",
    "nevada": "US", "wyoming": "US",
    "united states": "US", "usa": "US",
    "united kingdom": "GB", "england & wales": "GB", "scotland": "GB",
    "companies house": "GB",
    "romania": "RO", "românia": "RO", "onrc": "RO",
    "poland": "PL", "polska": "PL", "krs": "PL",
    "germany": "DE", "deutschland": "DE", "handelsregister": "DE",
    "italy": "IT", "italia": "IT",
    "france": "FR",
    "spain": "ES", "españa": "ES",
    "brazil": "BR", "brasil": "BR",
    "nigeria": "NG", "angola": "AO", "mozambique": "MZ",
    "uae": "AE", "united arab emirates": "AE",
    "saudi arabia": "SA", "ksa": "SA",
    "türkiye": "TR", "turkey": "TR",
}


# ── Address patterns ──────────────────────────────────────────────────
# Focus on "Principal Address" / "Registered office" / "Mailing Address"
_ADDRESS_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:Principal|Registered|Mailing)\s*(?:Office\s*)?Address\s*:\s*([^\n]+(?:\n[^\n]+){0,3})(?=\n\s*(?:Registered|Mailing|Officer|Manager|FEI|Document|$))", re.IGNORECASE),
    re.compile(r"(?:Registered|Principal)\s*Office\s*Address\s*:?\s*\n([^\n]+(?:\n[^\n]+){0,3})", re.IGNORECASE),
    re.compile(r"(?:Address|Street)\s*:\s*([^\n]+(?:\n[^\n]+){0,3})(?=\n\s*[A-Z])", re.IGNORECASE),
)


# ── Incorporation date ────────────────────────────────────────────────
_INCORP_DATE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:Date\s+(?:of\s+)?(?:Incorporation|Formation|Registration|Filing)|Incorporated|Formed)\s*:?\s*(\d{2}[/-]\d{2}[/-]\d{4}|\d{4}[/-]\d{2}[/-]\d{2})", re.IGNORECASE),
    re.compile(r"Date\s+Filed\s*:?\s*(\d{2}[/-]\d{2}[/-]\d{4})", re.IGNORECASE),
)


# ── Director / Officer ────────────────────────────────────────────────
# Sunbiz pattern: the role-header line ends with "Name & Address:" (not
# "Name:"), then the next non-empty line is the person's name. Handle
# that specific shape AND the simpler "Name: X" form.
_OFFICER_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Sunbiz "Registered Agent Name & Address:" followed by NAME on next line
    re.compile(
        r"(?:Registered\s+Agent|Authori[sz]ed\s+Person(?:\(s\))?|Officer|Director|Manager)"
        r"[\s\S]{0,40}?"
        r"(?:Name\s*&\s*Address\s*:|Name\s*:|Detail\s*:|\s*\n)"
        r"\s*\n?\s*"
        r"(?:Title\s+\w+\s*\n\s*)?"                     # optional "Title MGR\n"
        r"([A-Z][A-Z0-9,\s'.\-]{3,60}?)"
        r"(?=\s*\n)",
        re.IGNORECASE,
    ),
    # Simple: "Manager: NAME"
    re.compile(r"(?:Manager|MGR|Authorized\s+Person)\s*:\s*([A-Z][A-Z0-9,\s'.\-]{3,60})(?=\n|$)", re.IGNORECASE | re.MULTILINE),
    # UK Companies House directors list — "JOHN DOE\nAppointed on ..."
    re.compile(
        r"(?:Directors?|Officers?)\s*\n"
        r"([A-Z][A-Z\s',.\-]{5,60})\s*\n"
        r"Appointed\s+on",
        re.IGNORECASE,
    ),
)


# ═══════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════

def extract_attached_document_text(message: str) -> str | None:
    """Pull the raw body out of an `[ATTACHED DOCUMENT ... END ATTACHED
    DOCUMENT]` block. Returns None if no block is present."""
    if not message or "ATTACHED DOCUMENT" not in message.upper():
        return None
    m = _ATTACHED_DOC_RE.search(message)
    if not m:
        return None
    body = m.group(1).strip()
    return body or None


def extract_entity_from_document(doc_text: str) -> dict[str, Any]:
    """Parse a registry-document text block and return a DD-ready
    target dict. Never raises — returns best-effort fields, empty
    strings for missing items.

    Output keys (compatible with dd_orchestrator.orchestrate_dd target):
      - name                    canonical entity name (required for DD to run)
      - jurisdiction_iso2       inferred ISO-2 country code (e.g. "US")
      - jurisdiction            full jurisdiction name when available
      - registered_address      address string (multi-line)
      - registration_number     registry-assigned number
      - incorporation_date      YYYY-MM-DD when parseable
      - directors               [{"name": "..."}]
      - type                    "company" (default)
      - source                  "document_entity_bridge"
    """
    out: dict[str, Any] = {
        "name": "",
        "jurisdiction_iso2": "",
        "jurisdiction": "",
        "registered_address": "",
        "registration_number": "",
        "incorporation_date": "",
        "directors": [],
        "type": "company",
        "source": "document_entity_bridge",
    }
    if not doc_text or not doc_text.strip():
        return out

    text = doc_text

    # ── Name ──
    for pat in _NAME_PATTERNS:
        m = pat.search(text)
        if m:
            name = m.group(1).strip().rstrip(".,;:")
            # Avoid selecting the document HEADER ("DETAIL BY ENTITY NAME")
            # as the entity. Require at least one entity-suffix token.
            upper = name.upper()
            if any(sfx in upper for sfx in (
                "LLC", "INC", "CORP", "CORPORATION", "LTD", "LIMITED",
                "SRL", "SA ", " SA", "AG ", " AG", "GMBH", "NV ", " NV",
                "BV ", " BV", "SAS", "PLC", "LLP", "LP ", " LP",
            )):
                out["name"] = " ".join(name.split())  # collapse whitespace
                break
    # Fallback: if no suffix-style name found, take the first all-caps line
    # with at least 3 words.
    if not out["name"]:
        for line in text.splitlines()[:20]:
            s = line.strip()
            if len(s.split()) >= 3 and s.isupper() and len(s) < 120:
                out["name"] = " ".join(s.split())
                break

    # ── Jurisdiction ──
    lower = text.lower()
    for keyword, iso2 in _JURISDICTION_KEYWORDS.items():
        if keyword in lower:
            out["jurisdiction_iso2"] = iso2
            out["jurisdiction"] = keyword.title() if iso2 != "US" else "United States"
            break

    # ── Registration number + implied jurisdiction ──
    for implied_iso2, pat in _REG_NUMBER_PATTERNS:
        m = pat.search(text)
        if m:
            out["registration_number"] = m.group(1).strip()
            # Only set ISO2 from pattern if we don't already have one
            if not out["jurisdiction_iso2"]:
                # Normalise US-XX → US (we track state separately via address)
                out["jurisdiction_iso2"] = implied_iso2.split("-")[0]
            break

    # ── Address ──
    for pat in _ADDRESS_PATTERNS:
        m = pat.search(text)
        if m:
            raw = m.group(1)
            # Collapse multi-line addresses into one comma-separated line
            lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
            out["registered_address"] = ", ".join(lines[:4])
            break

    # Back-infer US jurisdiction from ST+ZIP in the address
    if not out["jurisdiction_iso2"] and out["registered_address"]:
        sm = _US_STATE_ZIP_RE.search(out["registered_address"])
        if sm:
            out["jurisdiction_iso2"] = "US"
            out["jurisdiction"] = "United States"

    # ── Incorporation date ──
    for pat in _INCORP_DATE_PATTERNS:
        m = pat.search(text)
        if m:
            raw = m.group(1)
            # Normalise to ISO YYYY-MM-DD
            out["incorporation_date"] = _normalise_date(raw)
            break

    # ── Directors / Officers ──
    seen_directors: set[str] = set()
    for pat in _OFFICER_PATTERNS:
        for m in pat.finditer(text):
            nm = m.group(1).strip().rstrip(".,;:")
            nm_key = nm.upper()
            if nm_key in seen_directors:
                continue
            # Filter out known non-name captures ("Authorized", "Detail", etc.)
            if nm_key in ("AUTHORIZED", "DETAIL", "ADDRESS", "ADDRESSES",
                          "TITLE", "STATUS", "ACTIVE", "CORPORATION"):
                continue
            seen_directors.add(nm_key)
            out["directors"].append({"name": nm})
            if len(out["directors"]) >= 10:
                break
        if len(out["directors"]) >= 10:
            break

    return out


def bridge_from_message(message: str, entity_hint: str = "") -> dict[str, Any] | None:
    """End-to-end helper: given a chat message that contains an
    [ATTACHED DOCUMENT] block (and optionally a pronoun entity hint),
    return a DD-ready target dict, or None if no extraction is needed
    or possible.

    Returns None when:
      - The message has no attached-document block.
      - entity_hint is a REAL company name (not a pronoun) — in that
        case the caller should trust the hint and not override.
      - Extraction found no entity name (nothing to bridge).
    """
    doc_text = extract_attached_document_text(message)
    if not doc_text:
        return None

    # If caller provided a non-pronoun hint, trust it — don't override.
    if entity_hint and not is_pronoun_reference(entity_hint):
        # But we may still enrich jurisdiction / address if they weren't
        # set externally. Caller can choose to merge.
        enrichment = extract_entity_from_document(doc_text)
        if enrichment["name"]:
            enrichment["name"] = entity_hint  # keep caller's name
        enrichment["_hint_kept"] = True
        return enrichment

    # Pronoun hint (or no hint) → full extraction from document
    extracted = extract_entity_from_document(doc_text)
    if not extracted.get("name"):
        return None
    logger.info(
        "[document_entity_bridge] pronoun → '%s' (jurisdiction=%s, reg=%s)",
        extracted["name"],
        extracted.get("jurisdiction_iso2") or "?",
        extracted.get("registration_number") or "?",
    )

    # Brain signal — pronoun-to-entity bridging is a high-value
    # comprehension signal. Tracks how often the bridge resolves
    # underspecified references, plus jurisdiction inference quality.
    try:
        import asyncio as _aio
        from . import brain_hook as _bh

        async def _emit():
            await _bh.absorb(
                module="document_entity_bridge",
                summary=(
                    f"Pronoun '{entity_hint}' → '{extracted['name']}' "
                    f"jurisdiction={extracted.get('jurisdiction_iso2','?')} "
                    f"reg={extracted.get('registration_number','?')}"
                ),
                detail=str({k: v for k, v in extracted.items() if k != "directors"})[:1500],
                entity_name=extracted["name"],
                success=True,
                gap_type=("entity_no_jurisdiction"
                          if not extracted.get("jurisdiction_iso2") else None),
                gap_detail=(f"Could not infer jurisdiction for '{extracted['name']}'"
                            if not extracted.get("jurisdiction_iso2") else None),
                confidence="ASSESSED",
            )

        try:
            loop = _aio.get_running_loop()
            loop.create_task(_emit())
        except RuntimeError:
            pass
    except Exception:
        pass

    # R-F996 — wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="document_entity_bridge",
        summary="Bridge From Message",
        source_id="document_entity_bridge:R-F996",
    )

    return extracted


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════

def _normalise_date(raw: str) -> str:
    """Convert MM/DD/YYYY, DD/MM/YYYY, or YYYY-MM-DD to YYYY-MM-DD.
    US format is the default interpretation when ambiguous since the
    primary use case right now is Sunbiz + Delaware."""
    if not raw:
        return ""
    raw = raw.strip()
    # Already ISO
    m = re.match(r"^(\d{4})[-/](\d{2})[-/](\d{2})$", raw)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    # MM/DD/YYYY (US default) or DD/MM/YYYY
    m = re.match(r"^(\d{2})[/-](\d{2})[/-](\d{4})$", raw)
    if m:
        a, b, yyyy = m.group(1), m.group(2), m.group(3)
        # If first component > 12, it's DD/MM/YYYY; otherwise assume US MM/DD/YYYY
        if int(a) > 12:
            return f"{yyyy}-{b}-{a}"
        return f"{yyyy}-{a}-{b}"
    return raw  # best-effort; return as-is

# R-F2119 §21a — wire failure handler for document_entity_bridge
try:
    wire_failure(module="document_entity_bridge", detail="module shutdown",
                gap_type="engine_failure", source="document_entity_bridge:shutdown")
except Exception:
    pass
