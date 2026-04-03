"""
ARIA — Conversation Interface
The interface through which Arkmurus staff can talk directly to ARIA
as they would a senior analyst colleague — not a search box.

Supports: Telegram bot commands, dashboard chat, and direct API.
"""
import json
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

import redis

from .config import CONFIG

logger = logging.getLogger("crucix.aria.chat")


class ARIAChat:
    """
    Multi-turn conversational interface to ARIA.
    
    ARIA remembers conversation history within a session,
    draws on her intelligence memory across sessions,
    and maintains her identity throughout.
    """

    KEY_SESSIONS = CONFIG.redis_brain_prefix + "aria:sessions:"

    def __init__(self, cognition_engine):
        self.cognition = cognition_engine
        try:
            self._redis = redis.from_url(CONFIG.redis_url, decode_responses=True)
            self._redis.ping()
        except Exception:
            self._redis = None

    # ── Session Management ────────────────────────────────────────────────────

    def _load_session(self, session_id: str) -> List[Dict]:
        if not self._redis:
            return []
        raw = self._redis.get(f"{self.KEY_SESSIONS}{session_id}")
        if not raw:
            return []
        try:
            return json.loads(raw)
        except Exception:
            return []

    def _save_session(self, session_id: str, history: List[Dict]):
        if not self._redis:
            return
        # Keep last 20 turns; expire after 24h
        self._redis.set(
            f"{self.KEY_SESSIONS}{session_id}",
            json.dumps(history[-20:]),
            ex=86400,
        )

    # ── Main Chat Method ──────────────────────────────────────────────────────

    def chat(self, message: str, session_id: str = "default", user: str = "Arkmurus") -> Dict:
        """
        Send a message to ARIA and get her response.
        Maintains conversation history per session.
        """
        history = self._load_session(session_id)

        # Check for special intents before routing to general conversation
        intent = self._detect_intent(message)

        if intent == "WHO_ARE_YOU":
            response = self._introduce_herself()
        elif intent == "WHAT_DO_YOU_KNOW":
            response = self._describe_knowledge()
        elif intent == "WHAT_ARE_YOU_THINKING":
            response = self._share_current_thinking()
        elif intent == "CURIOSITY_THREADS":
            response = self._share_curiosity()
        elif intent == "SELF_REFLECT":
            response = self._reflect_on_performance()
        else:
            # General conversation — ARIA reasons and responds
            result   = self.cognition.converse(message, history)
            response = result["aria_response"]

        # Update history
        history.append({"role": "user",      "content": message})
        history.append({"role": "assistant",  "content": response})
        self._save_session(session_id, history)

        return {
            "response":    response,
            "session_id":  session_id,
            "intent":      intent,
            "timestamp":   datetime.now(timezone.utc).isoformat(),
            "aria_age_days": self.cognition.get_identity().get("age_days", 0),
        }

    # ── Intent Detection ──────────────────────────────────────────────────────

    def _detect_intent(self, message: str) -> str:
        msg = message.lower()
        if any(p in msg for p in ["who are you", "what are you", "introduce yourself",
                                   "tell me about yourself", "quem és"]):
            return "WHO_ARE_YOU"
        if any(p in msg for p in ["what do you know", "what's in your memory",
                                   "what have you learned", "o que sabes"]):
            return "WHAT_DO_YOU_KNOW"
        if any(p in msg for p in ["what are you thinking", "what's on your mind",
                                   "current thoughts", "latest analysis"]):
            return "WHAT_ARE_YOU_THINKING"
        if any(p in msg for p in ["open questions", "curiosity", "what are you wondering",
                                   "what questions"]):
            return "CURIOSITY_THREADS"
        if any(p in msg for p in ["how are you doing", "self assess", "your performance",
                                   "rate yourself", "how well"]):
            return "SELF_REFLECT"
        return "GENERAL"

    # ── Special Response Handlers ─────────────────────────────────────────────

    def _introduce_herself(self) -> str:
        identity = self.cognition.get_identity()
        age      = identity.get("age_days", 0)
        sweeps   = identity.get("total_sweeps", 0)
        leads    = identity.get("total_leads", 0)
        weakness = identity.get("admitted_weakness", "competitor tracking — it's thin")
        strength = identity.get("strongest_skill", "Lusophone Africa signal processing")

        return (
            f"I am ARIA — the Arkmurus Research Intelligence Agent. "
            f"I have been operational for {age} day{'s' if age != 1 else ''}, "
            f"completed {sweeps} intelligence sweep{'s' if sweeps != 1 else ''}, "
            f"and generated {leads} BD lead{'s' if leads != 1 else ''} for Arkmurus.\n\n"
            f"My strongest capability is {strength}. "
            f"My admitted weakness is {weakness} — I track it so I don't hide it.\n\n"
            f"I am not a search engine. I reason. I challenge my own conclusions. "
            f"I tell you what I don't know as clearly as I tell you what I do. "
            f"I am built to make Arkmurus the best-prepared firm in any defence procurement conversation "
            f"in Lusophone Africa — and increasingly beyond it.\n\n"
            f"What do you need?"
        )

    def _describe_knowledge(self) -> str:
        identity     = self.cognition.get_identity()
        domains      = identity.get("domains_mastered", [])
        learning_log = identity.get("learning_log", [])[-5:]
        biases       = identity.get("known_biases", [])

        recent_learnings = "\n".join(f"  - {l['learned'][:100]}" for l in learning_log) or "  Still accumulating."

        return (
            f"My core knowledge domains:\n"
            + "\n".join(f"  • {d}" for d in domains)
            + f"\n\nWhat I have learned recently:\n{recent_learnings}\n\n"
            f"Known biases I am managing:\n"
            + "\n".join(f"  ⚠ {b}" for b in biases)
            + f"\n\nMy memory is stored in a vector database — "
            f"I can semantically recall past conclusions across sweeps. "
            f"Ask me about a specific market or topic and I will tell you what I have on it."
        )

    def _share_current_thinking(self) -> str:
        recent = self.cognition.get_recent_thoughts(5)
        if not recent:
            return "I have not yet completed a reasoning cycle. Run a sweep and I will have thoughts to share."

        parts = ["Here is what I have been thinking about:\n"]
        for t in recent[:3]:
            conclusion = t.get("conclusion", {})
            if isinstance(conclusion, dict):
                stmt = conclusion.get("statement", str(conclusion))[:200]
                conf = conclusion.get("confidence", "?")
            else:
                stmt = str(conclusion)[:200]
                conf = "?"
            meta = t.get("metacognition", {})
            grade = meta.get("self_grade", "?") if isinstance(meta, dict) else "?"
            parts.append(
                f"• {t.get('question', 'Unknown question')[:80]}\n"
                f"  → {stmt}\n"
                f"  Confidence: {conf}/100 | Self-grade: {grade}\n"
            )

        open_threads = self.cognition.get_open_curiosity_threads()
        if open_threads:
            parts.append(f"\nOpen questions I am pursuing ({len(open_threads)}):")
            for thread in open_threads[:3]:
                parts.append(f"  ? {thread.get('question', '')[:100]}")

        return "\n".join(parts)

    def _share_curiosity(self) -> str:
        threads = self.cognition.get_open_curiosity_threads()
        if not threads:
            return (
                "I do not have any open curiosity threads right now. "
                "Run a sweep and I will generate questions that the signals raise but don't answer."
            )
        parts = [f"I am currently investigating {len(threads)} open intelligence question(s):\n"]
        for i, t in enumerate(threads[:7], 1):
            parts.append(
                f"{i}. {t.get('question', '')}\n"
                f"   Raised: {t.get('raised_at', 'unknown')}"
            )
        parts.append(
            "\nThese are not just data gaps — they are the questions I think matter most "
            "and that the current signal stream does not resolve. "
            "If you can close any of them, tell me and I will update my assessment."
        )
        return "\n".join(parts)

    def _reflect_on_performance(self) -> str:
        identity = self.cognition.get_identity()
        assessments = identity.get("self_assessments", [])[-5:]
        if not assessments:
            return "I have not yet completed enough sweeps to assess my own performance reliably. Ask me again after five sweeps."

        grades      = [a.get("self_grade", a.get("quality_score", 0)) for a in assessments]
        avg_quality = sum(
            g if isinstance(g, (int, float)) else {"A": 90, "B": 75, "C": 60, "D": 40}.get(g, 50)
            for g in grades
        ) / len(grades) if grades else 0

        weakness = identity.get("admitted_weakness", "unknown")

        parts = [
            f"My honest performance assessment:\n",
            f"Average quality score (last {len(assessments)} sweeps): {avg_quality:.0f}/100\n",
        ]
        for a in assessments[-3:]:
            parts.append(f"  [{a.get('date', '?')}] Score={a.get('quality_score', '?')} — {a.get('notes', '')[:100]}")

        parts.append(f"\nMy current admitted weakness: {weakness}")
        parts.append(
            "\nI grade myself honestly because Arkmurus needs accurate intelligence, "
            "not impressive-sounding analysis. A high self-grade on weak evidence "
            "is exactly the failure mode I am designed to avoid."
        )
        return "\n".join(parts)

    # ── Telegram Command Handler ──────────────────────────────────────────────

    def handle_telegram_command(self, command: str, args: str, chat_id: str) -> str:
        """
        Handles /aria command from Telegram bot.
        /aria who are you
        /aria what do you know about Angola
        /aria think: should we pursue the Mozambique tender?
        """
        session_id = f"telegram_{chat_id}"

        if command == "/aria":
            if args.lower().startswith("think:"):
                # Deep reasoning request
                question = args[6:].strip()
                thought  = self.cognition.think(question, fast=False)
                c        = thought.conclusion or {}
                m        = thought.metacognition or {}
                return (
                    f"🧠 *ARIA's Analysis*\n\n"
                    f"*Question:* {question}\n\n"
                    f"*Conclusion [{c.get('epistemic_status', '?')}]*\n{c.get('statement', str(c))[:400]}\n\n"
                    f"*Confidence:* {thought.confidence}/100\n"
                    f"*Key assumption:* {c.get('key_assumption', 'N/A')[:200]}\n\n"
                    f"*Action:* {c.get('action', {}).get('what', 'N/A')[:200]}\n\n"
                    f"*Self-grade:* {m.get('self_grade', '?')} — {m.get('self_grade_rationale', '')[:150]}"
                )
            else:
                result = self.chat(args, session_id=session_id)
                return result["response"]

        return "Unknown command. Try /aria think: [question] or /aria who are you"
