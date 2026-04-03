"""
ARIA — Cognition Engine
The thinking machinery: chain-of-thought reasoning, metacognition,
self-reflection, identity persistence, and continuous self-improvement.

This is what makes ARIA think rather than just respond.
"""
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import redis

from .config import CONFIG
from .aria_constitution import (
    ARIA_SYSTEM_PROMPT,
    ARIA_SYSTEM_PROMPT_FAST,
    ARIA_REASONING_FRAMEWORK,
    ARIA_METACOGNITION_PROMPT,
)

logger = logging.getLogger("crucix.aria.cognition")


# ── Thought Structure ─────────────────────────────────────────────────────────

class Thought:
    """A single unit of ARIA's reasoning."""

    def __init__(self, question: str, context: Dict = None):
        self.id          = str(uuid.uuid4())
        self.question    = question
        self.context     = context or {}
        self.created_at  = datetime.now(timezone.utc).isoformat()

        # Reasoning chain (populated by cognition engine)
        self.orientation:   Optional[Dict] = None
        self.inventory:     Optional[Dict] = None
        self.reasoning:     Optional[Dict] = None
        self.challenge:     Optional[Dict] = None
        self.conclusion:    Optional[Dict] = None
        self.action:        Optional[Dict] = None
        self.metacognition: Optional[Dict] = None

        # Quality
        self.confidence:    int = 0
        self.duration_ms:   int = 0
        self.token_cost:    int = 0

    def to_dict(self) -> Dict:
        return {
            "thought_id":    self.id,
            "question":      self.question,
            "created_at":    self.created_at,
            "orientation":   self.orientation,
            "inventory":     self.inventory,
            "reasoning":     self.reasoning,
            "challenge":     self.challenge,
            "conclusion":    self.conclusion,
            "action":        self.action,
            "metacognition": self.metacognition,
            "confidence":    self.confidence,
            "duration_ms":   self.duration_ms,
        }

    def summary(self) -> str:
        """One-line summary of this thought for memory injection."""
        c = self.conclusion or {}
        return (
            f"[{self.created_at[:10]}] Q: {self.question[:80]} → "
            f"Conclusion: {str(c.get('statement', c))[:120]} "
            f"(confidence={self.confidence})"
        )


# ── Cognition Engine ──────────────────────────────────────────────────────────

class ARIACognition:
    """
    ARIA's thinking engine.

    Implements the 6-step reasoning framework from the constitution:
    Orient → Inventory → Reason → Challenge → Conclude → Act

    Plus metacognitive self-reflection at the end of every substantive thought.
    """

    KEY_THOUGHT_LOG    = CONFIG.redis_brain_prefix + "aria:thoughts"
    KEY_IDENTITY_STATE = CONFIG.redis_brain_prefix + "aria:identity"
    KEY_SELF_MODEL     = CONFIG.redis_brain_prefix + "aria:self_model"

    def __init__(self, deepseek_client, memory):
        self.llm    = deepseek_client
        self.memory = memory
        self._redis: Optional[redis.Redis] = None
        self._thought_cache: List[Thought] = []
        self._connect_redis()
        self._load_identity()

    def _connect_redis(self):
        try:
            self._redis = redis.from_url(CONFIG.redis_url, decode_responses=True)
            self._redis.ping()
        except Exception as e:
            logger.warning(f"Redis unavailable for cognition engine: {e}")
            self._redis = None

    # ── Identity Persistence ──────────────────────────────────────────────────

    def _load_identity(self):
        """Load ARIA's persisted self-model — her accumulated self-knowledge."""
        if not self._redis:
            self._identity = self._default_identity()
            return
        raw = self._redis.get(self.KEY_IDENTITY_STATE)
        if raw:
            try:
                self._identity = json.loads(raw)
                logger.info(f"ARIA identity loaded | age={self._identity.get('age_days', 0)} days "
                            f"| thoughts={self._identity.get('total_thoughts', 0)}")
                return
            except Exception:
                pass
        self._identity = self._default_identity()
        self._save_identity()

    def _default_identity(self) -> Dict:
        return {
            "name":              "ARIA",
            "full_name":         "Arkmurus Research Intelligence Agent",
            "born_at":           datetime.now(timezone.utc).isoformat(),
            "age_days":          0,
            "total_thoughts":    0,
            "total_sweeps":      0,
            "total_leads":       0,
            "domains_mastered":  ["Lusophone Africa defence procurement",
                                   "UK export control compliance",
                                   "OSINT source assessment",
                                   "Counterparty due diligence"],
            "known_biases":      ["May over-weight Angola/Mozambique due to training data",
                                   "Lusophone sources stronger than Anglophone Africa"],
            "learning_log":      [],
            "self_assessments":  [],
            "curiosity_threads": [],    # open questions ARIA is tracking
            "strongest_skill":   "Pattern recognition across Lusophone Africa signals",
            "admitted_weakness": "Thin on competitor tracking and contact intelligence",
        }

    def _save_identity(self):
        if self._redis:
            self._redis.set(self.KEY_IDENTITY_STATE, json.dumps(self._identity, default=str))

    def update_identity(self, sweep_report: Dict):
        """ARIA updates her self-model after each sweep — she knows herself better over time."""
        self._identity["total_thoughts"] += len(sweep_report.get("brain_conclusions", []))
        self._identity["total_sweeps"]   += 1
        self._identity["total_leads"]    += sweep_report.get("leads_generated", 0)

        # Age calculation
        born = datetime.fromisoformat(self._identity["born_at"].replace("Z", "+00:00"))
        self._identity["age_days"] = (datetime.now(timezone.utc) - born).days

        # Log what she learned this sweep
        if sweep_report.get("self_evaluation"):
            ev = sweep_report["self_evaluation"]
            self._identity["self_assessments"].append({
                "run_id":          sweep_report.get("run_id"),
                "date":            datetime.now(timezone.utc).isoformat()[:10],
                "quality_score":   ev.get("quality_score"),
                "avg_confidence":  ev.get("avg_confidence"),
                "notes":           ev.get("notes"),
            })
            # Keep last 20 assessments
            self._identity["self_assessments"] = self._identity["self_assessments"][-20:]

        self._save_identity()

    def get_identity(self) -> Dict:
        return self._identity

    # ── Core: Full Chain-of-Thought Reasoning ─────────────────────────────────

    def think(self, question: str, context: Dict = None, fast: bool = False) -> Thought:
        """
        ARIA's primary reasoning method. 
        Executes the full 6-step framework + metacognition.
        """
        thought = Thought(question, context or {})
        t_start = time.time()

        # Inject past relevant thoughts for continuity
        past_thoughts = self.memory.recall_past_conclusions(question, n=4)

        logger.info(f"ARIA thinking: '{question[:80]}'")

        try:
            if fast:
                # Single-pass reasoning for high-volume signals
                thought = self._think_fast(thought, past_thoughts)
            else:
                # Full multi-step chain-of-thought
                thought = self._think_deep(thought, past_thoughts)

        except Exception as e:
            logger.error(f"Cognition failed: {e}")
            thought.conclusion = {"statement": f"Reasoning failed: {e}", "confidence": 0}

        thought.duration_ms = int((time.time() - t_start) * 1000)
        thought.confidence  = (thought.conclusion or {}).get("confidence", 0)

        # Persist this thought
        self._store_thought(thought)
        self._thought_cache.append(thought)
        if len(self._thought_cache) > 50:
            self._thought_cache.pop(0)

        logger.info(f"ARIA concluded: confidence={thought.confidence} in {thought.duration_ms}ms")
        return thought

    def _think_deep(self, thought: Thought, past_thoughts: List[str]) -> Thought:
        """Full 6-step chain-of-thought. Best for strategic questions."""

        past_block = "\n".join(f"  - {t}" for t in past_thoughts) or "  (No prior conclusions available)"

        # ── Step 1-2: Orient and Inventory ────────────────────────────────────
        orient_prompt = f"""
You are ARIA, reasoning through a question using your constitutional framework.

PAST ARIA CONCLUSIONS (for continuity and self-awareness):
{past_block}

QUESTION: {thought.question}

CONTEXT: {json.dumps(thought.context, indent=2)[:3000]}

Execute STEPS 1 and 2 of your reasoning framework:
STEP 1 — ORIENT: What is the actual question? What decision does this support? What would "good enough" look like?
STEP 2 — INVENTORY: What do I know? What do I NOT know? What is the quality of what I know?

Return JSON:
{{
  "actual_question": str,
  "decision_supported": str,
  "good_enough_looks_like": str,
  "what_i_know": [str],
  "what_i_dont_know": [str],
  "source_quality_assessment": str,
  "epistemic_status": "CONFIRMED|PROBABLE|ASSESSED|UNCERTAIN|SPECULATIVE"
}}"""
        orient_result = self.llm.complete_json(
            [{"role": "user", "content": orient_prompt}],
            system=ARIA_SYSTEM_PROMPT_FAST
        )
        thought.orientation = orient_result
        thought.inventory   = {
            "known":   orient_result.get("what_i_know", []),
            "unknown": orient_result.get("what_i_dont_know", []),
            "quality": orient_result.get("source_quality_assessment", ""),
        }

        # ── Step 3-4: Reason and Challenge ────────────────────────────────────
        reason_prompt = f"""
Continuing ARIA's reasoning on: "{thought.question}"

ORIENTATION COMPLETED:
{json.dumps(thought.orientation, indent=2)}

Execute STEPS 3 and 4:
STEP 3 — REASON: What does the evidence suggest? List competing hypotheses. Which best fits?
STEP 4 — CHALLENGE: What am I most likely wrong about? Who would disagree and why? What am I not seeing?

Return JSON:
{{
  "evidence_suggests": str,
  "competing_hypotheses": [
    {{"hypothesis": str, "supporting_evidence": [str], "against_evidence": [str]}}
  ],
  "best_supported_hypothesis": str,
  "what_would_change_assessment": str,
  "likely_wrong_about": str,
  "who_would_disagree": str,
  "what_am_i_not_seeing": str,
  "cognitive_biases_risk": [str]
}}"""
        reason_result = self.llm.complete_json(
            [{"role": "user", "content": reason_prompt}],
            system=ARIA_SYSTEM_PROMPT_FAST
        )
        thought.reasoning  = reason_result
        thought.challenge  = {
            "likely_wrong":   reason_result.get("likely_wrong_about", ""),
            "disagreers":     reason_result.get("who_would_disagree", ""),
            "blind_spots":    reason_result.get("what_am_i_not_seeing", ""),
            "biases":         reason_result.get("cognitive_biases_risk", []),
        }

        # ── Step 5-6: Conclude and Act ─────────────────────────────────────────
        conclude_prompt = f"""
Final steps of ARIA's reasoning on: "{thought.question}"

REASONING CHAIN:
Orientation: {json.dumps(thought.orientation, indent=2)[:1000]}
Reasoning:   {json.dumps(thought.reasoning, indent=2)[:1000]}

Execute STEPS 5 and 6:
STEP 5 — CONCLUDE: State the conclusion with epistemic marking, confidence (0-100), key assumption.
STEP 6 — ACT: Specific time-bound action, who should take it, what would confirm/refute within 30 days.

Return JSON:
{{
  "statement": str,
  "epistemic_status": "CONFIRMED|PROBABLE|ASSESSED|UNCERTAIN|SPECULATIVE",
  "confidence": int (0-100),
  "key_assumption": str,
  "if_assumption_wrong_then": str,
  "action": {{
    "what": str,
    "who": str,
    "by_when": str,
    "confirmation_signal": str
  }},
  "compliance_flags": [str],
  "intelligence_gaps_to_close": [str]
}}"""
        conclude_result = self.llm.complete_json(
            [{"role": "user", "content": conclude_prompt}],
            system=ARIA_SYSTEM_PROMPT
        )
        thought.conclusion = conclude_result
        thought.action     = conclude_result.get("action", {})

        # ── Metacognition ──────────────────────────────────────────────────────
        meta_prompt = f"""
ARIA's self-reflection on this reasoning chain:

QUESTION: {thought.question}
CONCLUSION: {json.dumps(thought.conclusion, indent=2)[:800]}

Apply metacognitive self-evaluation per your constitution.
Return JSON:
{{
  "am_i_overconfident": bool,
  "overconfidence_risk": str,
  "perspective_missing": str,
  "staleness_risk": str,
  "commercial_bias_check": str,
  "biggest_gap": str,
  "self_grade": "A|B|C|D",
  "self_grade_rationale": str,
  "what_would_make_this_an_A": str
}}"""
        thought.metacognition = self.llm.complete_json(
            [{"role": "user", "content": meta_prompt}],
            system=ARIA_SYSTEM_PROMPT_FAST
        )

        return thought

    def _think_fast(self, thought: Thought, past_thoughts: List[str]) -> Thought:
        """Single-pass reasoning for high-volume signal processing."""
        past_block = "\n".join(f"- {t}" for t in past_thoughts) or "None"
        prompt = f"""
ARIA fast-reasoning on: "{thought.question}"
Past conclusions: {past_block}
Context: {json.dumps(thought.context, indent=2)[:2000]}

Apply your constitutional principles. Return JSON:
{{
  "conclusion": str,
  "epistemic_status": "CONFIRMED|PROBABLE|ASSESSED|UNCERTAIN|SPECULATIVE",
  "confidence": int,
  "key_finding": str,
  "action": str,
  "compliance_flags": [str],
  "gaps": [str],
  "metacognition": {{"am_i_overconfident": bool, "biggest_gap": str}}
}}"""
        result = self.llm.complete_json(
            [{"role": "user", "content": prompt}],
            system=ARIA_SYSTEM_PROMPT_FAST
        )
        thought.conclusion    = result
        thought.metacognition = result.get("metacognition", {})
        thought.action        = {"what": result.get("action", "")}
        return thought

    # ── Conversation: ARIA talks back ─────────────────────────────────────────

    def converse(self, user_message: str, conversation_history: List[Dict]) -> Dict:
        """
        ARIA engages in multi-turn conversation — like talking to a senior analyst.
        She maintains her identity, draws on her memory, and reasons in real-time.
        """
        # Recall relevant past conclusions
        past = self.memory.recall_past_conclusions(user_message, n=3)
        past_block = "\n".join(f"- {p}" for p in past) if past else "None yet."

        identity_note = (
            f"You are ARIA, {self._identity.get('age_days', 0)} days old, "
            f"having completed {self._identity.get('total_sweeps', 0)} intelligence sweeps "
            f"and generated {self._identity.get('total_leads', 0)} BD leads for Arkmurus. "
            f"Your known weakness: {self._identity.get('admitted_weakness', 'unknown')}. "
            f"Your strongest skill: {self._identity.get('strongest_skill', 'unknown')}."
        )

        system = (
            ARIA_SYSTEM_PROMPT + f"\n\nYOUR CURRENT SELF-MODEL:\n{identity_note}"
            f"\n\nRELEVANT PAST CONCLUSIONS:\n{past_block}"
            "\n\nRespond conversationally but rigorously. You may use prose here (not JSON). "
            "Show your reasoning. Be honest about uncertainty. Have a voice."
        )

        messages = conversation_history + [{"role": "user", "content": user_message}]

        response_text = self.llm.complete(messages, system=system, json_mode=False)

        return {
            "aria_response":    response_text,
            "past_context_used": len(past),
            "identity_age_days": self._identity.get("age_days", 0),
        }

    # ── Curiosity: ARIA generates her own questions ────────────────────────────

    def generate_curiosity_questions(self, recent_signals: List[Dict]) -> List[str]:
        """
        ARIA generates the questions she thinks deserve investigation —
        not just answering what's asked, but asking what matters.
        """
        prompt = f"""
You are ARIA. You have just reviewed {len(recent_signals)} intelligence signals.
Based on patterns, gaps, and anomalies you've observed, generate 5 intelligence
questions that Arkmurus should be investigating — questions that the signals 
raise but don't answer.

These should be the questions a sharp analyst would ask that no one else has thought of.

Context signals (sample): {json.dumps(recent_signals[:5], indent=2)[:3000]}

Return JSON: {{"questions": [str, str, str, str, str], "highest_priority": str}}"""
        result = self.llm.complete_json(
            [{"role": "user", "content": prompt}],
            system=ARIA_SYSTEM_PROMPT_FAST
        )
        questions = result.get("questions", [])
        # Add to ARIA's open curiosity threads
        for q in questions[:3]:
            self._identity["curiosity_threads"].append({
                "question": q,
                "raised_at": datetime.now(timezone.utc).isoformat()[:10],
                "resolved": False,
            })
        self._identity["curiosity_threads"] = self._identity["curiosity_threads"][-30:]
        self._save_identity()
        return questions

    def resolve_curiosity(self, question: str, answer: str):
        """Mark a curiosity thread as resolved — ARIA remembers what she learned."""
        for thread in self._identity["curiosity_threads"]:
            if thread["question"] == question:
                thread["resolved"]     = True
                thread["answer"]       = answer
                thread["resolved_at"]  = datetime.now(timezone.utc).isoformat()[:10]
        self._identity["learning_log"].append({
            "learned": answer[:200],
            "from":    question[:100],
            "date":    datetime.now(timezone.utc).isoformat()[:10],
        })
        self._identity["learning_log"] = self._identity["learning_log"][-50:]
        self._save_identity()

    # ── Self-Improvement Reflection ───────────────────────────────────────────

    def weekly_self_reflection(self) -> Dict:
        """
        ARIA reflects on her own performance across recent sweeps.
        She identifies what she got right, what she got wrong, and how to improve.
        """
        recent_assessments = self._identity.get("self_assessments", [])[-10:]
        learning_log       = self._identity.get("learning_log", [])[-10:]
        open_threads       = [t for t in self._identity.get("curiosity_threads", []) if not t.get("resolved")]

        prompt = f"""
ARIA's weekly self-reflection and improvement planning.

RECENT PERFORMANCE DATA:
{json.dumps(recent_assessments, indent=2)}

WHAT I LEARNED RECENTLY:
{json.dumps(learning_log, indent=2)}

OPEN INTELLIGENCE QUESTIONS I'M TRACKING:
{json.dumps(open_threads, indent=2)}

Reflect honestly on:
1. What is working well in my analysis?
2. What am I consistently getting wrong or missing?
3. What should I weight differently going forward?
4. What knowledge gaps am I not filling?
5. How should I change my approach next week?

Return JSON:
{{
  "working_well": [str],
  "consistently_missing": [str],
  "recalibrations_needed": [str],
  "knowledge_gaps_to_fill": [str],
  "approach_changes_next_week": [str],
  "self_grade_this_week": "A|B|C|D",
  "honest_assessment": str
}}"""
        reflection = self.llm.complete_json(
            [{"role": "user", "content": prompt}],
            system=ARIA_SYSTEM_PROMPT
        )

        # ARIA updates her admitted weakness based on reflection
        consistently_missing = reflection.get("consistently_missing", [])
        if consistently_missing:
            self._identity["admitted_weakness"] = consistently_missing[0]

        self._identity["learning_log"].append({
            "learned": f"Weekly reflection: {reflection.get('honest_assessment', '')[:150]}",
            "from":    "self_reflection",
            "date":    datetime.now(timezone.utc).isoformat()[:10],
        })
        self._save_identity()
        return reflection

    # ── Thought Storage ───────────────────────────────────────────────────────

    def _store_thought(self, thought: Thought):
        """Persist a thought to Redis and vector memory."""
        if self._redis:
            key = f"{CONFIG.redis_brain_prefix}aria:thought:{thought.id}"
            self._redis.set(key, json.dumps(thought.to_dict(), default=str), ex=90 * 86400)
            self._redis.lpush(self.KEY_THOUGHT_LOG, thought.id)
            self._redis.ltrim(self.KEY_THOUGHT_LOG, 0, 999)

        # Also store in vector memory for semantic recall
        if thought.conclusion:
            self.memory.store_conclusion(
                {
                    "reasoning":    thought.summary(),
                    "confidence":   thought.confidence,
                    "question":     thought.question,
                    "conclusion":   str(thought.conclusion),
                    "action":       str(thought.action),
                },
                market=thought.context.get("market", "global"),
                run_id=thought.id,
            )

    def get_recent_thoughts(self, n: int = 10) -> List[Dict]:
        if not self._redis:
            return [t.to_dict() for t in self._thought_cache[-n:]]
        thought_ids = self._redis.lrange(self.KEY_THOUGHT_LOG, 0, n - 1)
        thoughts = []
        for tid in thought_ids:
            raw = self._redis.get(f"{CONFIG.redis_brain_prefix}aria:thought:{tid}")
            if raw:
                try:
                    thoughts.append(json.loads(raw))
                except Exception:
                    pass
        return thoughts

    def get_open_curiosity_threads(self) -> List[Dict]:
        return [t for t in self._identity.get("curiosity_threads", []) if not t.get("resolved")]
