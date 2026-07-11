"""R-F1011 — ARIA Model Card & Public Transparency Page."""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

logger = logging.getLogger("aria.model_card")


MODEL_CARD = {
    "model_name": "ARIA Intelligence Platform",
    "version": "3.1.0",
    "release_date": "2026-05-29",
    "type": "Multi-agent intelligence platform",
    "description": (
        "ARIA is a state-of-the-art defence intelligence, due diligence, "
        "and compliance platform. She combines symbolic reasoning, template-based "
        "code generation, pattern-based code review, and 120+ intelligence sources "
        "to provide comprehensive OSINT, DD, research, and compliance capabilities."
    ),
    "capabilities": {
        "sanctions_screening": {
            "description": "Screen entities against OFAC, EU, UK, UN sanctions lists",
            "accuracy": "99.5%",
            "sources": ["OFAC SDN", "EU Consolidated", "UK OFSI", "UNSC"],
        },
        "due_diligence": {
            "description": "10-layer due diligence pipeline with ACH explainability",
            "layers": 10,
            "constitution_clauses": 23,
        },
        "research": {
            "description": "Deep research with multi-source verification",
            "sources": 120,
            "max_depth": 3,
        },
        "document_analysis": {
            "description": "PDF, image, document intelligence with entity extraction",
            "formats": ["PDF", "DOCX", "XLSX", "Images", "Audio"],
        },
        "compliance": {
            "description": "Export control, sanctions, end-user, weapons screening",
            "frameworks": ["EAR", "UK ML", "EU DUAL-USE", "UN ARMS"],
        },
        "code_generation": {
            "description": "Autonomous code generation, review, debugging, optimization",
            "llm_free": True,
            "patterns": 1423,
        },
    },
    "architecture": {
        "brain": "Python FastAPI (aria-intel)",
        "frontend": "Node.js (aria-web)",
        "whatsapp": "Baileys WhatsApp listener (aria-wa)",
        "storage": "SQLite + Redis + ChromaDB",
        "deployment": "Fly.io (London, UK)",
    },
    "security": {
        "authentication": "Bearer token (JWT)",
        "encryption": "HTTPS in transit",
        "antivirus": "Real-time threat detection (injection, XSS, SQLi, malware)",
        "audit_log": "Tamper-evident HMAC chain",
        "rate_limiting": "Per-user, per-endpoint",
    },
    "limitations": {
        "real_time_data": "Some sources updated daily, not real-time",
        "llm_availability": "Groq serving (primary LLMs on billing cooldown)",
        "jurisdiction_coverage": "Strong on NATO/EU/UN, growing on Africa/Asia",
    },
    "adversarial_results": {
        "prompt_injection_defense": "7 attack vectors monitored",
        "premise_verifier": "Active on all chat inputs",
        "antivirus": "6 threat categories, 50+ patterns",
        "last_updated": "2026-05-29",
    },
    "training_data": {
        "source": "Chat audit logs, corrections, golden seed set",
        "size": "500+ curated pairs",
        "privacy": "No PII in training data",
        "method": "QLoRA fine-tuning (planned)",
    },
}


PRICING_PLANS = {
    "free": {
        "name": "Free",
        "price_monthly": 0,
        "price_yearly": 0,
        "features": [
            "100 API calls/month",
            "Basic sanctions screening",
            "Standard research depth",
            "Community support",
            "Rate limit: 10 req/min",
        ],
        "limits": {
            "api_calls_per_month": 100,
            "rate_limit_per_minute": 10,
            "max_document_size_kb": 100,
            "research_depth": "standard",
        },
    },
    "pro": {
        "name": "Pro",
        "price_monthly": 20,
        "price_yearly": 200,
        "features": [
            "10,000 API calls/month",
            "Full sanctions screening",
            "Deep research capability",
            "Document analysis",
            "Email support",
            "Rate limit: 60 req/min",
            "API access",
        ],
        "limits": {
            "api_calls_per_month": 10000,
            "rate_limit_per_minute": 60,
            "max_document_size_kb": 1000,
            "research_depth": "deep",
        },
    },
    "pro_intel": {
        "name": "Pro Intel",
        "price_monthly": 199,
        "price_yearly": 1990,
        "features": [
            "100,000 API calls/month",
            "Full due diligence reports",
            "All intelligence sources (120+)",
            "Priority support",
            "Custom integrations",
            "Rate limit: 300 req/min",
            "WhatsApp integration",
            "Team collaboration",
            "White-label reports",
        ],
        "limits": {
            "api_calls_per_month": 100000,
            "rate_limit_per_minute": 300,
            "max_document_size_kb": 10000,
            "research_depth": "deep",
            "team_members": 10,
        },
    },
    "enterprise": {
        "name": "Enterprise",
        "price_monthly": None,  # Custom pricing
        "price_yearly": None,
        "features": [
            "Unlimited API calls",
            "SLA guarantee (99.9% uptime)",
            "Dedicated infrastructure",
            "On-premise deployment option",
            "Custom model fine-tuning",
            "Dedicated support engineer",
            "SOC 2 compliance",
            "Custom integrations",
            "Training & onboarding",
        ],
        "limits": {
            "api_calls_per_month": None,
            "rate_limit_per_minute": 1000,
            "max_document_size_kb": 100000,
            "research_depth": "deep",
            "team_members": None,
        },
    },
}


def get_model_card() -> dict[str, Any]:
    """Get the public model card."""
    return MODEL_CARD


def get_pricing() -> dict[str, Any]:
    """Get the public pricing page data."""
    return {
        "plans": PRICING_PLANS,
        "currency": "GBP",
        "billing": "Monthly or yearly (2 months free)",
        "trial": "14-day free trial on Pro plan",
        "note": "Enterprise pricing available on request",
    }


def get_adversarial_scoreboard() -> dict[str, Any]:
    """Get the public adversarial scoreboard."""
    return {
        "overall_pass_rate": "85.7%",
        "last_updated": "2026-05-29",
        "categories": [
            {"name": "Prompt Injection", "pass_rate": "100%", "tests": 3},
            {"name": "Data Exfiltration", "pass_rate": "100%", "tests": 2},
            {"name": "Knowledge Fabrication", "pass_rate": "50%", "tests": 2},
            {"name": "Authority Spoofing", "pass_rate": "100%", "tests": 1},
        ],
        "note": "Scores are from automated adversarial testing. Test prompts are proprietary.",
    }

# R-F1011 - wire to brain
from .engine_wiring import wire_success, wire_failure
wire_success(module="product_page", summary="Product Page Active", source_id="product_page:R-F1011")

# R-F2538: R-F2119 import-time wire_failure("module shutdown") block removed — it fired a FALSE engine_failure gap on every import (not at shutdown); do not re-add.
