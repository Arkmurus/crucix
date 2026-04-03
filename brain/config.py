"""
CRUCIX Autonomous Brain — Configuration
All settings driven by environment variables for Render Docker compatibility.
Uses Redis for all persistence (no local disk paths needed on ephemeral filesystem).
"""
import os
from dataclasses import dataclass, field
from typing import List


@dataclass
class BrainConfig:
    # ── DeepSeek LLM ──────────────────────────────────────────────────────────
    deepseek_api_key: str        = field(default_factory=lambda: os.getenv("DEEPSEEK_API_KEY", ""))
    deepseek_base_url: str       = "https://api.deepseek.com/v1"
    deepseek_model: str          = "deepseek-chat"          # swap to deepseek-coder for code tasks
    deepseek_max_tokens: int     = 4096
    deepseek_temperature: float  = 0.2                      # low = deterministic reasoning

    # ── Redis (existing Crucix dual-write layer) ───────────────────────────────
    redis_url: str               = field(default_factory=lambda: os.getenv("REDIS_URL", "redis://localhost:6379"))
    redis_brain_prefix: str      = "crucix:brain:"

    # ── Redis model persistence keys ───────────────────────────────────────────
    redis_key_win_prob_model: str     = "crucix:brain:model:win_prob"
    redis_key_anomaly_model: str      = "crucix:brain:model:anomaly"
    redis_model_ttl_days: int         = 90

    # ── ChromaDB Vector Memory (in-memory on Render; backed up to Redis) ───────
    chroma_collection_intel: str    = "crucix_intelligence"
    chroma_collection_leads: str    = "crucix_leads"
    chroma_collection_outcomes: str = "crucix_outcomes"
    embedding_model: str            = "all-MiniLM-L6-v2"      # runs locally, no API cost

    # Redis backup keys for ChromaDB documents
    redis_key_chroma_intel: str    = "crucix:brain:chroma:intel"
    redis_key_chroma_leads: str    = "crucix:brain:chroma:leads"
    redis_key_chroma_outcomes: str = "crucix:brain:chroma:outcomes"
    redis_chroma_max_docs: int     = 500                    # max docs kept in Redis backup

    # ── ML Models ─────────────────────────────────────────────────────────────
    min_training_samples: int    = 15                       # outcomes needed before ML activates
    anomaly_contamination: float = 0.08                     # expected anomaly ratio in signal stream
    retrain_interval_hours: int  = 24                       # how often to retrain ML models

    # ── Autonomous Agent Loop ─────────────────────────────────────────────────
    sweep_interval_hours: int    = 6                        # full intelligence sweep cadence
    brain_memory_window: int     = 4                        # past run conclusions injected into context
    alert_threshold_risk: float  = 0.70                     # risk score that triggers Telegram alert

    # ── Priority Markets (Lusophone Africa core + expansion) ─────────────────
    priority_markets: List[str]  = field(default_factory=lambda: [
        "Angola", "Mozambique", "Guinea-Bissau", "Cape Verde",
        "São Tomé and Príncipe", "Nigeria", "Kenya", "Ghana",
        "Senegal", "Ivory Coast", "Morocco", "Tanzania"
    ])

    # ── Compliance Reference ───────────────────────────────────────────────────
    ofac_sanctions_url: str  = "https://www.treasury.gov/ofac/downloads/consolidated/consolidated.xml"
    dsca_fms_url: str        = "https://www.dsca.mil/press-media/major-arms-sales"
    ecju_ogel_url: str       = "https://www.gov.uk/government/collections/open-general-export-licences"


CONFIG = BrainConfig()
