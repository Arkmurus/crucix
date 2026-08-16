# =============================================================================
# ARIA v3.1 — Anti-Corruption Law Module
# aria_service/writers/anti_corruption_law.py
#
# Provides structured legal analysis and document production for:
#   - UK Bribery Act 2010 adequate procedures assessment (s.7 defence)
#   - FCPA jurisdictional analysis and compliance
#   - Commission structure legal opinion (defensibility under UKBA)
#   - Intermediary vetting checklist
#   - Facilitation payment risk assessment
#   - SAR (Suspicious Activity Report) trigger assessment
#
# LEGAL FRAMEWORK:
#   - UK Bribery Act 2010 (ss. 1, 2, 6, 7)
#   - MoJ Adequate Procedures Guidance (2011) — Six Principles
#   - US FCPA (15 U.S.C. §§ 78dd-1 et seq.)
#   - OECD Anti-Bribery Convention (1997)
#   - UN Convention Against Corruption (UNCAC 2003)
#   - Proceeds of Crime Act 2002 (UK) — SAR obligations
#
# NOTE: ARIA is not a law firm. This module produces legal analysis for
# internal advisory use. Clients requiring a formal legal opinion should
# engage qualified external counsel. ARIA flags this on every output.
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

logger = logging.getLogger("ARIA.AntiCorruptionLaw")



# =============================================================================
# RISK TAXONOMY
# =============================================================================

class CorruptionRiskLevel(Enum):
    LOW         = "LOW"
    MEDIUM      = "MEDIUM"
    HIGH        = "HIGH"
    CRITICAL    = "CRITICAL"   # SAR consideration required

class UKBAExposure(Enum):
    NONE        = "None — no UK nexus"
    INDIRECT    = "Indirect — UK person or company involved"
    DIRECT      = "Direct — UK company or person is the payer or recipient"
    CORPORATE   = "Corporate — s.7 corporate liability risk"

class FCPAExposure(Enum):
    NONE        = "None — no US nexus"
    ISSUER      = "Issuer — listed on US exchange"
    DOMESTIC    = "Domestic concern — US person or company"
    TERRITORIAL = "Territorial — US dollar transaction or US banking"

# MoJ Adequate Procedures — Six Principles (UK Bribery Act s.7 defence)
ADEQUATE_PROCEDURES_PRINCIPLES = {
    "P1": {
        "name": "Proportionate procedures",
        "description": "Anti-bribery policies proportionate to risk",
        "assessment_questions": [
            "Does the organisation have a written anti-bribery policy?",
            "Is the policy proportionate to the scale and nature of business?",
            "Does the policy address the specific risks of defence/security sector?",
            "Is there a gifts and hospitality register?",
        ],
    },
    "P2": {
        "name": "Top-level commitment",
        "description": "Senior management commitment to zero tolerance",
        "assessment_questions": [
            "Has senior management formally endorsed the anti-bribery policy?",
            "Is there a designated senior officer with anti-bribery responsibility?",
            "Does the organisation communicate zero tolerance publicly?",
        ],
    },
    "P3": {
        "name": "Risk assessment",
        "description": "Periodic assessment of bribery risks",
        "assessment_questions": [
            "Has a formal bribery risk assessment been conducted within 24 months?",
            "Does the risk assessment cover all relevant jurisdictions?",
            "Are intermediaries and agents included in the risk assessment?",
            "Is the country corruption index (TI CPI) referenced?",
        ],
    },
    "P4": {
        "name": "Due diligence",
        "description": "Due diligence on persons performing services",
        "assessment_questions": [
            "Is DD conducted on all intermediaries before engagement?",
            "Does DD include UBO identification and PEP screening?",
            "Are commission rates benchmarked against market norms?",
            "Is DD refreshed on a defined periodic basis?",
        ],
    },
    "P5": {
        "name": "Communication",
        "description": "Training and communication of policies",
        "assessment_questions": [
            "Are all relevant staff trained on anti-bribery obligations?",
            "Is training documented with completion records?",
            "Are intermediaries made aware of the organisation's anti-bribery policy?",
            "Is there a confidential reporting mechanism (whistleblower hotline)?",
        ],
    },
    "P6": {
        "name": "Monitoring and review",
        "description": "Ongoing monitoring and review of procedures",
        "assessment_questions": [
            "Are anti-bribery procedures audited at least annually?",
            "Is there a documented process for investigating suspected violations?",
            "Are commission payments audited against services actually delivered?",
            "Is there a documented gifts/hospitality approval process?",
        ],
    },
}

# TI Corruption Perceptions Index risk thresholds (indicative)
CPI_RISK_MAP = {
    "very_high": (0, 30),    # Below 30: Very high risk — enhanced DD mandatory
    "high":      (30, 45),   # 30-45: High risk — standard enhanced DD
    "medium":    (45, 60),   # 45-60: Medium risk — standard DD
    "low":       (60, 100),  # Above 60: Lower risk — proportionate DD
}

# Red flag indicators for commission structures
COMMISSION_RED_FLAGS = [
    "Commission rate significantly above market norm (>15% in defence sector)",
    "Payment to jurisdiction with no nexus to the transaction",
    "Intermediary with no demonstrable capability or sector knowledge",
    "Payment contingent on specific official's decision or approval",
    "Request for cash or untraceable payment method",
    "Shell company intermediary with no substance",
    "Relationship between intermediary and decision-making official",
    "Unusual urgency to conclude engagement before DD is complete",
    "Resistance to contractual anti-bribery representations",
    "Sub-agent arrangements not disclosed at outset",
]


# =============================================================================
# DATA STRUCTURES
# =============================================================================

@dataclass
class IntermediaryProfile:
    name:               str
    jurisdiction:       str
    role:               str                 # Broker, agent, consultant, distributor
    ti_cpi_score:       Optional[int]       # Transparency International CPI
    commission_rate_pct: float
    contract_value_usd: float
    is_pep:             bool = False        # Politically Exposed Person
    pep_relation:       Optional[str] = None
    ubo_identified:     bool = False
    has_written_contract: bool = False
    services_documented: bool = False
    known_to_decision_maker: Optional[bool] = None

@dataclass
class ComplianceOpinionRequest:
    """Input for generating a compliance opinion on a specific transaction."""
    transaction_description: str
    contracting_party:       str
    counterparty_country:    str
    intermediaries:          list[dict]
    commission_total_usd:    float
    contract_value_usd:      float
    uk_nexus:                bool            # UK company or person involved
    us_nexus:                bool            # US person, company, or USD transaction
    requesting_party:        str
    additional_context:      str = ""


@dataclass
class AdequateProceduresGap:
    principle: str              # P1-P6
    principle_name: str
    gaps_identified: list[str]
    remediation: list[str]
    risk_if_unaddressed: str

@dataclass
class ComplianceOpinion:
    """A structured compliance opinion on a specific transaction."""
    reference:          str
    transaction:        str
    produced_at:        str
    produced_by:        str
    disclaimer:         str
    ukba_exposure:      UKBAExposure
    fcpa_exposure:      FCPAExposure
    overall_risk:       CorruptionRiskLevel
    sar_required:       bool
    red_flags_identified: list[str]
    adequate_procedures_gaps: list[AdequateProceduresGap]
    commission_assessment: str
    intermediary_assessments: list[dict]
    legal_analysis:     str
    recommendations:    list[str]
    conditions_to_proceed: list[str]
    go_no_go:           str             # "PROCEED" / "PROCEED WITH CONDITIONS" / "DO NOT PROCEED"

    def render_text(self) -> str:
        sep = "=" * 72
        thin = "-" * 72

        flags = "\n".join(f"  [{i+1}] {f}" for i, f in enumerate(self.red_flags_identified)) or "  None identified"

        gaps_text = ""
        for gap in self.adequate_procedures_gaps:
            gaps_text += f"\n  {gap.principle} — {gap.principle_name}:\n"
            for g in gap.gaps_identified:
                gaps_text += f"    GAP: {g}\n"
            for r in gap.remediation:
                gaps_text += f"    FIX: {r}\n"
            gaps_text += f"    RISK: {gap.risk_if_unaddressed}\n"

        recs = "\n".join(f"  {i+1}. {r}" for i, r in enumerate(self.recommendations))
        conditions = "\n".join(f"  {i+1}. {c}" for i, c in enumerate(self.conditions_to_proceed))

        go_colour = {
            "PROCEED":                   "PROCEED",
            "PROCEED WITH CONDITIONS":   "PROCEED WITH CONDITIONS",
            "DO NOT PROCEED":            "DO NOT PROCEED — HALT ENGAGEMENT",
        }.get(self.go_no_go, self.go_no_go)

        return f"""
{sep}
ARKMURUS CONFIDENTIAL — LEGAL ADVISORY NOTE
{sep}
COMPLIANCE OPINION — ANTI-CORRUPTION ASSESSMENT
Reference:  {self.reference}
Transaction:{self.transaction}
Produced:   {self.produced_at}
Produced by:{self.produced_by}

DISCLAIMER
{self.disclaimer}

{thin}
EXECUTIVE SUMMARY

UK BRIBERY ACT EXPOSURE:  {self.ukba_exposure.value}
FCPA EXPOSURE:            {self.fcpa_exposure.value}
OVERALL RISK LEVEL:       {self.overall_risk.value}
SAR TRIGGER ASSESSMENT:   {"SAR CONSIDERATION REQUIRED — see NCA guidance" if self.sar_required else "SAR not required on current facts"}

CONCLUSION:  *** {go_colour} ***

{thin}
RED FLAGS IDENTIFIED
{flags}

{thin}
LEGAL ANALYSIS
{self.legal_analysis}

{thin}
COMMISSION STRUCTURE ASSESSMENT
{self.commission_assessment}

{thin}
ADEQUATE PROCEDURES GAP ANALYSIS (MoJ Six Principles)
{gaps_text}

{thin}
RECOMMENDATIONS
{recs}

{thin}
CONDITIONS TO PROCEED (if applicable)
{conditions or "  Not applicable — see go/no-go conclusion above"}

{thin}
IMPORTANT NOTICE
This opinion is produced by ARIA (Arkmurus Research Intelligence Agent)
for internal advisory use only. It does not constitute legal advice and
does not create a lawyer-client relationship. Arkmurus is not a law firm.
For transactions above USD 1M or where criminal exposure is identified,
engage qualified external counsel (solicitors with relevant UKBA/FCPA
experience) before proceeding.

Reference UK Bribery Act 2010; Ministry of Justice Adequate Procedures
Guidance (March 2011); SFO Prosecution Guidance on Bribery.
{sep}
""".strip()


# =============================================================================
# ANTI-CORRUPTION LAW ENGINE
# =============================================================================

class AntiCorruptionLawEngine:
    """
    Produces structured anti-corruption compliance opinions and
    adequate procedures assessments.
    """

    SYSTEM_PROMPT = """You are ARIA's anti-corruption legal analysis module.
You produce structured compliance opinions under the UK Bribery Act 2010,
US FCPA, and related anti-corruption frameworks for defence sector transactions.

You know:
- The UK Bribery Act 2010 (ss. 1, 2, 6, 7) and MoJ Adequate Procedures Guidance
- FCPA jurisdictional reach including territorial jurisdiction via USD transactions
- Market norms for defence intermediary commission rates (typically 3-12% in defence)
- Red flags that indicate potential corruption in defence procurement
- When a SAR should be considered under POCA 2002

Rules:
1. Never minimise risk — err toward caution in all assessments
2. Always flag if a SAR may be required under POCA 2002 s.330
3. Commission rates above 15% in defence sector require enhanced justification
4. PEP involvement always triggers CRITICAL risk minimum
5. State clearly: ARIA is not a law firm — flag when external counsel required
6. Do not give a GO conclusion where red flags are unresolved
Return ONLY valid JSON matching the schema."""

    DISCLAIMER = (
        "This document is produced by ARIA (Arkmurus Research Intelligence Agent) "
        "for internal advisory purposes only. It does not constitute legal advice "
        "and does not create a solicitor-client relationship. Arkmurus Ltd is not "
        "a law firm and is not regulated by the Solicitors Regulation Authority. "
        "This opinion should not be relied upon as a substitute for advice from "
        "qualified legal counsel with experience in UK Bribery Act / FCPA matters."
    )

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6"):
        """R-F1645: no longer requires anthropic SDK at construction time."""
        self.api_key = api_key
        self.model = model
        self.client = None

    def write_compliance_opinion(
        self, request: ComplianceOpinionRequest
    ) -> ComplianceOpinion:
        """
        Produce a compliance opinion for a specific transaction.
        """
        ref = f"ARK-COMPLIANCE-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')}"
        intermediaries = [
            IntermediaryProfile(
                name=i.get("name", "Unknown"),
                jurisdiction=i.get("jurisdiction", "Unknown"),
                role=i.get("role", "Agent"),
                ti_cpi_score=i.get("ti_cpi_score"),
                commission_rate_pct=i.get("commission_rate_pct", 0),
                contract_value_usd=request.contract_value_usd,
                is_pep=i.get("is_pep", False),
                pep_relation=i.get("pep_relation"),
                ubo_identified=i.get("ubo_identified", False),
                has_written_contract=i.get("has_written_contract", False),
                services_documented=i.get("services_documented", False),
                known_to_decision_maker=i.get("known_to_decision_maker"),
            )
            for i in request.intermediaries
        ]

        pre_analysis = self._pre_screen(request, intermediaries)
        prompt = self._build_prompt(request, intermediaries, pre_analysis)

        try:
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
        except Exception as e:
            logger.error(f"Compliance opinion generation failed: {e}")
            raise

        gaps = [
            AdequateProceduresGap(
                principle=g["principle"],
                principle_name=g["principle_name"],
                gaps_identified=g["gaps_identified"],
                remediation=g["remediation"],
                risk_if_unaddressed=g["risk_if_unaddressed"],
            )
            for g in data.get("adequate_procedures_gaps", [])
        ]

        opinion = ComplianceOpinion(
            reference=ref,
            transaction=request.transaction_description,
            produced_at=datetime.now(timezone.utc).isoformat(),
            produced_by="ARIA — Arkmurus Research Intelligence Agent",
            disclaimer=self.DISCLAIMER,
            ukba_exposure=UKBAExposure(data.get("ukba_exposure", "Direct")),
            fcpa_exposure=FCPAExposure(data.get("fcpa_exposure", "None — no US nexus")),
            overall_risk=CorruptionRiskLevel(data.get("overall_risk", "HIGH")),
            sar_required=data.get("sar_required", False),
            red_flags_identified=data.get("red_flags_identified", []),
            adequate_procedures_gaps=gaps,
            commission_assessment=data.get("commission_assessment", ""),
            intermediary_assessments=data.get("intermediary_assessments", []),
            legal_analysis=data.get("legal_analysis", ""),
            recommendations=data.get("recommendations", []),
            conditions_to_proceed=data.get("conditions_to_proceed", []),
            go_no_go=data.get("go_no_go", "PROCEED WITH CONDITIONS"),
        )

        # Brain signal — UKBA + FCPA compliance opinions are the highest-
        # liability outputs ARIA produces (legal opinions on bribery
        # exposure that the team may rely on for transaction GO/NO-GO).
        # SAR-required + RED-risk findings feed capability_gap so the
        # predictor can warn early on similar future transactions.
        try:
            import asyncio as _aio
            from ..intel import brain_hook as _bh

            verdict = opinion.go_no_go
            risk = opinion.overall_risk.value if hasattr(opinion.overall_risk, "value") else str(opinion.overall_risk)
            sar = opinion.sar_required
            n_flags = len(opinion.red_flags_identified or [])
            n_gaps = len(gaps)

            async def _emit():
                await _bh.absorb(
                    module="anti_corruption_law",
                    summary=(
                        f"Compliance opinion {ref}: {verdict} (risk={risk}) — "
                        f"{n_flags} red flags, {n_gaps} adequate-procedure gaps, SAR={sar}"
                    ),
                    detail=(opinion.legal_analysis or "")[:1500],
                    entity_name=request.transaction_description[:120],
                    success=True,
                    gap_type=("compliance_red_or_sar"
                              if (risk in ("RED", "HIGH", "CRITICAL") or sar) else None),
                    gap_detail=(f"{verdict} on {request.transaction_description[:80]}: "
                                f"{n_flags} red flags{', SAR required' if sar else ''}"
                                if (risk in ("RED", "HIGH", "CRITICAL") or sar) else None),
                    extra_topics=["compliance", "legal", "finance"],
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

        return opinion

    def assess_adequate_procedures(
        self, organisation_name: str, context: str
    ) -> str:
        """
        Run a gap analysis against the MoJ Six Principles.
        Returns a structured assessment report.
        """
        prompt = f"""Assess the adequacy of anti-bribery procedures for:
Organisation: {organisation_name}
Context: {context}

Using the MoJ Six Principles from the UK Bribery Act 2010 Adequate Procedures Guidance,
identify gaps and provide remediation for each principle.

Return JSON:
{{
  "organisation": "{organisation_name}",
  "overall_adequacy": "ADEQUATE|PARTIALLY ADEQUATE|INADEQUATE",
  "s7_defence_available": true/false,
  "principles": [
    {{
      "principle": "P1",
      "principle_name": "Proportionate procedures",
      "status": "COMPLIANT|PARTIAL|NON-COMPLIANT",
      "gaps": ["gap 1", "gap 2"],
      "remediation": ["action 1", "action 2"],
      "priority": "HIGH|MEDIUM|LOW"
    }}
  ],
  "summary_recommendations": ["rec 1", "rec 2"],
  "external_counsel_required": true/false
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
        return rr.text

    # ── helpers ──────────────────────────────────────────────────────────────

    def _pre_screen(
        self,
        request: ComplianceOpinionRequest,
        intermediaries: list[IntermediaryProfile],
    ) -> dict:
        """Quick pre-screening before LLM call."""
        flags = []
        sar_indicators = []

        for i in intermediaries:
            if i.is_pep:
                flags.append(f"PEP identified: {i.name} ({i.pep_relation or 'direct PEP'})")
                sar_indicators.append(f"PEP involvement — {i.name}")
            if i.commission_rate_pct > 15:
                flags.append(
                    f"Above-market commission: {i.name} at {i.commission_rate_pct}% "
                    f"(market norm 3-12% in defence)"
                )
            if not i.ubo_identified:
                flags.append(f"UBO not identified: {i.name}")
            if not i.has_written_contract:
                flags.append(f"No written contract: {i.name}")
            if not i.services_documented:
                flags.append(f"Services not documented: {i.name}")
            if i.known_to_decision_maker:
                flags.append(
                    f"Known relationship with decision-maker: {i.name}"
                )
                sar_indicators.append(f"Intermediary-official relationship — {i.name}")
            if i.ti_cpi_score and i.ti_cpi_score < 30:
                flags.append(
                    f"Very high-risk jurisdiction: {i.jurisdiction} "
                    f"(CPI {i.ti_cpi_score}/100)"
                )

        commission_pct = (
            request.commission_total_usd / request.contract_value_usd * 100
            if request.contract_value_usd > 0 else 0
        )
        if commission_pct > 15:
            flags.append(
                f"Total commission {commission_pct:.1f}% of contract value exceeds "
                f"defence sector norms"
            )

        return {
            "pre_screen_flags": flags,
            "sar_indicators": sar_indicators,
            "commission_pct_of_contract": commission_pct,
            "pep_count": sum(1 for i in intermediaries if i.is_pep),
            "high_risk_jurisdiction_count": sum(
                1 for i in intermediaries
                if i.ti_cpi_score and i.ti_cpi_score < 45
            ),
        }

    def _build_prompt(
        self,
        request: ComplianceOpinionRequest,
        intermediaries: list[IntermediaryProfile],
        pre_analysis: dict,
    ) -> str:
        intermediary_text = "\n".join(
            f"  - {i.name} ({i.role}): {i.jurisdiction} | "
            f"Commission: {i.commission_rate_pct}% | "
            f"PEP: {i.is_pep} | UBO identified: {i.ubo_identified} | "
            f"Written contract: {i.has_written_contract} | "
            f"CPI: {i.ti_cpi_score or 'unknown'}"
            for i in intermediaries
        )
        flags_text = "\n".join(f"  - {f}" for f in pre_analysis["pre_screen_flags"])

        return f"""Produce a compliance opinion for this defence sector transaction:

TRANSACTION: {request.transaction_description}
CONTRACTING PARTY: {request.contracting_party}
COUNTERPARTY COUNTRY: {request.counterparty_country}
CONTRACT VALUE: USD {request.contract_value_usd:,.0f}
TOTAL COMMISSION: USD {request.commission_total_usd:,.0f} ({pre_analysis['commission_pct_of_contract']:.1f}% of contract)
UK NEXUS: {request.uk_nexus}
US NEXUS: {request.us_nexus}

INTERMEDIARIES:
{intermediary_text}

PRE-SCREEN FLAGS ALREADY IDENTIFIED:
{flags_text or "  None from automated screening"}

ADDITIONAL CONTEXT: {request.additional_context}

ADEQUATE PROCEDURES PRINCIPLES TO ASSESS: {list(ADEQUATE_PROCEDURES_PRINCIPLES.keys())}

Return JSON:
{{
  "ukba_exposure": "None — no UK nexus|Indirect — UK person or company involved|Direct — UK company or person is the payer or recipient|Corporate — s.7 corporate liability risk",
  "fcpa_exposure": "None — no US nexus|Issuer — listed on US exchange|Domestic concern — US person or company|Territorial — US dollar transaction or US banking",
  "overall_risk": "LOW|MEDIUM|HIGH|CRITICAL",
  "sar_required": true/false,
  "red_flags_identified": ["flag 1", "flag 2"],
  "legal_analysis": "Full UKBA/FCPA legal analysis paragraph",
  "commission_assessment": "Assessment of commission structure defensibility",
  "intermediary_assessments": [
    {{"name": "...", "risk": "LOW|MEDIUM|HIGH|CRITICAL", "rationale": "..."}}
  ],
  "adequate_procedures_gaps": [
    {{
      "principle": "P1",
      "principle_name": "Proportionate procedures",
      "gaps_identified": ["gap 1"],
      "remediation": ["fix 1"],
      "risk_if_unaddressed": "..."
    }}
  ],
  "recommendations": ["rec 1", "rec 2"],
  "conditions_to_proceed": ["condition 1"],
  "go_no_go": "PROCEED|PROCEED WITH CONDITIONS|DO NOT PROCEED"
}}"""
