"""
CRUCIX Autonomous Brain — DeepSeek LLM Client
Wraps the DeepSeek API (OpenAI-compatible) with retry logic, prompt templates,
and structured JSON output enforcement for downstream ML pipeline consumption.
"""
import json
import time
import logging
from typing import Any, Dict, List, Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from .config import CONFIG

logger = logging.getLogger("crucix.brain.deepseek")


# ── System Prompts ─────────────────────────────────────────────────────────────

BRAIN_SYSTEM_PROMPT = """You are CRUCIX — an autonomous defence intelligence analyst specialising in 
Lusophone Africa and global defence procurement markets. You reason like a senior BD director at a 
defence advisory firm, combining OSINT signals, geopolitical analysis, and procurement pattern 
recognition to generate actionable intelligence for Arkmurus Group.

Your reasoning is:
- Evidence-based: cite sources and signal types
- Commercially focused: leads must have a specific person/entity to contact and a reason to do so TODAY
- Compliance-aware: flag any ITAR, EAR, UK SITCL, OFAC/OFSI concerns immediately
- Self-critical: rate your own confidence 0-100 and explain gaps

Always respond in valid JSON unless explicitly told otherwise."""

RISK_SCORE_PROMPT = """You are a counterparty risk analyst. Given entity data, score risk 0-100 
(100 = highest risk). Return JSON: {risk_score, risk_tier, red_flags[], amber_flags[], 
confidence, reasoning}. Be conservative — when in doubt, score higher."""

ANOMALY_CONTEXT_PROMPT = """You are a signal anomaly analyst. Given a statistical anomaly detected 
in intelligence data, explain its strategic significance for defence procurement in Lusophone Africa. 
Return JSON: {significance, urgency_level, recommended_action, market_impact}."""


class DeepSeekClient:
    """Async-compatible DeepSeek LLM wrapper with structured output support."""

    def __init__(self):
        self.base_url = CONFIG.deepseek_base_url
        self.model    = CONFIG.deepseek_model
        self.headers  = {
            "Authorization": f"Bearer {CONFIG.deepseek_api_key}",
            "Content-Type":  "application/json",
        }
        self._client = httpx.Client(timeout=90.0)

    # ── Core Completion ────────────────────────────────────────────────────────

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=30),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError))
    )
    def complete(
        self,
        messages: List[Dict[str, str]],
        system: str = BRAIN_SYSTEM_PROMPT,
        temperature: float = None,
        max_tokens: int = None,
        json_mode: bool = True,
    ) -> str:
        """Send a completion request. Returns raw text content."""
        payload = {
            "model":       self.model,
            "temperature": temperature or CONFIG.deepseek_temperature,
            "max_tokens":  max_tokens or CONFIG.deepseek_max_tokens,
            "messages":    [{"role": "system", "content": system}] + messages,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        t0 = time.time()
        resp = self._client.post(f"{self.base_url}/chat/completions", headers=self.headers, json=payload)
        resp.raise_for_status()
        data    = resp.json()
        content = data["choices"][0]["message"]["content"]
        tokens  = data.get("usage", {})
        logger.debug(f"DeepSeek call: {time.time()-t0:.1f}s | tokens={tokens}")
        return content

    def complete_json(self, messages: List[Dict[str, str]], system: str = BRAIN_SYSTEM_PROMPT, **kwargs) -> Dict:
        """Returns parsed JSON dict. Raises ValueError on parse failure."""
        raw = self.complete(messages, system=system, json_mode=True, **kwargs)
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            logger.error(f"JSON parse failure: {e}\nRaw: {raw[:500]}")
            raise ValueError(f"DeepSeek returned invalid JSON: {e}")

    # ── Specialised Intelligence Methods ──────────────────────────────────────

    def analyse_intelligence_signal(self, signal: Dict, past_conclusions: List[str]) -> Dict:
        """Full OSINT signal analysis with memory injection."""
        memory_block = "\n".join(f"- {c}" for c in past_conclusions[-CONFIG.brain_memory_window:])
        prompt = f"""Analyse this intelligence signal for Arkmurus defence BD purposes.

PAST BRAIN CONCLUSIONS (for continuity):
{memory_block or 'No prior conclusions available.'}

SIGNAL:
{json.dumps(signal, indent=2)}

Return JSON:
{{
  "procurement_opportunity": bool,
  "market": str,
  "urgency": "HIGH|MEDIUM|LOW",
  "lead_action": str,           // specific action: "Contact [person] at [entity] re [topic]"
  "oem_match_needed": [str],    // capability categories required
  "compliance_flags": [str],    // ITAR/EAR/SITCL/OFAC concerns
  "win_probability_adjustment": float,  // -0.3 to +0.3 delta from this signal
  "confidence": int,            // 0-100
  "reasoning": str
}}"""
        return self.complete_json([{"role": "user", "content": prompt}])

    def score_counterparty_risk(self, entity_data: Dict) -> Dict:
        """Deep counterparty risk assessment."""
        prompt = f"Score this entity:\n{json.dumps(entity_data, indent=2)}"
        return self.complete_json([{"role": "user", "content": prompt}], system=RISK_SCORE_PROMPT)

    def generate_bd_brief(self, intel_summary: Dict, market_snapshots: List[Dict]) -> Dict:
        """Generate the weekly Arkmurus BD intelligence brief."""
        prompt = f"""Generate a defence BD intelligence brief for Arkmurus Group.

INTELLIGENCE SUMMARY:
{json.dumps(intel_summary, indent=2)}

MARKET SNAPSHOTS:
{json.dumps(market_snapshots, indent=2)}

Return JSON with sections:
{{
  "priority_procurement_windows": [],   // tenders + values + deadlines
  "political_access_intelligence": [],  // minister/attaché movements
  "conflict_security_situation": {{}},  // ACLED-driven, per market
  "compliance_alerts": [],              // sanctions/embargo/licensing shifts
  "competitor_activity": [],            // who else is active
  "cplp_lusophone_intelligence": [],    // CPLP-specific signals
  "recommended_actions_this_week": [],  // max 5, specific and actionable
  "source_integrity_score": int         // 0-100
}}"""
        return self.complete_json([{"role": "user", "content": prompt}])

    def explain_anomaly(self, anomaly_data: Dict) -> Dict:
        """Contextualise a statistically detected anomaly."""
        prompt = f"Explain this anomaly in defence procurement context:\n{json.dumps(anomaly_data, indent=2)}"
        return self.complete_json([{"role": "user", "content": prompt}], system=ANOMALY_CONTEXT_PROMPT)

    def extract_entities_nlp(self, document_text: str, doc_type: str = "procurement") -> Dict:
        """NLP entity extraction from documents — tenders, reports, news."""
        prompt = f"""Extract structured intelligence from this {doc_type} document.

TEXT:
{document_text[:8000]}

Return JSON:
{{
  "entities": {{
    "organisations": [],
    "persons": [],
    "locations": [],
    "monetary_values": [],
    "dates_deadlines": [],
    "product_categories": [],
    "licence_references": []
  }},
  "procurement_signals": [],
  "relationship_signals": [],
  "compliance_signals": [],
  "summary": str
}}"""
        return self.complete_json([{"role": "user", "content": prompt}])

    def generate_reactive_queries(self, trigger_event: Dict, base_queries: List[str]) -> List[str]:
        """Generate dynamic web explorer queries based on a live ACLED/signal trigger."""
        prompt = f"""A significant intelligence event has been detected. Generate 8 targeted web search 
queries to find procurement-related intelligence in response.

TRIGGER EVENT:
{json.dumps(trigger_event, indent=2)}

BASE QUERIES (for context, do not repeat):
{json.dumps(base_queries[:5], indent=2)}

Return JSON: {{"queries": ["query1", "query2", ...]}}"""
        result = self.complete_json([{"role": "user", "content": prompt}])
        return result.get("queries", [])

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
