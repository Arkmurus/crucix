# =============================================================================
# ARIA v3.1 — Technical Specification Writer
# aria_service/writers/tech_spec_writer.py
#
# Produces defence technical specifications that:
#   - Reference NATO STANAGs correctly
#   - Use NATO Stock Numbers (NSN) for equipment identification
#   - Define acceptance criteria that are objectively verifiable
#   - Comply with non-discrimination requirements (capability-based, not brand-specific)
#   - Include ILS (Integrated Logistics Support) requirements
# =============================================================================

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

try:
    import anthropic
except ImportError:
    anthropic = None

logger = logging.getLogger("ARIA.TechSpecWriter")


class SpecificationLevel(Enum):
    SYSTEM          = "System-level specification"
    SUBSYSTEM       = "Sub-system specification"
    COMPONENT       = "Component specification"
    PERFORMANCE     = "Performance specification"
    FUNCTIONAL      = "Functional specification"
    INTERFACE       = "Interface specification"


class VerificationMethod(Enum):
    TEST            = "T — Test (empirical measurement)"
    DEMONSTRATION   = "D — Demonstration (observable operation)"
    ANALYSIS        = "A — Analysis (calculation or modelling)"
    INSPECTION      = "I — Inspection (visual or dimensional check)"


# KEY NATO STANAG REFERENCES
STANAG_REFERENCES = {
    "4586": "NATO STANAG 4586 — UCS (Unmanned Control System) interoperability",
    "2494": "NATO STANAG 2494 — Military vehicle systems interfaces",
    "3600": "NATO STANAG 3600 — Instrument landing system",
    "4187": "NATO STANAG 4187 — Fueling systems for military equipment",
    "4193": "NATO STANAG 4193 — Electrical power supply",
    "4218": "NATO STANAG 4218 — Small arms ammunition interoperability",
    "4425": "NATO STANAG 4425 — Aeromedical evacuation",
    "4569": "NATO STANAG 4569 — Protection levels for occupants of armoured vehicles",
    "4594": "NATO STANAG 4594 — Interoperability of NATO communication systems",
    "6001": "NATO STANAG 6001 — Language proficiency levels",
    "7170": "NATO STANAG 7170 — Ship structures",
    "2895": "NATO STANAG 2895 — Extreme climatic conditions",
}


@dataclass
class TechSpecSection:
    section_number: str
    title:          str
    content:        str
    stanag_refs:    list[str] = field(default_factory=list)
    nsn_refs:       list[str] = field(default_factory=list)
    verification:   VerificationMethod = VerificationMethod.TEST


@dataclass
class TechSpecRequest:
    platform_type:      str              # e.g. "Armoured Personnel Carrier"
    capability_required: str            # Performance requirement in plain English
    operating_environment: str          # e.g. "Tropical, 40°C ambient, high humidity"
    nato_interoperability: bool         # Must comply with NATO standards
    ils_required:       bool            # Integrated Logistics Support spec needed
    threat_levels:      list[str]       # e.g. ["STANAG 4569 Level 2", "IED Level 3"]
    specific_stanags:   list[str]       # Specific STANAG numbers required
    delivery_country:   str
    quantity:           int
    additional_reqs:    str = ""
    output_language:    str = "en"


class TechSpecWriter:
    """Produces defence technical specifications referencing NATO standards."""

    SYSTEM_PROMPT = """You are ARIA's technical specification writing module.
You produce capability-based technical specifications for defence procurement.

Rules:
1. Specifications MUST be capability-based not brand-specific
   (say "minimum ballistic protection equivalent to STANAG 4569 Level 2"
    not "must use RAFAEL Trophy APS")
2. Every performance requirement must have a verification method (T/D/A/I)
3. Reference NATO STANAGs where applicable — cite the standard number and edition
4. Use NSN format (XXXX-XX-XXX-XXXX) for equipment identification where known
5. ILS requirements must cover: reliability (MTBF), availability, maintainability (MTTR),
   supply chain, technical documentation, training
6. All threshold values must be objectively measurable
Return ONLY valid JSON."""

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6"):
        if anthropic is None:
            raise RuntimeError(
                "TechSpecWriter requires the `anthropic` SDK. "
                "Install via `pip install anthropic` or add it to requirements."
            )
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model

    def write(self, request: TechSpecRequest) -> dict:
        relevant_stanags = {
            k: v for k, v in STANAG_REFERENCES.items()
            if k in request.specific_stanags
        }

        prompt = f"""Write a technical specification for:

PLATFORM: {request.platform_type}
CAPABILITY REQUIRED: {request.capability_required}
OPERATING ENVIRONMENT: {request.operating_environment}
NATO INTEROPERABILITY: {request.nato_interoperability}
ILS REQUIRED: {request.ils_required}
THREAT LEVELS: {', '.join(request.threat_levels)}
RELEVANT STANAGS: {json.dumps(relevant_stanags, indent=2)}
DELIVERY COUNTRY: {request.delivery_country}
QUANTITY: {request.quantity}
ADDITIONAL: {request.additional_reqs}

Return JSON:
{{
  "document_title": "Technical Specification for [platform]",
  "spec_level": "System|Subsystem|Component|Performance|Functional",
  "sections": [
    {{
      "section_number": "1",
      "title": "Scope and Purpose",
      "content": "Full text of section",
      "stanag_refs": ["4569"],
      "verification_method": "I"
    }}
  ],
  "performance_requirements": [
    {{
      "id": "PR-001",
      "category": "Mobility",
      "requirement": "The system shall achieve a minimum road speed of...",
      "threshold": "Minimum acceptable value",
      "objective": "Desired value",
      "verification": "T",
      "stanag_ref": null
    }}
  ],
  "ils_requirements": {{
    "mtbf_hours": 500,
    "mttr_hours": 2,
    "availability_pct": 90,
    "support_concept": "Two-level maintenance...",
    "documentation_standard": "NATO S1000D or equivalent",
    "training_requirement": "Operator and maintainer training..."
  }},
  "acceptance_criteria": "How the specification will be verified at delivery",
  "applicable_standards": ["List of all standards referenced"]
}}"""

        from ._resilient_llm import resilient_complete
        rr = resilient_complete(
            anthropic_client=self.client,
            anthropic_model=self.model,
            system_prompt=self.SYSTEM_PROMPT,
            user_prompt=prompt,
            max_tokens=3000,
        )
        self._last_llm_degraded = rr.degraded
        self._last_llm_model = rr.model_used
        self._last_llm_reason = rr.reason
        raw = re.sub(r"```json\s*|\s*```", "", rr.text).strip()
        data = json.loads(raw)
        data["platform"] = request.platform_type
        data["produced_by"] = "ARIA — Arkmurus Research Intelligence Agent"
        data["produced_at"] = datetime.now(timezone.utc).isoformat()

        # Brain signal — tech specs reference STANAGs + ILS requirements +
        # threat-level mapping; these are the most technical training pairs
        # ARIA produces and feed mastery on the technical/procurement axes.
        try:
            import asyncio as _aio
            from ..intel import brain_hook as _bh

            n_sections = len(data.get("sections", []))
            n_perf = len(data.get("performance_requirements", []))
            stanags = sorted(set(request.specific_stanags or []))[:5]

            async def _emit():
                await _bh.absorb(
                    module="tech_spec_writer",
                    summary=(
                        f"Tech spec: {request.platform_type} — {n_sections} sections, "
                        f"{n_perf} performance reqs, STANAGs={stanags}"
                    ),
                    detail=str(data.get("acceptance_criteria", ""))[:1500],
                    entity_name=request.platform_type[:120],
                    success=True,
                    extra_topics=["technical", "procurement"],
                    confidence="CONFIRMED",
                )

            try:
                loop = _aio.get_running_loop()
                loop.create_task(_emit())
            except RuntimeError:
                pass
        except Exception:
            pass

        return data


# =============================================================================
# ARIA v3.1 — Portuguese Legal Writer
# aria_service/writers/portuguese_legal_writer.py
#
# Produces Portuguese-language legal and procurement documents in the
# correct administrative register for each Lusophone jurisdiction.
#
# SUPPORTED VARIANTS:
#   PT-AO — Angola (Português angolano — administrative register)
#   PT-MZ — Mozambique (Português moçambicano — official procurement language)
#   PT-GW — Guinea-Bissau (Português guineense — official language)
#   PT-CV — Cape Verde (Português cabo-verdiano)
#   PT-PT — European Portuguese (for CPLP diplomatic correspondence)
#
# DOCUMENT TYPES:
#   - Carta oficial (official letter to ministry)
#   - Nota verbal (diplomatic note)
#   - Proposta comercial (commercial proposal)
#   - Carta de intenção / LOI (letter of intent)
#   - Resposta a concurso (tender response)
#   - Parecer técnico (technical opinion)
#   - Acordo de representação (representative agreement)
# =============================================================================

class PortugueseVariant(Enum):
    PT_AO = "pt-AO"   # Angola
    PT_MZ = "pt-MZ"   # Mozambique
    PT_GW = "pt-GW"   # Guinea-Bissau
    PT_CV = "pt-CV"   # Cape Verde
    PT_PT = "pt-PT"   # European Portuguese


class PortugueseDocumentType(Enum):
    CARTA_OFICIAL         = "Carta Oficial"
    NOTA_VERBAL           = "Nota Verbal"
    PROPOSTA_COMERCIAL    = "Proposta Comercial"
    CARTA_INTENCAO        = "Carta de Intenção (LOI)"
    RESPOSTA_CONCURSO     = "Resposta a Concurso Público"
    PARECER_TECNICO       = "Parecer Técnico"
    ACORDO_REPRESENTACAO  = "Acordo de Representação"
    MEMORANDO             = "Memorando de Entendimento (MoU)"


# Jurisdiction-specific formal conventions
JURISDICTION_CONVENTIONS = {
    PortugueseVariant.PT_AO: {
        "greeting_formal": "Excelentíssimo(a) Senhor(a)",
        "greeting_minister": "Excelentíssimo Senhor Ministro",
        "closing_formal": "Com os melhores cumprimentos e elevada estima e consideração,",
        "signing_block": "[Nome]\n[Cargo]\n[Organização]\nLuanda, [data]",
        "procurement_body": "Instituto Nacional de Contratação Pública (INCOP)",
        "official_gazette": "Diário da República de Angola",
        "currency": "AOA (Kwanza)",
        "date_format": "Luanda, aos {day} de {month} de {year}",
        "notes": [
            "Angolan administrative Portuguese uses 'Vossa Excelência' for ministers",
            "SIMPORTEX correspondence requires reference to Decreto Presidencial",
            "Contracts must reference Lei dos Contratos Públicos (DP 66/21)",
        ],
    },
    PortugueseVariant.PT_MZ: {
        "greeting_formal": "Excelentíssimo(a) Senhor(a)",
        "greeting_minister": "Sua Excelência, o Senhor Ministro",
        "closing_formal": "Com os melhores cumprimentos,",
        "signing_block": "[Nome]\n[Cargo]\n[Organização]\nMaputo, [data]",
        "procurement_body": "UFSA (Unidade Funcional de Supervisão das Aquisições)",
        "official_gazette": "Boletim da República",
        "currency": "MZN (Metical)",
        "date_format": "Maputo, {day} de {month} de {year}",
        "notes": [
            "Mozambican formal Portuguese is closer to European standard",
            "Public procurement uses Portal de Compras do Estado (PCE)",
            "Reference Lei n.º 9/2016 for public procurement",
        ],
    },
    PortugueseVariant.PT_GW: {
        "greeting_formal": "Excelentíssimo(a) Senhor(a)",
        "greeting_minister": "Excelentíssimo Senhor Ministro",
        "closing_formal": "Com elevada estima e consideração,",
        "signing_block": "[Nome]\n[Cargo]\n[Organização]\nBissau, [data]",
        "procurement_body": "Ministério das Finanças — Direcção Nacional do Tesouro",
        "official_gazette": "Boletim Oficial da República da Guiné-Bissau",
        "currency": "XOF (Franco CFA)",
        "date_format": "Bissau, {day} de {month} de {year}",
        "notes": [
            "Guinea-Bissau Portuguese has strong French-influenced administrative vocabulary",
            "Presidential decree mandates require specific formal reference structure",
            "UEMOA framework applies alongside CPLP norms",
        ],
    },
}


@dataclass
class PortugueseDocumentRequest:
    document_type:  PortugueseDocumentType
    variant:        PortugueseVariant
    sender:         str              # Name and title of sender
    sender_org:     str
    recipient:      str              # Name and title of recipient
    recipient_org:  str
    subject:        str
    body_intent:    str              # What the document should say (in English is fine)
    reference_docs: list[str]       # Any documents to reference
    contract_value: Optional[float] = None
    currency:       Optional[str]   = None
    deadline:       Optional[str]   = None


class PortugueseLegalWriter:
    """
    Produces formal Portuguese-language legal and procurement documents
    in the correct administrative register for each Lusophone jurisdiction.
    """

    SYSTEM_PROMPT = """You are ARIA's Portuguese-language legal document writer.
You produce formal documents in the correct administrative register for each
Lusophone African jurisdiction: Angola (PT-AO), Mozambique (PT-MZ),
Guinea-Bissau (PT-GW), and Cape Verde (PT-CV).

The difference that matters is register and convention, not vocabulary.
Angolan administrative Portuguese has specific formulations for ministerial
correspondence that differ from European Portuguese. You know these.

Rules:
1. Use the correct formal register for the jurisdiction — not generic Portuguese
2. Opening and closing formulae must match the specific jurisdiction's conventions
3. Legal references must cite the correct Angolan/Mozambican/Guinean legislation
4. Currency must be in the correct local denomination
5. Dates must follow the local format convention
6. Do not anglicise — produce authentic-sounding document, not a translation
Return the document as a JSON object with the full Portuguese text."""

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6"):
        if anthropic is None:
            raise RuntimeError(
                "PortugueseLegalWriter requires the `anthropic` SDK. "
                "Install via `pip install anthropic` or add it to requirements."
            )
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model

    def write(self, request: PortugueseDocumentRequest) -> dict:
        conventions = JURISDICTION_CONVENTIONS.get(request.variant, {})
        notes = "\n".join(f"  - {n}" for n in conventions.get("notes", []))

        prompt = f"""Produce a formal {request.document_type.value} in {request.variant.value}:

SENDER: {request.sender}, {request.sender_org}
RECIPIENT: {request.recipient}, {request.recipient_org}
SUBJECT: {request.subject}
WHAT IT SHOULD SAY: {request.body_intent}
REFERENCES: {', '.join(request.reference_docs) or 'None'}
CONTRACT VALUE: {f'USD {request.contract_value:,.0f}' if request.contract_value else 'N/A'}
DEADLINE: {request.deadline or 'Not applicable'}

JURISDICTION CONVENTIONS FOR {request.variant.value}:
  Formal greeting: {conventions.get('greeting_formal', '')}
  Minister greeting: {conventions.get('greeting_minister', '')}
  Closing: {conventions.get('closing_formal', '')}
  Procurement body: {conventions.get('procurement_body', '')}
  Official gazette: {conventions.get('official_gazette', '')}
  Currency: {conventions.get('currency', '')}
  Notes:
{notes}

Return JSON:
{{
  "document_type": "{request.document_type.value}",
  "variant": "{request.variant.value}",
  "reference_number": "Suggested reference number",
  "full_document_portuguese": "Complete document in formal Portuguese",
  "english_summary": "Brief English summary of what was written",
  "legal_references_cited": ["List of laws and decrees cited"],
  "register_notes": "Notes on register choices made"
}}"""

        from ._resilient_llm import resilient_complete
        rr = resilient_complete(
            anthropic_client=self.client,
            anthropic_model=self.model,
            system_prompt=self.SYSTEM_PROMPT,
            user_prompt=prompt,
            max_tokens=2000,
        )
        self._last_llm_degraded = rr.degraded
        self._last_llm_model = rr.model_used
        self._last_llm_reason = rr.reason
        raw = re.sub(r"```json\s*|\s*```", "", rr.text).strip()
        data = json.loads(raw)
        data["produced_by"] = "ARIA — Arkmurus Research Intelligence Agent"
        data["produced_at"] = datetime.now(timezone.utc).isoformat()

        # Brain signal — Portuguese-language legal docs are the Lusophone
        # moat in action. Variant + jurisdiction + document_type uniquely
        # identify the training pair; CPLP corpus mastery grows here.
        try:
            import asyncio as _aio
            from ..intel import brain_hook as _bh

            async def _emit():
                await _bh.absorb(
                    module="portuguese_legal_writer",
                    summary=(
                        f"Portuguese {request.document_type.value} ({request.variant.value}): "
                        f"{request.subject[:80]} → {request.recipient_org[:60]}"
                    ),
                    detail=(data.get("english_summary") or "")[:1500],
                    entity_name=request.recipient_org[:120],
                    success=True,
                    extra_topics=["legal", "compliance", "general"],
                    confidence="CONFIRMED",
                )

            try:
                loop = _aio.get_running_loop()
                loop.create_task(_emit())
            except RuntimeError:
                pass
        except Exception:
            pass

        return data
