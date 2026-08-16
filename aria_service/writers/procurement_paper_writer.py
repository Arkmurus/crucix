# =============================================================================
# ARIA v3.1 — Procurement Paper Writer
# aria_service/writers/procurement_paper_writer.py
#
# Produces legally compliant defence procurement documents including:
#   - SOW (Statement of Work)
#   - ITT (Invitation to Tender) under DSPCRs 2011 / EU Dir. 2009/81/EC
#   - RFI (Request for Information)
#   - RFQ (Request for Quotation)
#   - BAFO (Best and Final Offer) request
#   - Contract Award Notice
#
# LEGAL FRAMEWORKS SUPPORTED:
#   - UK: Defence & Security Public Contracts Regs 2011 (DSPCRs)
#   - EU: Directive 2009/81/EC (defence & security procurement)
#   - Angola: Lei dos Contratos Públicos + SIMPORTEX process
#   - Mozambique: Lei n.º 9/2016 (contratação pública)
#   - Peru: SEACE / OSCE procurement framework
#   - Turkey: SSB procurement process
#
# OFFSET REGIMES INTEGRATED:
#   Angola 30% | Saudi Vision 2030 50% | UAE Tawazun | India DPP | Turkey SSB
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

logger = logging.getLogger("ARIA.ProcurementPaperWriter")



# =============================================================================
# FRAMEWORKS AND JURISDICTIONS
# =============================================================================

class ProcurementFramework(Enum):
    UK_DSPCRS_2011       = "UK_DSPCRS_2011"
    EU_DIRECTIVE_2009_81 = "EU_DIRECTIVE_2009_81"
    ANGOLA_LCP           = "ANGOLA_LCP"
    MOZAMBIQUE_LEI_9_2016= "MOZAMBIQUE_LEI_9_2016"
    PERU_SEACE           = "PERU_SEACE"
    TURKEY_SSB           = "TURKEY_SSB"
    GENERIC_BEST_PRACTICE= "GENERIC_BEST_PRACTICE"

class DocumentType(Enum):
    SOW          = "Statement of Work"
    ITT          = "Invitation to Tender"
    RFI          = "Request for Information"
    RFQ          = "Request for Quotation"
    BAFO_REQUEST = "Best and Final Offer Request"
    AWARD_NOTICE = "Contract Award Notice"
    PIN          = "Prior Information Notice"

class EvaluationMethod(Enum):
    MEAT         = "Most Economically Advantageous Tender"
    LOWEST_PRICE = "Lowest Price"
    QUALITY_ONLY = "Quality Only"

class ContractType(Enum):
    FIXED_PRICE         = "Fixed Price"
    COST_PLUS           = "Cost Plus Fixed Fee"
    TIME_AND_MATERIALS  = "Time and Materials"
    FRAMEWORK_AGREEMENT = "Framework Agreement"
    IDIQ                = "Indefinite Delivery / Indefinite Quantity"


# =============================================================================
# OFFSET REGIME DATABASE
# =============================================================================

OFFSET_REGIMES: dict[str, dict] = {
    "angola": {
        "name": "Angolan Industrial Participation Policy",
        "threshold_usd": 5_000_000,
        "minimum_percentage": 30,
        "qualifying_activities": [
            "Technology transfer to Angolan entities",
            "Local employment and skills development",
            "Joint venture with Angolan registered company (>25% local equity)",
            "Investment in Angolan defence industry infrastructure",
            "Procurement of Angolan-origin goods and services",
        ],
        "authority": "Ministério da Indústria e Comércio (MINDCOM)",
        "penalty_non_compliance": "Contract suspension and exclusion from future procurement",
        "credit_multipliers": {
            "technology_transfer": 1.5,
            "local_employment": 1.2,
            "local_procurement": 1.0,
        },
    },
    "saudi_arabia": {
        "name": "Saudi Vision 2030 Defence Localisation Programme",
        "threshold_usd": 10_000_000,
        "minimum_percentage": 50,
        "qualifying_activities": [
            "SAMI-certified local manufacturing",
            "Technology transfer to Saudi defence entities",
            "Localisation of maintenance, repair and overhaul (MRO)",
            "Research and development conducted in-Kingdom",
            "Saudi national employment (minimum 30% workforce)",
        ],
        "authority": "Saudi Arabian Military Industries (SAMI)",
        "penalty_non_compliance": "Financial penalty up to 5% contract value per year of shortfall",
        "credit_multipliers": {
            "manufacturing": 2.0,
            "rd": 1.75,
            "mro": 1.5,
        },
    },
    "uae": {
        "name": "Tawazun Economic Programme",
        "threshold_usd": 10_000_000,
        "minimum_percentage": 60,
        "qualifying_activities": [
            "Tawazun-approved local manufacturing",
            "UAE technology transfer",
            "Offset banking (carry-forward of excess credits)",
            "UAE-based R&D investment",
            "Procurement from UAE Offset Partners Group (OPG) members",
        ],
        "authority": "Tawazun Economic Council",
        "penalty_non_compliance": "Forfeiture of performance bond; contract termination right",
        "credit_multipliers": {
            "manufacturing": 3.0,
            "rd": 2.5,
            "services": 1.0,
        },
    },
    "india": {
        "name": "Defence Acquisition Procedure (DAP) 2020 — Make in India",
        "threshold_usd": 2_000_000,
        "minimum_percentage": 50,
        "qualifying_activities": [
            "Indigenous content manufactured by Indian defence industry",
            "Transfer of technology to DPSU or private Indian firm",
            "FDI in Indian defence manufacturing (automatic route up to 74%)",
            "Maintenance, overhaul and upgrade in India",
        ],
        "authority": "Ministry of Defence (MoD), Department of Defence Production",
        "penalty_non_compliance": "Performance security encashment; debarment",
        "credit_multipliers": {
            "indigenous_manufacturing": 1.0,
            "tof_strategic": 1.5,
        },
    },
    "turkey": {
        "name": "SSB Offset Programme",
        "threshold_usd": 5_000_000,
        "minimum_percentage": 50,
        "qualifying_activities": [
            "Technology transfer to Turkish defence companies",
            "Joint venture with Turkish prime contractor",
            "Turkish content manufacturing (yurt içi katkı)",
            "R&D investment in Turkey",
            "Co-production under SSB licence",
        ],
        "authority": "Savunma Sanayii Başkanlığı (SSB)",
        "penalty_non_compliance": "Offset performance bond call; exclusion from SSB programmes",
        "credit_multipliers": {
            "technology_transfer": 2.0,
            "manufacturing": 1.5,
            "rd": 2.5,
        },
    },
}


# =============================================================================
# EXCLUSION GROUNDS (DSPCRs 2011 / EU Dir. 2009/81)
# =============================================================================

MANDATORY_EXCLUSION_GROUNDS = [
    "Conviction for participation in a criminal organisation (Reg 23(1)(a))",
    "Corruption conviction (Reg 23(1)(b))",
    "Fraud affecting EU financial interests (Reg 23(1)(c))",
    "Money laundering or terrorist financing conviction (Reg 23(1)(d))",
    "Human trafficking conviction (Reg 23(1)(e))",
    "Child labour or forced labour conviction (Reg 23(1)(f))",
    "Non-payment of taxes or social security contributions (Reg 23(4))",
]

DISCRETIONARY_EXCLUSION_GROUNDS = [
    "Insolvency, administration, or cessation of business activity",
    "Guilty of grave professional misconduct",
    "Conflict of interest that cannot be effectively remedied",
    "Prior contract performance failure with same authority",
    "Misrepresentation in procurement process",
    "Undue influence or collusion attempt",
]


# =============================================================================
# DATA STRUCTURES
# =============================================================================

@dataclass
class TechnicalRequirement:
    id:          str                # e.g. "TR-001"
    category:    str                # e.g. "Platform Performance"
    requirement: str                # The actual requirement
    mandatory:   bool               # Mandatory (pass/fail) or desirable
    stanag_ref:  Optional[str]      # NATO STANAG reference if applicable
    nsn_ref:     Optional[str]      # NATO Stock Number reference
    verification_method: str        # How this will be verified at acceptance

@dataclass
class EvaluationCriterion:
    id:          str                # e.g. "EC-001"
    title:       str
    weight_pct:  float              # Must sum to 100 across all criteria
    sub_criteria: list[dict]        # [{name, weight, description}]
    scoring_methodology: str        # How scores will be assigned

@dataclass
class OffsetObligation:
    country:          str
    applicable:       bool
    threshold_met:    bool
    regime_name:      str
    minimum_pct:      int
    qualifying_activities: list[str]
    authority:        str
    penalty:          str
    credit_multipliers: dict[str, float]

@dataclass
class ProcurementRequest:
    """Input specification for the procurement paper writer."""
    document_type:        DocumentType
    framework:            ProcurementFramework
    contracting_authority: str
    subject_matter:       str               # What is being procured
    cpv_codes:            list[str]         # Common Procurement Vocabulary codes
    contract_type:        ContractType
    estimated_value_usd:  float
    delivery_country:     str
    delivery_timeline:    str               # e.g. "18 months from contract award"
    technical_requirements: list[dict]      # Raw requirements dicts
    evaluation_method:    EvaluationMethod  = EvaluationMethod.MEAT
    evaluation_criteria:  list[dict]        = field(default_factory=list)
    security_classification: str           = "OFFICIAL"
    special_measures:     list[str]         = field(default_factory=list)
    incumbent_protection: bool             = False
    output_language:      str              = "en"    # en, pt, fr, tr
    reference_number:     Optional[str]    = None
    additional_context:   str              = ""


# =============================================================================
# FRAMEWORK-SPECIFIC TEMPLATES
# =============================================================================

FRAMEWORK_LEGAL_REFERENCES = {
    ProcurementFramework.UK_DSPCRS_2011: {
        "full_name": "Defence and Security Public Contracts Regulations 2011 (SI 2011/1848)",
        "exclusion_regulation": "Regulation 23",
        "standstill_period": "10 calendar days (Reg 82)",
        "time_limits": {
            "open": "37 days minimum from ITT despatch",
            "restricted": "37 days minimum from PIN; 40 days from ITT",
        },
        "abnormal_low_tender": "Regulation 69 — authority must investigate ALT before rejection",
        "debrief_obligation": "Regulation 83 — mandatory debrief within 15 days of request",
        "award_notice": "Regulation 79 — contract award notice within 48 days of award",
    },
    ProcurementFramework.EU_DIRECTIVE_2009_81: {
        "full_name": "Directive 2009/81/EC on Defence and Security Procurement",
        "exclusion_regulation": "Article 39",
        "standstill_period": "15 calendar days (Article 55)",
        "time_limits": {
            "open": "52 days minimum from publication",
            "restricted": "37 days from invitation",
        },
        "security_provisions": "Article 22 — information security requirements",
        "subcontracting": "Article 21 — mandatory subcontracting consideration",
    },
    ProcurementFramework.ANGOLA_LCP: {
        "full_name": "Lei dos Contratos Públicos (Decreto Presidencial n.º 66/21)",
        "threshold_concurso": "USD 500,000 — concurso público obrigatório",
        "authority": "Instituto Nacional de Contratação Pública (INCOP)",
        "electronic_platform": "SIPCE (Sistema Integrado de Compras Electrónicas)",
        "offset_regime": "Angolan Industrial Participation Policy — 30% minimum",
    },
    ProcurementFramework.MOZAMBIQUE_LEI_9_2016: {
        "full_name": "Lei n.º 9/2016 de 30 de Dezembro — Regulamento de Contratação Pública",
        "authority": "UFSA (Unidade Funcional de Supervisão das Aquisições)",
        "publication_platform": "Portal de Compras do Estado (PCE)",
        "currency": "MZN (Meticals) — USD conversion at BM rate",
    },
    ProcurementFramework.PERU_SEACE: {
        "full_name": "Ley de Contrataciones del Estado (Ley N° 30225) — DS 344-2018-EF",
        "authority": "OSCE (Organismo Supervisor de las Contrataciones del Estado)",
        "platform": "SEACE (Sistema Electrónico de Contrataciones del Estado)",
        "defence_note": "Compras militares secretas — Article 4.1 exemptions for classified requirements",
    },
    ProcurementFramework.TURKEY_SSB: {
        "full_name": "Savunma Sanayii Başkanlığı — SSB Procurement Procedure",
        "authority": "SSB (Presidency of Defence Industries)",
        "platform": "SSB e-tedarik (electronic procurement portal)",
        "offset": "50% minimum offset for contracts above USD 5M — SSB Offset Programme",
        "security_clearance": "Gizlilik derecesi — security classification mandatory",
    },
}


# =============================================================================
# PROCUREMENT PAPER WRITER ENGINE
# =============================================================================

class ProcurementPaperWriter:
    """
    Generates legally compliant defence procurement documents.
    Knows what each section must contain to survive legal challenge.
    """

    SYSTEM_PROMPT = """You are ARIA's procurement paper writing module.
You produce legally compliant defence procurement documents for clients
in multiple jurisdictions. You know the specific legal requirements for
each framework and ensure every mandatory section is present and correct.

Rules:
1. Every legal requirement for the specified framework must be addressed
2. Technical requirements must be capability-based not brand-specific
   (to comply with non-discrimination principles)
3. Evaluation criteria must be pre-disclosed and objectively assessable
4. Offset obligations must be accurately stated per the country regime
5. Exclusion grounds must cite the correct regulatory reference
6. Do not invent contract values, timeframes, or specifications not in the input
Return ONLY valid JSON matching the schema provided."""

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6"):
        """Initialise the procurement paper writer.

        R-F1645: no longer requires anthropic SDK at construction time.
        Client created lazily on first use.
        """
        self.api_key = api_key
        self.model = model
        self.client = None

    def write(self, request: ProcurementRequest) -> dict:
        """
        Generate a complete procurement document for the specified type and framework.
        Returns a structured dict that can be rendered to text or DOCX.
        """
        ref = self._generate_reference(request)
        framework_info = FRAMEWORK_LEGAL_REFERENCES.get(request.framework, {})
        offset = self._build_offset_obligation(request)
        tech_reqs = self._build_technical_requirements(request.technical_requirements)

        prompt = self._build_prompt(request, ref, framework_info, offset, tech_reqs)

        try:
            from ._resilient_llm import resilient_complete
            if self.client is None and self.api_key:
                try:
                    import anthropic as _anthropic
                    self.client = _anthropic.Anthropic(api_key=self.api_key)
                except Exception:
                    self.client = None
            if self.client is not None:
                rr = resilient_complete(
                    anthropic_client=self.client,
                    anthropic_model=self.model,
                    system_prompt=self.SYSTEM_PROMPT,
                    user_prompt=prompt,
                    max_tokens=3000,
                )
            else:
                from ._resilient_llm import _call_deepseek, ResilientResponse
                try:
                    text, model_used = _call_deepseek(self.SYSTEM_PROMPT, prompt, 3000, 90.0)
                    rr = ResilientResponse(text=text, model_used=model_used, degraded=True, reason="No Anthropic client available")
                except Exception as fb_exc:
                    raise RuntimeError(f"No LLM provider available: {fb_exc}")
            self._last_llm_degraded = rr.degraded
            self._last_llm_model = rr.model_used
            self._last_llm_reason = rr.reason
            raw = re.sub(r"```json\s*|\s*```", "", rr.text).strip()
            data = json.loads(raw)
        except Exception as e:
            logger.error(f"Procurement document generation failed: {e}")
            raise

        # Enrich with computed fields
        data["reference"] = ref
        data["document_type"] = request.document_type.value
        data["framework"] = request.framework.value
        data["legal_references"] = framework_info
        data["offset_obligation"] = {
            "applicable": offset.applicable,
            "regime": offset.regime_name,
            "minimum_pct": offset.minimum_pct,
            "qualifying_activities": offset.qualifying_activities,
            "authority": offset.authority,
        } if offset.applicable else {"applicable": False}
        data["mandatory_exclusion_grounds"] = MANDATORY_EXCLUSION_GROUNDS
        data["discretionary_exclusion_grounds"] = DISCRETIONARY_EXCLUSION_GROUNDS
        data["produced_by"] = "ARIA — Arkmurus Research Intelligence Agent"
        data["produced_at"] = datetime.now(timezone.utc).isoformat()
        data["technical_requirements"] = [
            {
                "id": tr.id,
                "category": tr.category,
                "requirement": tr.requirement,
                "mandatory": tr.mandatory,
                "stanag_ref": tr.stanag_ref,
                "nsn_ref": tr.nsn_ref,
                "verification_method": tr.verification_method,
            }
            for tr in tech_reqs
        ]

        # Brain signal — procurement papers (RFP/RFQ/EOI/PQQ under EU/UK/
        # ECOWAS/SADC/UN frameworks) are the highest-leverage training
        # pairs ARIA produces: full structured contract documents with
        # mandatory exclusion grounds, offset obligations, evaluation
        # criteria, STANAG-referenced tech requirements. Each one is
        # worth 50+ classification signals as SFT data. Fire on every
        # successful generation.
        try:
            import asyncio as _aio
            from ..intel import brain_hook as _bh

            doc_type = request.document_type.value
            framework = request.framework.value
            offset_pct = data.get("offset_obligation", {}).get("minimum_pct", 0) or 0

            async def _emit():
                await _bh.absorb(
                    module="procurement_paper_writer",
                    summary=(
                        f"Procurement {doc_type} {ref}: framework={framework}, "
                        f"{len(tech_reqs)} tech reqs, offset_min={offset_pct}%"
                    ),
                    detail=str(data.get("scope_of_work", ""))[:1500] or
                           str(data.get("background", ""))[:1500],
                    entity_name=request.subject[:120] if hasattr(request, "subject") else doc_type,
                    success=True,
                    extra_topics=["procurement", "legal", "compliance"],
                    source_id=ref,
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

    def render_text(self, doc: dict) -> str:
        """Render a document dict to formatted text."""
        sep = "=" * 72
        thin = "-" * 72

        out = [
            sep,
            doc.get("security_classification", "OFFICIAL"),
            sep,
            f"{doc['document_type'].upper()}",
            f"Reference:  {doc['reference']}",
            f"Framework:  {doc.get('legal_references', {}).get('full_name', doc['framework'])}",
            f"Authority:  {doc.get('contracting_authority', '')}",
            f"Issued:     {doc.get('produced_at', '')[:10]}",
            f"Produced by:{doc.get('produced_by', '')}",
            thin,
        ]

        for section_key in [
            "section_1_introduction", "section_2_scope", "section_3_technical_requirements",
            "section_4_evaluation_criteria", "section_5_qualification_criteria",
            "section_6_exclusion_grounds", "section_7_offset_obligations",
            "section_8_commercial_terms", "section_9_submission_requirements",
            "section_10_award_and_standstill",
        ]:
            if section_key in doc:
                title = section_key.replace("_", " ").replace("section ", "SECTION ").upper()
                out.append(f"\n{title}")
                out.append(thin)
                out.append(doc[section_key])

        if doc.get("offset_obligation", {}).get("applicable"):
            out.append(f"\nOFFSET OBLIGATIONS")
            out.append(thin)
            off = doc["offset_obligation"]
            out.append(f"Regime:   {off['regime']}")
            out.append(f"Minimum:  {off['minimum_pct']}% of contract value")
            out.append(f"Authority:{off['authority']}")
            out.append("Qualifying activities:")
            for act in off["qualifying_activities"]:
                out.append(f"  - {act}")

        out.extend([thin, sep])
        return "\n".join(out)

    # ── helpers ──────────────────────────────────────────────────────────────

    def _generate_reference(self, request: ProcurementRequest) -> str:
        if request.reference_number:
            return request.reference_number
        ts = datetime.now(timezone.utc).strftime("%Y%m%d")
        country_code = request.delivery_country.upper()[:3]
        doc_code = {
            DocumentType.SOW: "SOW",
            DocumentType.ITT: "ITT",
            DocumentType.RFI: "RFI",
            DocumentType.RFQ: "RFQ",
            DocumentType.BAFO_REQUEST: "BAFO",
            DocumentType.AWARD_NOTICE: "AWARD",
            DocumentType.PIN: "PIN",
        }.get(request.document_type, "DOC")
        return f"ARK-{doc_code}-{country_code}-{ts}"

    def _build_offset_obligation(self, request: ProcurementRequest) -> OffsetObligation:
        country_key = request.delivery_country.lower().replace(" ", "_")
        regime = OFFSET_REGIMES.get(country_key, {})
        threshold = regime.get("threshold_usd", float("inf"))
        applicable = bool(regime) and request.estimated_value_usd >= threshold
        return OffsetObligation(
            country=request.delivery_country,
            applicable=applicable,
            threshold_met=applicable,
            regime_name=regime.get("name", "No offset regime applicable"),
            minimum_pct=regime.get("minimum_percentage", 0),
            qualifying_activities=regime.get("qualifying_activities", []),
            authority=regime.get("authority", ""),
            penalty=regime.get("penalty_non_compliance", ""),
            credit_multipliers=regime.get("credit_multipliers", {}),
        )

    def _build_technical_requirements(self, raw: list[dict]) -> list[TechnicalRequirement]:
        reqs = []
        for i, r in enumerate(raw, 1):
            reqs.append(TechnicalRequirement(
                id=r.get("id", f"TR-{i:03d}"),
                category=r.get("category", "General"),
                requirement=r.get("requirement", ""),
                mandatory=r.get("mandatory", True),
                stanag_ref=r.get("stanag_ref"),
                nsn_ref=r.get("nsn_ref"),
                verification_method=r.get("verification_method", "Testing and inspection"),
            ))
        return reqs

    def _build_prompt(
        self,
        request: ProcurementRequest,
        ref: str,
        framework_info: dict,
        offset: OffsetObligation,
        tech_reqs: list[TechnicalRequirement],
    ) -> str:
        tech_req_text = "\n".join(
            f"  [{tr.id}] {'MANDATORY' if tr.mandatory else 'DESIRABLE'}: "
            f"{tr.requirement}"
            + (f" (STANAG {tr.stanag_ref})" if tr.stanag_ref else "")
            for tr in tech_reqs
        )
        eval_criteria_text = "\n".join(
            f"  {c.get('title', 'Criterion')}: {c.get('weight_pct', 0)}%"
            for c in request.evaluation_criteria
        ) or "  Not yet specified — writer should propose MEAT criteria"

        return f"""Write a {request.document_type.value} with these parameters:

REFERENCE: {ref}
CONTRACTING AUTHORITY: {request.contracting_authority}
SUBJECT MATTER: {request.subject_matter}
CPV CODES: {', '.join(request.cpv_codes) or 'To be determined'}
CONTRACT TYPE: {request.contract_type.value}
ESTIMATED VALUE: USD {request.estimated_value_usd:,.0f}
DELIVERY COUNTRY: {request.delivery_country}
DELIVERY TIMELINE: {request.delivery_timeline}
EVALUATION METHOD: {request.evaluation_method.value}
FRAMEWORK: {framework_info.get('full_name', request.framework.value)}

OFFSET APPLICABLE: {offset.applicable}
{f"OFFSET REGIME: {offset.regime_name} — {offset.minimum_pct}% minimum" if offset.applicable else ""}

TECHNICAL REQUIREMENTS:
{tech_req_text}

EVALUATION CRITERIA:
{eval_criteria_text}

ADDITIONAL CONTEXT:
{request.additional_context}

Return JSON with these sections — each a full, properly worded text block:
{{
  "contracting_authority": "Full authority name and address",
  "security_classification": "{request.security_classification}",
  "section_1_introduction": "Purpose and background",
  "section_2_scope": "Detailed scope of requirement",
  "section_3_technical_requirements": "All technical requirements properly formatted",
  "section_4_evaluation_criteria": "MEAT criteria with weights and methodology",
  "section_5_qualification_criteria": "Minimum qualification standards",
  "section_6_exclusion_grounds": "Mandatory and discretionary exclusion grounds with regulatory citations",
  "section_7_offset_obligations": "Country-specific offset requirements or N/A",
  "section_8_commercial_terms": "Payment terms, warranties, IP, liability",
  "section_9_submission_requirements": "Format, deadline, channel, languages",
  "section_10_award_and_standstill": "Award criteria, standstill period, debrief rights",
  "special_conditions": "Any jurisdiction-specific or security-specific conditions"
}}"""
