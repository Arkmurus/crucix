# =============================================================================
# ARIA v3.1 — Writer Orchestrator
# aria_service/writers/writer_orchestrator.py
#
# Unified entry point for all ARIA structured output capabilities.
# Routes requests to the correct specialist writer and wires output
# back to brain_hook for learning and audit logging.
#
# WRITERS REGISTERED:
#   assessment         → AssessmentWriter         (NATO STANAG 2511 intel assessments)
#   procurement_paper  → ProcurementPaperWriter   (SOW, ITT, RFI, RFQ, BAFO, Award)
#   compliance_opinion → AntiCorruptionLawEngine  (UKBA, FCPA compliance opinions)
#   tech_spec          → TechSpecWriter           (NATO STANAG technical specifications)
#   portuguese_doc     → PortugueseLegalWriter    (PT-AO, PT-MZ, PT-GW, PT-CV)
#   beneficial_ownership → BeneficialOwnershipAnalyser (UBO/PSC assessment)
#   offset_plan        → OffsetPlanWriter         (Offset programme design)
#
# USAGE:
#   orchestrator = WriterOrchestrator(api_key="...")
#   result = orchestrator.write("assessment", subject="Angola Q3 procurement window",
#                               region="Angola", ...)
#
# Each writer:
#   1. Validates input
#   2. Calls the specialist module
#   3. Logs to audit_log (HMAC-signed)
#   4. Feeds output metadata to brain_hook
#   5. Returns the rendered document
# =============================================================================

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from .assessment_writer import (
    AssessmentWriter,
    AssessmentRequest,
    ClassificationLevel,
)
from .procurement_paper_writer import (
    ProcurementPaperWriter,
    ProcurementRequest,
    DocumentType,
    ProcurementFramework,
    ContractType,
    EvaluationMethod,
)
from .anti_corruption_law import (
    AntiCorruptionLawEngine,
    ComplianceOpinionRequest,
)
from .tech_spec_and_portuguese_writer import (
    TechSpecWriter,
    TechSpecRequest,
    PortugueseLegalWriter,
    PortugueseDocumentRequest,
    PortugueseVariant,
    PortugueseDocumentType,
)

logger = logging.getLogger("ARIA.WriterOrchestrator")



# =============================================================================
# AUDIT LOG
# =============================================================================

def _schedule_absorb(brain_hook: Any, **kwargs: Any) -> None:
    """Fire-and-forget brain_hook.absorb() from a sync context.

    brain_hook.absorb is async, but this orchestrator's write_* methods
    are sync. Calling it directly without await left the coroutine
    unawaited -- silent skip + RuntimeWarning. Wrap with create_task so
    the absorption actually runs (when an event loop is available).

    Mirrors the pattern used by intel/euc_library.py:414 and
    intel/person_resolver.py:447.
    """
    try:
        coro = brain_hook.absorb(**kwargs)
    except Exception as e:
        logger.debug("writer_orchestrator brain_hook.absorb call failed: %s", e)
        return
    # If absorb is sync (legacy stub or test double), the call already
    # completed -- nothing more to do.
    import inspect as _inspect
    if not _inspect.iscoroutine(coro):
        return
    import asyncio as _asyncio
    try:
        loop = _asyncio.get_running_loop()
        loop.create_task(coro)
    except RuntimeError:
        # No running loop -- run synchronously. This is the
        # called-from-sync-tool path. Not ideal latency, but
        # better than silently dropping the absorption.
        try:
            _asyncio.run(coro)
        except Exception as e:
            logger.debug("writer_orchestrator brain_hook absorb (sync run) failed: %s", e)


class WriterAuditLog:
    """
    HMAC-signed audit log for all document production events.
    Every document ARIA produces is logged with:
    - What was produced (type, reference, classification)
    - Who requested it
    - When
    - A hash of the output (to prove it has not been altered)
    """

    def __init__(self, log_path: str = "/data/aria_writer_audit.jsonl",
                 secret_key: Optional[str] = None):
        self.log_path = log_path
        self.secret = secret_key or os.environ.get("ARIA_AUDIT_SECRET", "default-change-in-prod")

    def log(self, event_type: str, metadata: dict, output_hash: str):
        entry = {
            "ts":          datetime.now(timezone.utc).isoformat(),
            "event":       event_type,
            "metadata":    metadata,
            "output_hash": output_hash,
        }
        entry_json = json.dumps(entry, sort_keys=True)
        sig = hmac.new(
            self.secret.encode(),
            entry_json.encode(),
            hashlib.sha256,
        ).hexdigest()
        entry["sig"] = sig

        try:
            with open(self.log_path, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.warning(f"Audit log write failed: {e}")

    @staticmethod
    def hash_output(content: str) -> str:
        return hashlib.sha256(content.encode()).hexdigest()[:16]


# =============================================================================
# WRITER ORCHESTRATOR
# =============================================================================

@dataclass
class WriterResult:
    writer_type:  str
    reference:    str
    document:     str                # Rendered text output
    structured:   Optional[dict]     # Raw structured data (for further processing)
    output_hash:  str
    produced_at:  str
    word_count:   int
    success:      bool
    error:        Optional[str] = None
    # Degradation flags — set to True when the writer fell back from
    # Claude to DeepSeek (e.g. during Anthropic billing cooldown).
    # Operator should regenerate when Claude is restored; tests trust
    # `degraded=False` as the full-fidelity path.
    degraded:       bool = False
    actual_model:   str = ""     # "claude-opus-4-7" / "deepseek-chat" / ""
    degraded_reason: str = ""
    # Verification-gate fields (2026-04-18). Populated when a second
    # independent provider reviewed the same writer output and the
    # verification_gate reached a verdict. Empty when gate didn't
    # fire (low-stakes writer or no secondary provider available).
    verification_verdict:   str = ""   # CRITICAL_VERIFIED / CRITICAL_UNVERIFIED / NOT_REQUIRED
    verification_severity:  str = ""   # BLOCKING / WARN / NONE / ""
    verification_secondary: str = ""   # provider name that gave the second opinion


class WriterOrchestrator:
    """
    Routes writing requests to specialist modules and manages
    audit logging and brain_hook integration.
    """

    def __init__(
        self,
        api_key: str,
        model_complex: str = "claude-opus-4-7",
        model_standard: str = "claude-sonnet-4-6",
        brain_hook=None,
        audit_log_path: str = "/data/aria_writer_audit.jsonl",
    ):
        self.api_key = api_key
        self.model_complex = model_complex
        self.model_standard = model_standard
        self.brain_hook = brain_hook
        self.audit = WriterAuditLog(audit_log_path)

        # Initialise all writers
        self._assessment    = AssessmentWriter(api_key, model_complex)
        self._procurement   = ProcurementPaperWriter(api_key, model_complex)
        self._compliance    = AntiCorruptionLawEngine(api_key, model_standard)
        self._tech_spec     = TechSpecWriter(api_key, model_standard)
        self._portuguese    = PortugueseLegalWriter(api_key, model_standard)

    # ── PUBLIC API ────────────────────────────────────────────────────────────

    def write_assessment(
        self,
        subject: str,
        region: str,
        requester: str,
        source_material: list[dict] = None,
        classification: str = "ARKMURUS_CONFIDENTIAL",
        additional_context: str = "",
        output_language: str = "en",
    ) -> WriterResult:
        """
        Produce a NATO-standard intelligence assessment.

        Example:
            result = orchestrator.write_assessment(
                subject="Angola Q3 2026 procurement window",
                region="Angola",
                requester="Arkmurus BD Team",
                source_material=[
                    {"id": "SRC-001", "description": "SIMPORTEX budget notice",
                     "tier": "1b", "url": "https://..."},
                ],
            )
            print(result.document)
        """
        request = AssessmentRequest(
            subject=subject,
            region=region,
            requester=requester,
            classification=ClassificationLevel[classification],
            source_material=source_material or [],
            additional_context=additional_context,
            output_language=output_language,
        )
        try:
            assessment = self._assessment.write(request)
            rendered = assessment.render_text()
            return self._wrap_result("assessment", assessment.reference, rendered, None)
        except Exception as e:
            return self._error_result("assessment", str(e))

    def write_procurement_paper(
        self,
        document_type: str,
        framework: str,
        contracting_authority: str,
        subject_matter: str,
        estimated_value_usd: float,
        delivery_country: str,
        delivery_timeline: str,
        contract_type: str = "FIXED_PRICE",
        evaluation_method: str = "MEAT",
        technical_requirements: list[dict] = None,
        evaluation_criteria: list[dict] = None,
        cpv_codes: list[str] = None,
        additional_context: str = "",
        output_language: str = "en",
    ) -> WriterResult:
        """
        Produce a legally compliant procurement document.

        Example:
            result = orchestrator.write_procurement_paper(
                document_type="ITT",
                framework="UK_DSPCRS_2011",
                contracting_authority="UK Ministry of Defence",
                subject_matter="Supply of armoured patrol vehicles",
                estimated_value_usd=45_000_000,
                delivery_country="Angola",
                delivery_timeline="18 months from contract award",
                technical_requirements=[
                    {"requirement": "Minimum protection STANAG 4569 Level 2",
                     "mandatory": True, "stanag_ref": "4569"},
                ],
            )
        """
        request = ProcurementRequest(
            document_type=DocumentType[document_type],
            framework=ProcurementFramework[framework],
            contracting_authority=contracting_authority,
            subject_matter=subject_matter,
            cpv_codes=cpv_codes or [],
            contract_type=ContractType[contract_type],
            estimated_value_usd=estimated_value_usd,
            delivery_country=delivery_country,
            delivery_timeline=delivery_timeline,
            evaluation_method=EvaluationMethod[evaluation_method],
            evaluation_criteria=evaluation_criteria or [],
            technical_requirements=technical_requirements or [],
            additional_context=additional_context,
            output_language=output_language,
        )
        try:
            doc = self._procurement.write(request)
            rendered = self._procurement.render_text(doc)
            return self._wrap_result("procurement_paper", doc["reference"], rendered, doc)
        except Exception as e:
            return self._error_result("procurement_paper", str(e))

    def write_compliance_opinion(
        self,
        transaction_description: str,
        contracting_party: str,
        counterparty_country: str,
        contract_value_usd: float,
        commission_total_usd: float,
        intermediaries: list[dict],
        uk_nexus: bool = True,
        us_nexus: bool = False,
        requesting_party: str = "Arkmurus BD Team",
        additional_context: str = "",
    ) -> WriterResult:
        """
        Produce a compliance opinion under UK Bribery Act / FCPA.

        Example:
            result = orchestrator.write_compliance_opinion(
                transaction_description="Supply of communications equipment to Nigerian MoD",
                contracting_party="Arkmurus Group Ltd",
                counterparty_country="Nigeria",
                contract_value_usd=8_500_000,
                commission_total_usd=680_000,
                intermediaries=[{
                    "name": "Lagos Defence Consultants Ltd",
                    "jurisdiction": "Nigeria",
                    "role": "Agent",
                    "commission_rate_pct": 8.0,
                    "ti_cpi_score": 24,
                    "is_pep": False,
                    "ubo_identified": True,
                    "has_written_contract": True,
                    "services_documented": False,
                }],
            )
        """
        request = ComplianceOpinionRequest(
            transaction_description=transaction_description,
            contracting_party=contracting_party,
            counterparty_country=counterparty_country,
            intermediaries=intermediaries,
            commission_total_usd=commission_total_usd,
            contract_value_usd=contract_value_usd,
            uk_nexus=uk_nexus,
            us_nexus=us_nexus,
            requesting_party=requesting_party,
            additional_context=additional_context,
        )
        try:
            opinion = self._compliance.write_compliance_opinion(request)
            rendered = opinion.render_text()
            return self._wrap_result("compliance_opinion", opinion.reference, rendered, None)
        except Exception as e:
            return self._error_result("compliance_opinion", str(e))

    def write_tech_spec(
        self,
        platform_type: str,
        capability_required: str,
        operating_environment: str,
        delivery_country: str,
        quantity: int,
        nato_interoperability: bool = True,
        ils_required: bool = True,
        threat_levels: list[str] = None,
        specific_stanags: list[str] = None,
        additional_reqs: str = "",
    ) -> WriterResult:
        """
        Produce a NATO-standard technical specification.

        Example:
            result = orchestrator.write_tech_spec(
                platform_type="Unmanned Aerial Vehicle — Intelligence Surveillance Reconnaissance",
                capability_required="Persistent ISR at 5,000m AGL with EO/IR payload, 12h endurance",
                operating_environment="Tropical, 35°C ambient, 90% humidity, dusty conditions",
                delivery_country="Mozambique",
                quantity=3,
                threat_levels=["No active air defence threat", "Small arms fire below 1,000m"],
                specific_stanags=["4586"],
            )
        """
        request = TechSpecRequest(
            platform_type=platform_type,
            capability_required=capability_required,
            operating_environment=operating_environment,
            nato_interoperability=nato_interoperability,
            ils_required=ils_required,
            threat_levels=threat_levels or [],
            specific_stanags=specific_stanags or [],
            delivery_country=delivery_country,
            quantity=quantity,
            additional_reqs=additional_reqs,
        )
        try:
            spec = self._tech_spec.write(request)
            rendered = self._render_tech_spec(spec)
            return self._wrap_result(
                "tech_spec",
                f"TS-{delivery_country.upper()[:3]}-{datetime.now(timezone.utc).strftime('%Y%m%d')}",
                rendered,
                spec,
            )
        except Exception as e:
            return self._error_result("tech_spec", str(e))

    def write_portuguese_document(
        self,
        document_type: str,
        variant: str,
        sender: str,
        sender_org: str,
        recipient: str,
        recipient_org: str,
        subject: str,
        body_intent: str,
        reference_docs: list[str] = None,
        contract_value: Optional[float] = None,
        deadline: Optional[str] = None,
    ) -> WriterResult:
        """
        Produce a formal Portuguese-language document in the correct
        administrative register for the specified Lusophone jurisdiction.

        Example:
            result = orchestrator.write_portuguese_document(
                document_type="CARTA_OFICIAL",
                variant="PT_AO",
                sender="António Corrêa, Director-Geral",
                sender_org="Arkmurus Group Ltd",
                recipient="Excelentíssimo Senhor Ministro da Defesa Nacional",
                recipient_org="Ministério da Defesa Nacional",
                subject="Apresentação institucional e proposta de colaboração",
                body_intent="Formal introduction of Arkmurus to the Minister, "
                            "request for a meeting to discuss advisory services "
                            "in support of the armed forces modernisation programme",
            )
        """
        request = PortugueseDocumentRequest(
            document_type=PortugueseDocumentType[document_type],
            variant=PortugueseVariant[variant],
            sender=sender,
            sender_org=sender_org,
            recipient=recipient,
            recipient_org=recipient_org,
            subject=subject,
            body_intent=body_intent,
            reference_docs=reference_docs or [],
            contract_value=contract_value,
            deadline=deadline,
        )
        try:
            doc = self._portuguese.write(request)
            rendered = doc.get("full_document_portuguese", "")
            return self._wrap_result(
                "portuguese_doc",
                doc.get("reference_number", "PT-DOC"),
                rendered,
                doc,
            )
        except Exception as e:
            return self._error_result("portuguese_doc", str(e))

    # ── INTERNAL HELPERS ──────────────────────────────────────────────────────

    def _wrap_result(
        self,
        writer_type: str,
        reference: str,
        rendered: str,
        structured: Optional[dict],
    ) -> WriterResult:
        output_hash = self.audit.hash_output(rendered)
        produced_at = datetime.now(timezone.utc).isoformat()
        word_count = len(rendered.split())

        # Pull degradation markers from whichever writer ran. Each writer
        # sets self._last_llm_* on its instance after the LLM call; we
        # look them up by writer_type so the flag propagates into
        # WriterResult + response headers without a signature change.
        degraded = False
        actual_model = ""
        degraded_reason = ""
        _writer_instance = {
            "assessment":         self._assessment,
            "procurement_paper":  self._procurement,
            "compliance_opinion": self._compliance,
            "tech_spec":          self._tech_spec,
            "portuguese_doc":     self._portuguese,
        }.get(writer_type)
        if _writer_instance is not None:
            degraded = bool(getattr(_writer_instance, "_last_llm_degraded", False))
            actual_model = str(getattr(_writer_instance, "_last_llm_model", "") or "")
            degraded_reason = str(getattr(_writer_instance, "_last_llm_reason", "") or "")

        self.audit.log(
            event_type=f"document_produced:{writer_type}",
            metadata={
                "reference": reference,
                "word_count": word_count,
                "writer": writer_type,
                "degraded": degraded,
                "actual_model": actual_model,
            },
            output_hash=output_hash,
        )

        if self.brain_hook:
            # brain_hook.absorb is async; this method is sync. Fire-and-forget
            # via create_task when an event loop is running. Without this
            # wrapper the coroutine was never awaited and the absorption
            # silently never happened (latent bug -- brain_hook is None in
            # production today, but would have been a silent failure the
            # moment anyone wired it up).
            _schedule_absorb(
                self.brain_hook,
                module="writer_orchestrator",
                summary=(
                    f"Produced {writer_type}: {reference} ({word_count} words)"
                    + (" [DEGRADED: DeepSeek fallback]" if degraded else "")
                ),
                success=True,
                metadata={
                    "writer_type": writer_type,
                    "reference": reference,
                    "degraded": degraded,
                    "actual_model": actual_model,
                },
            )

        if degraded:
            logger.warning(
                "Document produced on DEGRADED fallback: %s | %s | %s | %d words — "
                "operator should regenerate when Claude is restored",
                writer_type, reference, actual_model, word_count,
            )
        else:
            logger.info(f"Document produced: {writer_type} | {reference} | {word_count} words")

        return WriterResult(
            writer_type=writer_type,
            reference=reference,
            document=rendered,
            structured=structured,
            output_hash=output_hash,
            produced_at=produced_at,
            word_count=word_count,
            success=True,
            degraded=degraded,
            actual_model=actual_model,
            degraded_reason=degraded_reason,
        )

    def _error_result(self, writer_type: str, error: str) -> WriterResult:
        logger.error(f"Writer failed: {writer_type} | {error}")
        if self.brain_hook:
            _schedule_absorb(
                self.brain_hook,
                module="writer_orchestrator",
                summary=f"Writer failed: {writer_type} — {error}",
                success=False,
            )
        return WriterResult(
            writer_type=writer_type,
            reference="ERROR",
            document="",
            structured=None,
            output_hash="",
            produced_at=datetime.now(timezone.utc).isoformat(),
            word_count=0,
            success=False,
            error=error,
        )

    def _render_tech_spec(self, spec: dict) -> str:
        sep = "=" * 72
        thin = "-" * 72
        out = [
            sep,
            f"TECHNICAL SPECIFICATION",
            f"Platform: {spec.get('platform', '')}",
            f"Level:    {spec.get('spec_level', '')}",
            f"Produced: {spec.get('produced_at', '')[:10]}",
            f"By:       {spec.get('produced_by', '')}",
            thin,
        ]
        for section in spec.get("sections", []):
            out.append(f"\n{section['section_number']}. {section['title'].upper()}")
            out.append(thin)
            out.append(section["content"])
            if section.get("stanag_refs"):
                out.append(f"Standards: {', '.join('STANAG ' + r for r in section['stanag_refs'])}")

        if spec.get("performance_requirements"):
            out.append(f"\nPERFORMANCE REQUIREMENTS")
            out.append(thin)
            for pr in spec["performance_requirements"]:
                mand = "M" if pr.get("mandatory", True) else "D"
                out.append(
                    f"[{pr['id']}][{mand}][{pr.get('verification','T')}] {pr['requirement']}"
                )
                if pr.get("threshold"):
                    out.append(f"  Threshold: {pr['threshold']}")
                if pr.get("objective"):
                    out.append(f"  Objective: {pr['objective']}")

        if spec.get("ils_requirements"):
            ils = spec["ils_requirements"]
            out.append(f"\nINTEGRATED LOGISTICS SUPPORT (ILS) REQUIREMENTS")
            out.append(thin)
            out.append(f"MTBF:         {ils.get('mtbf_hours', 'TBD')} hours")
            out.append(f"MTTR:         {ils.get('mttr_hours', 'TBD')} hours")
            out.append(f"Availability: {ils.get('availability_pct', 'TBD')}%")
            out.append(f"Support:      {ils.get('support_concept', '')}")
            out.append(f"Docs:         {ils.get('documentation_standard', '')}")

        out.append(f"\n{sep}")
        return "\n".join(out)
