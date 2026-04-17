# aria_service/writers/__init__.py
# ARIA Writer Package — structured document production capability
#
# The writer package transforms ARIA from an intelligence retrieval platform
# into a document production engine. She no longer just knows things —
# she writes them in the correct format, with the correct legal references,
# in the correct language, for the correct jurisdiction.

from .assessment_writer import (
    AssessmentWriter,
    AssessmentRequest,
    IntelligenceAssessment,
    KeyJudgement,
    AlternativeHypothesis,
    SourceRecord,
    ConfidenceLevel,
    ClassificationLevel,
    tier_to_nato_grade,
    confidence_from_sources,
)

from .procurement_paper_writer import (
    ProcurementPaperWriter,
    ProcurementRequest,
    DocumentType,
    ProcurementFramework,
    ContractType,
    EvaluationMethod,
    OffsetObligation,
    OFFSET_REGIMES,
    MANDATORY_EXCLUSION_GROUNDS,
    DISCRETIONARY_EXCLUSION_GROUNDS,
    FRAMEWORK_LEGAL_REFERENCES,
)

from .anti_corruption_law import (
    AntiCorruptionLawEngine,
    ComplianceOpinionRequest,
    ComplianceOpinion,
    CorruptionRiskLevel,
    UKBAExposure,
    FCPAExposure,
    ADEQUATE_PROCEDURES_PRINCIPLES,
    CPI_RISK_MAP,
    COMMISSION_RED_FLAGS,
)

from .tech_spec_and_portuguese_writer import (
    TechSpecWriter,
    TechSpecRequest,
    PortugueseLegalWriter,
    PortugueseDocumentRequest,
    PortugueseVariant,
    PortugueseDocumentType,
    STANAG_REFERENCES,
    JURISDICTION_CONVENTIONS,
)

from .writer_orchestrator import (
    WriterOrchestrator,
    WriterResult,
    WriterAuditLog,
)

__all__ = [
    # Assessment
    "AssessmentWriter", "AssessmentRequest", "IntelligenceAssessment",
    "KeyJudgement", "AlternativeHypothesis", "SourceRecord",
    "ConfidenceLevel", "ClassificationLevel",
    "tier_to_nato_grade", "confidence_from_sources",
    # Procurement
    "ProcurementPaperWriter", "ProcurementRequest",
    "DocumentType", "ProcurementFramework", "ContractType", "EvaluationMethod",
    "OffsetObligation", "OFFSET_REGIMES", "MANDATORY_EXCLUSION_GROUNDS",
    "DISCRETIONARY_EXCLUSION_GROUNDS", "FRAMEWORK_LEGAL_REFERENCES",
    # Anti-corruption
    "AntiCorruptionLawEngine", "ComplianceOpinionRequest", "ComplianceOpinion",
    "CorruptionRiskLevel", "UKBAExposure", "FCPAExposure",
    "ADEQUATE_PROCEDURES_PRINCIPLES", "CPI_RISK_MAP", "COMMISSION_RED_FLAGS",
    # Tech spec + Portuguese
    "TechSpecWriter", "TechSpecRequest",
    "PortugueseLegalWriter", "PortugueseDocumentRequest",
    "PortugueseVariant", "PortugueseDocumentType",
    "STANAG_REFERENCES", "JURISDICTION_CONVENTIONS",
    # Orchestrator
    "WriterOrchestrator", "WriterResult", "WriterAuditLog",
]
