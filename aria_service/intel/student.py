"""
ARIA Student Brain — active learning behaviours.

Until now ARIA learned PASSIVELY: she captured whatever cloud LLM answers
arrived through the chat endpoint. The student brain makes her ACTIVE:
she identifies her own weak topics, generates study questions, deep-reads
authoritative sources during idle time, self-quizzes from past cases, and
compares her local answers against the cloud LLM to learn from divergence.

Behaviours
══════════
1. MASTERY TRACKING — per-topic competence score from accuracy on prior cases
2. CURRICULUM       — list of weak topics that need more study
3. SELF-QUIZ        — pick a stale library case, answer it locally, compare
                       to original, score self
4. READING SESSION  — deep-read N authoritative articles + extract
                       reasoning method (not just facts)
5. COMPARE & LEARN  — when both local and cloud produce answers, score the
                       divergence and store as study material
6. SPACED REPETITION — bring back stale cases on a forgetting curve
7. METHOD CAPTURE   — extract HOW the LLM solved a problem, not just WHAT

Independence philosophy
═══════════════════════
A great student doesn't memorise — she learns the *method*. When DeepSeek
solves a problem, the student brain captures the reasoning chain so the
next time a similar problem appears, ARIA can apply the same method
locally. Over months, the library becomes a "textbook of methods" that
can fine-tune ARIA-LLM into a domain expert without ever needing the
cloud teacher again.

Topic taxonomy
══════════════
ARIA's mastery is tracked across these domain topics:

    compliance        — export control, sanctions, embargoes
    procurement       — tenders, RFPs, contract awards
    market_intel      — country-specific intelligence
    technical         — weapon systems, calibres, ECCN
    geopolitics       — alliance shifts, regime stability
    osint             — investigation methodology
    finance           — budget cycles, deal economics
    relationships     — contacts, decision-makers, tenure
    competitor_intel  — rival broker activity
    legal             — UK/EU/US/UN regulatory framework

Each topic gets a 0..1 mastery score that grows from successful local
answers and shrinks from divergence with cloud LLM teachers.
"""
from __future__ import annotations

import asyncio
import logging
import math
import random
import re
import time
from typing import Any, Optional

from . import redis_store as rs
from . import reasoning_library
from . import reasoning_router
from . import knowledge as kb
from . import neural_memory

logger = logging.getLogger("aria.student")

MASTERY_KEY = "crucix:aria:student:mastery"
CURRICULUM_KEY = "crucix:aria:student:curriculum"
QUIZ_HISTORY_KEY = "crucix:aria:student:quiz_history"
READING_LOG_KEY = "crucix:aria:student:reading_log"
DIVERGENCE_KEY = "crucix:aria:student:divergence"
STUDENT_META_KEY = "crucix:aria:student:meta"

# Mastery starts at 0.5 (neutral) and updates by EWMA
INITIAL_MASTERY = 0.5
MASTERY_LR_POSITIVE = 0.08    # learning rate when answer was correct
MASTERY_LR_NEGATIVE = 0.12    # faster down — be honest about gaps
MASTERY_FLOOR = 0.05
MASTERY_CEILING = 0.98
WEAK_THRESHOLD = 0.55         # below this = "weak topic, needs study"

TOPICS = [
    "compliance", "procurement", "market_intel", "technical",
    "geopolitics", "osint", "finance", "relationships",
    "competitor_intel", "legal",
]

# Topic detection patterns — used to tag every interaction with a topic
_TOPIC_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("compliance", re.compile(r"\b(?:sanction|embargo|ofac|ofsi|itar|ear|sitcl|siel|ogel|export\s+control|licen[cs]e|debarment|euc|end.user)\b", re.I)),
    ("procurement", re.compile(r"\b(?:tender|rfp|rfq|contract|bid|procurement|acquisition|fms|solicitation|award)\b", re.I)),
    ("market_intel", re.compile(r"\b(?:angola|mozambique|nigeria|kenya|saudi|uae|brazil|indonesia|cplp|lusophone|market|country)\b", re.I)),
    ("technical", re.compile(r"\b(?:k9|himars|bayraktar|patriot|f-35|leopard|abrams|caliber|calibre|eccn|ml\d|weapon|system|specs?)\b", re.I)),
    ("geopolitics", re.compile(r"\b(?:nato|alliance|coup|regime|stability|conflict|geopolitical|sadc|ecowas|au\s+mission)\b", re.I)),
    ("osint", re.compile(r"\b(?:investigate|profile|due\s+diligence|background\s+check|crawl|research|osint|trace|map\s+the)\b", re.I)),
    ("finance", re.compile(r"\b(?:budget|spending|gdp|deal\s+(?:value|worth)|million|billion|finance|funding|loan|credit|offset|payment)\b", re.I)),
    ("relationships", re.compile(r"\b(?:contact|minister|general|colonel|admiral|director|liaison|tenure|meeting|relationship|humint)\b", re.I)),
    ("competitor_intel", re.compile(r"\b(?:competitor|rival|paramount|elbit|baykar|norinco|rheinmetall|bae|leonardo|raytheon|lockheed|boeing)\b", re.I)),
    ("legal", re.compile(r"\b(?:law|legal|regulation|statute|act\s+\d{4}|wassenaar|mtcr|nsg|treaty|convention)\b", re.I)),
]


def detect_topics(text: str) -> list[str]:
    """Tag a question/response with one or more topic categories."""
    if not text:
        return []
    matched = []
    for topic, pattern in _TOPIC_PATTERNS:
        if pattern.search(text):
            matched.append(topic)
    return matched or ["general"]


# ── Mastery tracking ───────────────────────────────────────────────────────

_mastery_cache: dict | None = None

async def _load_mastery() -> dict:
    global _mastery_cache
    if _mastery_cache is not None:
        return _mastery_cache
    raw = await rs.get_json(MASTERY_KEY)
    if isinstance(raw, dict):
        _mastery_cache = raw
    else:
        _mastery_cache = {t: {"score": INITIAL_MASTERY, "samples": 0,
                              "correct": 0, "wrong": 0, "last_practiced": 0}
                          for t in TOPICS}
    # Make sure all topics exist (in case TOPICS list grew)
    for t in TOPICS:
        if t not in _mastery_cache:
            _mastery_cache[t] = {"score": INITIAL_MASTERY, "samples": 0,
                                 "correct": 0, "wrong": 0, "last_practiced": 0}
    return _mastery_cache


async def _save_mastery() -> None:
    if _mastery_cache is not None:
        await rs.set_json(MASTERY_KEY, _mastery_cache)


async def update_mastery(topics: list[str], correct: bool, weight: float = 1.0) -> None:
    """Update mastery scores for the topics touched by an interaction.

    correct=True: ARIA's local answer matched the truth (or the teacher).
    correct=False: she got it wrong or diverged from the teacher.
    """
    if not topics:
        return
    mastery = await _load_mastery()
    now = time.time()
    for topic in topics:
        if topic not in mastery:
            mastery[topic] = {"score": INITIAL_MASTERY, "samples": 0,
                              "correct": 0, "wrong": 0, "last_practiced": 0}
        m = mastery[topic]
        m["samples"] = m.get("samples", 0) + 1
        m["last_practiced"] = now
        if correct:
            m["correct"] = m.get("correct", 0) + 1
            lr = MASTERY_LR_POSITIVE * weight
            m["score"] = min(MASTERY_CEILING, m["score"] + lr * (1 - m["score"]))
        else:
            m["wrong"] = m.get("wrong", 0) + 1
            lr = MASTERY_LR_NEGATIVE * weight
            m["score"] = max(MASTERY_FLOOR, m["score"] - lr * m["score"])
    await _save_mastery()


async def get_mastery_report() -> dict:
    mastery = await _load_mastery()
    total_samples = sum(m.get("samples", 0) for m in mastery.values())
    weak = [t for t, m in mastery.items() if m.get("score", 0) < WEAK_THRESHOLD]
    strong = [t for t, m in mastery.items() if m.get("score", 0) >= 0.80]
    overall = (
        sum(m.get("score", 0) * m.get("samples", 0) for m in mastery.values())
        / max(total_samples, 1)
    ) if total_samples > 0 else INITIAL_MASTERY
    return {
        "overall_mastery": round(overall, 3),
        "total_samples": total_samples,
        "topics": {
            t: {
                "score": round(m.get("score", 0), 3),
                "samples": m.get("samples", 0),
                "correct": m.get("correct", 0),
                "wrong": m.get("wrong", 0),
                "accuracy": round(m.get("correct", 0) / max(m.get("samples", 0), 1), 3),
                "last_practiced": m.get("last_practiced", 0),
            }
            for t, m in mastery.items()
        },
        "weak_topics": weak,
        "strong_topics": strong,
        "study_priority": sorted(mastery.items(), key=lambda x: x[1].get("score", 0))[:3],
    }


# ── Curriculum: what to study next ──────────────────────────────────────────

async def get_curriculum() -> dict:
    """Build a study plan: which topics need attention + suggested actions."""
    mastery = await _load_mastery()
    library_stats = await reasoning_library.get_stats()
    library_by_intent = library_stats.get("by_intent", {})

    plan = []
    for topic in TOPICS:
        m = mastery.get(topic, {})
        score = m.get("score", INITIAL_MASTERY)
        samples = m.get("samples", 0)
        last_practiced = m.get("last_practiced", 0)
        days_since = (time.time() - last_practiced) / 86400 if last_practiced else 999

        priority = 0
        actions = []

        # Weak topics get high priority
        if score < WEAK_THRESHOLD:
            priority += int((WEAK_THRESHOLD - score) * 100)
            actions.append("study_weak_topic")
        # Topics not practiced recently fade
        if days_since > 14:
            priority += min(int(days_since), 30)
            actions.append("spaced_repetition")
        # Topics with no library coverage need seeding
        if samples < 5:
            priority += 20
            actions.append("seed_with_reading")

        if priority > 0:
            plan.append({
                "topic": topic,
                "score": round(score, 3),
                "samples": samples,
                "days_since_practice": round(days_since, 1),
                "priority": priority,
                "actions": actions,
            })

    plan.sort(key=lambda x: -x["priority"])
    return {
        "items": plan[:10],
        "total_weak": sum(1 for p in plan if "study_weak_topic" in p["actions"]),
        "total_stale": sum(1 for p in plan if "spaced_repetition" in p["actions"]),
        "library_size": library_stats.get("total_cases", 0),
    }


# ── Self-quiz ──────────────────────────────────────────────────────────────

async def self_quiz(num_questions: int = 5) -> dict:
    """Pick stale library cases, try answering them with the LOCAL stack,
    compare to the original answer, and update mastery accordingly.

    This is the active recall loop. Without it the library is just a cache;
    with it, ARIA actually verifies that her local stack can reproduce
    answers she's been taught.
    """
    index = await reasoning_library._load_index()
    if not index:
        return {"quizzed": 0, "passed": 0, "note": "library empty"}

    # Pick stale cases — least recently used + lowest confidence
    candidates = sorted(
        index,
        key=lambda c: (c.get("ts_last_used", 0), c.get("confidence_score", 0)),
    )[:num_questions * 3]
    sample = random.sample(candidates, min(num_questions, len(candidates)))

    results = []
    passed = 0
    for entry in sample:
        case = await rs.get_json(reasoning_library._case_key(entry["id"]))
        if not case:
            continue
        question = case.get("question") or ""
        original_response = case.get("response") or ""
        if not question or not original_response:
            continue

        # Try the LOCAL reasoning stack (no cloud)
        local = await reasoning_router.try_local_reasoning(question)
        local_answered = local.get("answered", False)
        local_response = local.get("response") if local_answered else None

        # Score similarity between local and original (token overlap proxy)
        similarity = 0.0
        if local_response and original_response:
            similarity = _quick_similarity(local_response, original_response)

        passed_quiz = local_answered and similarity >= 0.4
        if passed_quiz:
            passed += 1

        topics = detect_topics(question)
        await update_mastery(topics, correct=passed_quiz, weight=0.5)

        results.append({
            "case_id": case.get("id"),
            "question": question[:120],
            "topics": topics,
            "local_answered": local_answered,
            "local_source": local.get("source") if local_answered else None,
            "similarity_to_original": round(similarity, 3),
            "passed": passed_quiz,
        })

    # Persist quiz history
    history = await rs.get_json(QUIZ_HISTORY_KEY) or []
    history.append({
        "ts": time.time(),
        "quizzed": len(results),
        "passed": passed,
        "score": round(passed / max(len(results), 1), 3),
    })
    history = history[-200:]  # keep last 200
    await rs.set_json(QUIZ_HISTORY_KEY, history)

    return {
        "quizzed": len(results),
        "passed": passed,
        "score": round(passed / max(len(results), 1), 3),
        "results": results,
    }


def _quick_similarity(a: str, b: str) -> float:
    """Token-overlap similarity (Jaccard) — fast, no embeddings needed."""
    if not a or not b:
        return 0.0
    ta = set(re.findall(r"\w+", a.lower()))
    tb = set(re.findall(r"\w+", b.lower()))
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union if union > 0 else 0.0


# ── Reading session: deep-read authoritative sources ───────────────────────

async def reading_session(llm=None, num_articles: int = 3) -> dict:
    """Run a focused reading session — pick high-priority articles, read them,
    extract facts AND the reasoning method, and update mastery on topics
    covered.

    A great student doesn't just read — she annotates. This function captures:
      - Facts        → knowledge.store_fact()
      - Concepts     → neural_memory.learn_from_text()
      - Topics       → mastery tracker
      - Source       → tagged for future audit
    """
    from .researcher import _fetch_rss, _fetch_article_text, RESEARCH_FEEDS

    # Pick feeds aligned with weak topics
    mastery = await _load_mastery()
    weak_topics = sorted(
        TOPICS, key=lambda t: mastery.get(t, {}).get("score", INITIAL_MASTERY)
    )[:3]
    logger.info("[student] reading session — focused on weak topics: %s", weak_topics)

    # Get fresh articles from a rotating set of feeds
    feeds_pool = random.sample(RESEARCH_FEEDS, min(8, len(RESEARCH_FEEDS)))
    articles_to_read = []
    for feed in feeds_pool:
        try:
            items = await _fetch_rss(feed["url"], timeout=10.0)
            for it in items[:3]:
                articles_to_read.append({**it, "feed_name": feed["name"]})
                if len(articles_to_read) >= num_articles * 3:
                    break
        except Exception:
            continue
        if len(articles_to_read) >= num_articles * 3:
            break

    # Score each article by how well its title matches our weak topics
    def _topic_match(text: str) -> int:
        topics_in_text = detect_topics(text)
        return sum(1 for t in topics_in_text if t in weak_topics)

    articles_to_read.sort(key=lambda a: -_topic_match(a.get("title", "")))
    selected = articles_to_read[:num_articles]

    studied = []
    for art in selected:
        url = art.get("link") or ""
        title = art.get("title") or ""
        if not url or not title:
            continue

        body = await _fetch_article_text(url, timeout=12.0)
        if not body or len(body) < 200:
            continue

        topics = detect_topics(f"{title} {body[:1000]}")

        # Index into knowledge base + neural memory (no LLM call needed)
        try:
            await kb.store_fact(
                topic=title[:80],
                content=body[:800],
                source=f"reading:{art.get('feed_name','unknown')}",
                confidence="ASSESSED",
            )
        except Exception as e:
            logger.debug("[student] kb store failed: %s", e)

        try:
            # Use LLM extraction if available, regex if not
            await neural_memory.learn_from_text(
                f"{title} {body[:2000]}",
                source=f"reading:{art.get('feed_name','unknown')}",
                llm=llm,
            )
        except Exception as e:
            logger.debug("[student] neural learning failed: %s", e)

        # Reading is reinforcement — small positive mastery bump
        await update_mastery(topics, correct=True, weight=0.3)

        studied.append({
            "title": title[:120],
            "url": url,
            "feed": art.get("feed_name"),
            "topics": topics,
            "chars_read": len(body),
        })

    # Log the session
    log = await rs.get_json(READING_LOG_KEY) or []
    log.append({
        "ts": time.time(),
        "articles_read": len(studied),
        "topics_focused": weak_topics,
    })
    log = log[-100:]
    await rs.set_json(READING_LOG_KEY, log)

    logger.info("[student] reading session complete: %d articles studied", len(studied))
    return {
        "articles_read": len(studied),
        "weak_topics_studied": weak_topics,
        "studied": studied,
    }


# ── Compare-and-learn (silent local attempt during cloud calls) ────────────

async def record_divergence(
    question: str,
    cloud_response: str,
    local_response: str | None,
    local_source: str | None,
    similarity: float,
) -> None:
    """Record a divergence between local and cloud answers as study material.

    When ARIA's local stack disagrees with her cloud teacher, that's a
    GAP — and gaps are what students should focus on. We log every
    divergence so the curriculum builder can prioritise study time
    on topics where local has been wrong.
    """
    log = await rs.get_json(DIVERGENCE_KEY) or []
    topics = detect_topics(question)
    log.append({
        "ts": time.time(),
        "question": question[:300],
        "topics": topics,
        "local_source": local_source,
        "local_response_preview": (local_response or "")[:300],
        "cloud_response_preview": cloud_response[:300],
        "similarity": round(similarity, 3),
        "needs_study": similarity < 0.5,
    })
    log = log[-500:]
    await rs.set_json(DIVERGENCE_KEY, log)

    # Update mastery — local was wrong-ish if similarity is low
    if similarity < 0.5:
        await update_mastery(topics, correct=False, weight=0.7)
    else:
        await update_mastery(topics, correct=True, weight=0.4)


async def compare_local_silently(question: str, cloud_response: str) -> dict:
    """After a cloud LLM responds, run the local stack on the SAME question
    and compare. The cloud's answer is treated as the teacher's; the local
    stack's answer is the student's attempt. The divergence becomes
    study material.

    This is what makes ARIA actively learn from her teacher rather than
    passively cache his answers.
    """
    try:
        local = await reasoning_router.try_local_reasoning(question)
        local_response = local.get("response") if local.get("answered") else None
        local_source = local.get("source") if local.get("answered") else None
        similarity = _quick_similarity(local_response or "", cloud_response)
        await record_divergence(question, cloud_response, local_response, local_source, similarity)
        return {
            "compared": True,
            "local_attempted": local.get("answered", False),
            "similarity": round(similarity, 3),
            "local_source": local_source,
        }
    except Exception as e:
        logger.warning("compare_local_silently failed: %s", e)
        return {"compared": False, "error": str(e)}


# ── Stats and reporting ────────────────────────────────────────────────────

async def get_student_stats() -> dict:
    mastery_report = await get_mastery_report()
    curriculum = await get_curriculum()
    quiz_history = await rs.get_json(QUIZ_HISTORY_KEY) or []
    reading_log = await rs.get_json(READING_LOG_KEY) or []
    divergences = await rs.get_json(DIVERGENCE_KEY) or []

    recent_quizzes = quiz_history[-10:]
    avg_quiz_score = (
        sum(q.get("score", 0) for q in recent_quizzes) / max(len(recent_quizzes), 1)
        if recent_quizzes else 0
    )

    return {
        "mastery": mastery_report,
        "curriculum": curriculum,
        "quiz_count": len(quiz_history),
        "recent_quiz_score": round(avg_quiz_score, 3),
        "reading_sessions": len(reading_log),
        "articles_studied_total": sum(r.get("articles_read", 0) for r in reading_log),
        "divergences_recorded": len(divergences),
        "divergences_needing_study": sum(1 for d in divergences if d.get("needs_study")),
    }


async def mastery_to_prompt_addendum(message: str) -> str:
    """Generate a system-prompt addendum that surfaces weak topics relevant
    to the current query. Closes the feedback loop: student tracks mastery
    → prompt tells ARIA she's weak → ARIA is more careful / cites more
    sources on that topic.

    Called by _build_calibrated_system_prompt in aria_engine.py.
    Returns empty string if no relevant weakness is detected.
    """
    if not message or len(message.strip()) < 10:
        return ""
    topics = detect_topics(message)
    if not topics or topics == ["general"]:
        return ""

    mastery = await _load_mastery()
    weak_relevant: list[tuple[str, float]] = []
    for t in topics:
        m = mastery.get(t, {})
        score = m.get("score", INITIAL_MASTERY)
        samples = m.get("samples", 0)
        if score < WEAK_THRESHOLD and samples >= 2:
            weak_relevant.append((t, score))

    if not weak_relevant:
        return ""

    lines = [
        "⚠ MASTERY ALERT — The student module has detected that you "
        "have LOW mastery on the following topic(s) relevant to this "
        "question. Take extra care: cite more sources, flag uncertainty "
        "explicitly, and prefer [ASSESSED] over [CONFIRMED] unless you "
        "have strong grounding."
    ]
    for topic, score in sorted(weak_relevant, key=lambda x: x[1]):
        lines.append(f"  • {topic}: mastery {score:.0%} (below {WEAK_THRESHOLD:.0%} threshold)")
    return "\n".join(lines)
