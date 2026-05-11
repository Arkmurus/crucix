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
MASTERY_LR_POSITIVE = 0.18    # bumped again 2026-04-17 late PM after the
                               # calibration review STILL flagged UNDERCONFIDENT
                               # at -17.1pp even after the 0.08→0.12 change.
                               # Stored scores don't respond to learning-rate
                               # changes retroactively — hence the new
                               # self-calibration correction in
                               # calibration_review.run_calibration_review().
                               # The rate change still helps future events
                               # converge faster.
MASTERY_LR_NEGATIVE = 0.12    # keep down-rate lower — be honest about gaps;
                               # never let self-perception overshoot reality
MASTERY_FLOOR = 0.05
MASTERY_CEILING = 0.98
WEAK_THRESHOLD = 0.55         # below this = "weak topic, needs study"

# Hard floors per domain — if mastery drops below this, automatic
# remediation triggers (knowledge injection from domain modules).
# Floors are higher for core competencies (procurement, compliance,
# osint) and lower for supplementary areas (finance, relationships).
# Adapted from mastery_system.py DomainQuizEngine.
HARD_FLOORS: dict[str, float] = {
    "procurement":     0.65,
    "compliance":      0.70,
    "osint":           0.65,
    "technical":       0.65,
    "market_intel":    0.65,
    "competitor_intel": 0.65,
    "geopolitics":     0.60,
    "finance":         0.60,
    "relationships":   0.55,
    "legal":           0.70,
    "general":         0.50,
}

TOPICS = [
    "compliance", "procurement", "market_intel", "technical",
    "geopolitics", "osint", "finance", "relationships",
    "competitor_intel", "legal", "general",
]

# Load-bearing capability tags — capability-only, region-agnostic.
# Operator rebalanced 2026-04-22 to match the global-positioning doctrine
# (aria_global_positioning.md): ARIA is a GLOBAL defence broking advisor,
# so no single-region tags like angola_procurement or uk_compliance
# belong in the load-bearing core — regional coverage lives in the
# heatmap and source_validator.COVERAGE_DOMAINS instead.
# Any work that touches mastery scoring / weak-topic picking / reading-
# session queueing must preserve this list. A tag that silently falls
# out of scope is a capability regression.
CORE_MASTERY_TAGS = [
    # Languages of the major non-English defence markets
    "lang:pt", "lang:ar", "lang:fr", "lang:es", "lang:ru", "lang:zh",
    # Cross-cutting capability areas that apply in every region
    "sanctions", "nato_standards", "strategic_geography", "export_control",
]

# Topic detection patterns — used to tag every interaction with a topic.
# Enriched from mastery_prompt_builder.py's domain keyword taxonomy.
_TOPIC_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("compliance", re.compile(
        r"\b(?:sanction|embargo|ofac|ofsi|itar|ear|sitcl|siel|ogel|export\s+control|"
        r"licen[cs]e|debarment|euc|end.user|ata|ats|wassenaar|mtcr|nsg|hcoc|"
        r"dual.use|proliferation|diversion|att\s+article|arms\s+trade\s+treaty|"
        r"poca|bribery\s+act|mlr|money\s+laundering|sar|suspicious|kyc|aml|"
        r"ancex|ecju|bis\s+entity|denied\s+person|specially\s+designated)\b", re.I)),
    ("procurement", re.compile(
        r"\b(?:tender|rfp|rfq|rfi|contract|bid|procurement|acquisition|fms|"
        r"solicitation|award|lifecycle|capability\s+definition|"
        r"source\s+selection|best\s+value|lpta|tradeoff|evaluation\s+criteria|"
        r"ted\.europa|sam\.gov|contracts\s+finder|ungm|offset|industrial\s+"
        r"participation|counter.?trade|cpv\s+code|nato\s+stock|nsn|"
        r"fms\s+case|lor\s+letter|loa\s+letter|dsca|congressional\s+notification|"
        r"budget\s+execution|through.life\s+support|sustainment)\b", re.I)),
    ("market_intel", re.compile(
        r"\b(?:angola|mozambique|nigeria|kenya|saudi|uae|brazil|indonesia|"
        r"cplp|lusophone|market|country|sipri|iiss|jane.s|defence\s+spending|"
        r"military\s+budget|gdp\s+percent|demand\s+signal|"
        r"ghana|ethiopia|tanzania|uganda|rwanda|drc|senegal|"
        r"c[oô]te\s+d.ivoire|cameroon|mali|niger|burkina|chad|"
        r"india|pakistan|turkey|egypt|morocco|algeria|tunisia|"
        r"philippines|vietnam|thailand|malaysia|south\s+africa)\b", re.I)),
    ("technical", re.compile(
        r"\b(?:k9|himars|bayraktar|patriot|f-35|f-16|leopard|abrams|caliber|"
        r"calibre|eccn|ml\d{1,2}|weapon|system|specs?|stanag|aqap|aectp|"
        r"ifv|apc|mrap|howitzer|mortar|torpedo|missile|radar|sonar|"
        r"c4isr|ew\b|electronic\s+warfare|uav|uas|drone|munition|"
        r"detonator|explosive|propellant|warhead|fuse|primer|"
        r"ballistic|armou?red?\s+vehicle|redback|lynx|boxer|"
        r"nato\s+standard|def\s+stan|mil.std)\b", re.I)),
    ("geopolitics", re.compile(
        r"\b(?:nato|alliance|coup|regime|stability|conflict|geopolitical|"
        r"sadc|ecowas|au\s+mission|un\s+mission|peacekeeping|"
        r"insurgency|terrorism|separatist|civil\s+war|border\s+dispute|"
        r"arms\s+race|nuclear|non.proliferation|ceasefire|"
        r"diplomatic|bilateral|multilateral|mou|treaty|pact)\b", re.I)),
    ("osint", re.compile(
        r"\b(?:investigate|profile|due\s+diligence|background\s+check|crawl|"
        r"research|osint|trace|map\s+the|screen|vet|audit|"
        r"pdd|person\s+dd|pep\s+check|adverse\s+media|"
        r"ghost\s+detect|ubo|beneficial\s+owner|"
        r"open\s+source|intelligence\s+cycle|collection|"
        r"source\s+grading|reliability|credibility)\b", re.I)),
    ("finance", re.compile(
        r"\b(?:budget|spending|gdp|deal\s+(?:value|worth)|million|billion|"
        r"finance|funding|loan|credit|offset|payment|escrow|"
        r"letter\s+of\s+credit|bank\s+guarantee|performance\s+bond|"
        r"advance\s+payment|retention|commission|broker\s+fee|"
        r"forex|exchange\s+rate|inflation|fiscal)\b", re.I)),
    ("relationships", re.compile(
        r"\b(?:contact|minister|general|colonel|admiral|director|liaison|"
        r"tenure|meeting|relationship|humint|stakeholder|"
        r"decision.maker|influencer|gatekeeper|champion|"
        r"networking|introduction|referral|warm\s+lead|cold\s+call|"
        r"embassy|attach[eé]|trade\s+commissioner)\b", re.I)),
    ("competitor_intel", re.compile(
        r"\b(?:competitor|rival|paramount|elbit|baykar|norinco|rheinmetall|"
        r"bae|leonardo|raytheon|lockheed|boeing|thales|airbus|saab|"
        r"hanwha|hyundai\s+rotem|knds|nexter|krauss.maffei|"
        r"denel|armscor|otokar|aselsan|stm|tai|"
        r"csgc|poly\s+technologies|cetc|avic|cssc|"
        r"market\s+share|competitive\s+(?:advantage|position|landscape)|"
        r"win\s+rate|incumbent|displacer|price\s+war)\b", re.I)),
    ("legal", re.compile(
        r"\b(?:law|legal|regulation|statute|act\s+\d{4}|wassenaar|mtcr|nsg|"
        r"treaty|convention|jurisdiction|court|arbitration|dispute|"
        r"contract\s+law|force\s+majeure|indemnity|warranty|"
        r"intellectual\s+property|licensing\s+agreement|"
        r"end.user\s+certificate|export\s+licence|import\s+permit)\b", re.I)),
    # ── CORE_MASTERY_TAGS (cross-cutting capability tags) ───────────────
    # These four were in CORE_MASTERY_TAGS but absent from _TOPIC_PATTERNS,
    # so detect_topics() literally could not emit them and reading_session
    # could never lift their mastery scores. Six core tags were stuck at the
    # 0.491 floor as of 2026-04-27 (sanctions, nato_standards, strategic_
    # geography, export_control + lang:ru, lang:zh — the language ones do
    # have script-based detection further down). Patterns deliberately
    # overlap with `compliance` / `geopolitics` / `legal` so an article
    # about ITAR lifts BOTH compliance AND export_control simultaneously,
    # which is the right semantic.
    ("sanctions", re.compile(
        r"\b(?:ofac|ofsi|sdn(?:\s+list)?|specially\s+designated|"
        r"sanctions?\s+(?:list|regime|package|programme|program|target|"
        r"designation|update|removal)|asset\s+freeze|"
        r"sectoral\s+sanctions|secondary\s+sanctions|extraterritorial|"
        r"caatsa|magnitsky|opensanctions|eu\s+consolidated\s+(?:list|sanctions)|"
        r"un\s+sc(?:\s+sanctions)?|comprehensive\s+embargo|"
        r"embargoed\s+(?:country|state|destination)|"
        r"travel\s+ban|frozen\s+assets|de.?listing|sanctions?\s+evasion)\b",
        re.I)),
    ("nato_standards", re.compile(
        r"\b(?:stanag|aqap|aectp|natostd|nspa|nato\s+standardisation|"
        r"nato\s+standardization|nato\s+stock\s+number|nsn\s+\d|"
        r"nato\s+codification|allied\s+(?:joint|technical|"
        r"administrative)\s+publication|\bajp\s*-?\s*\d|\batp\s*-?\s*\d|"
        r"\baap\s*-?\s*\d|annex\s+to\s+stanag|"
        r"interoperability\s+standard|nato\s+(?:cert|certif)|"
        r"def\s+stan|mil[\s-]std|federation\s+mission\s+network|fmn)\b",
        re.I)),
    ("strategic_geography", re.compile(
        r"\b(?:choke[\s-]?point|strait\s+of|maritime\s+(?:trade|domain|chokepoint)|"
        r"sea[\s-]lane|key\s+terrain|forward[\s-](?:base|posture|operating\s+base)|"
        r"power\s+projection|sphere\s+of\s+influence|"
        r"strategic\s+(?:depth|frontier|location|corridor|hub|importance)|"
        r"littoral|landlocked|geostrategic|continental\s+shelf|"
        r"red\s+sea|south\s+china\s+sea|persian\s+gulf|"
        r"horn\s+of\s+africa|sahel|caucasus|balkans|"
        r"baltic\s+(?:states|approaches)|mediterranean|gibraltar|bab[\s-]el[\s-]mandeb|"
        r"hormuz|malacca|suez|panama\s+canal|northern\s+sea\s+route)\b",
        re.I)),
    ("export_control", re.compile(
        r"\b(?:itar|\bear\b|eccn|spire|sitcl|siel|ogel|otcl|"
        r"export\s+licen[cs]e|export[\s-]control|dual[\s-]use|"
        r"wassenaar|mtcr|nsg|hcoc|australia\s+group|"
        r"deemed\s+export|re[\s-]export|in[\s-]country\s+transfer|"
        r"end[\s-]use\s+(?:check|monitoring)|technology\s+transfer|"
        r"controlled\s+(?:item|technology|software|goods)|"
        r"munitions\s+list|usml|cml|eu\s+dual[\s-]use\s+regulation|"
        r"bis\s+entity|denied\s+person|debarment\s+list|"
        r"ddtc|brokering\s+(?:registration|licence|license)|"
        r"export\s+control\s+(?:act|order|reform))\b", re.I)),
]


def detect_topics(text: str) -> list[str]:
    """Tag a question/response with one or more topic categories.

    Language tags (`lang:ru`, `lang:zh`, `lang:ar`) are emitted from
    script-based heuristics — Cyrillic / CJK / Arabic Unicode ranges.
    Before this, no code path ever wrote to a `lang:*` mastery tag, so
    `lang:ru` and `lang:zh` sat at the 0.50 initial floor forever while
    pt/fr/es/ar had been seeded by earlier calibration runs. Require a
    minimum character count so short English copy doesn't accidentally
    tag itself with a non-Latin language.
    """
    if not text:
        return []
    matched = []
    for topic, pattern in _TOPIC_PATTERNS:
        if pattern.search(text):
            matched.append(topic)
    # Language-script tags. Thresholds are deliberately conservative:
    # 20+ chars of the target script means the text carries meaningful
    # content in that language, not just a quoted word or name.
    cyrillic = sum(1 for ch in text if "Ѐ" <= ch <= "ӿ")
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    arabic = sum(1 for ch in text if "؀" <= ch <= "ۿ")
    if cyrillic >= 20:
        matched.append("lang:ru")
    if cjk >= 20:
        matched.append("lang:zh")
    if arabic >= 20:
        matched.append("lang:ar")
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
    # Scaffold the 9 core-mastery tags so they are always visible in the
    # report — even with zero samples — instead of being invisibly absent.
    # Before this, tags only existed if something happened to update them,
    # which let the overall mastery rollup sit at 96% while load-bearing
    # capability cells were silently empty.
    for t in CORE_MASTERY_TAGS:
        if t not in _mastery_cache:
            _mastery_cache[t] = {"score": INITIAL_MASTERY, "samples": 0,
                                 "correct": 0, "wrong": 0, "last_practiced": 0}
    return _mastery_cache


async def _save_mastery() -> None:
    if _mastery_cache is not None:
        await rs.set_json(MASTERY_KEY, _mastery_cache, ex=180 * 86400)


async def update_mastery(topics: list[str], correct: bool, weight: float = 1.0) -> None:
    """Update mastery scores for the topics touched by an interaction.

    correct=True: ARIA's local answer matched the truth (or the teacher).
    correct=False: she got it wrong or diverged from the teacher.

    If a topic drops below its HARD_FLOOR, a remediation flag is set and
    the next weekly report + mastery prompt addendum will highlight it.
    The actual knowledge injection (e.g. re-ingest procurement_knowledge
    module) runs in the weekly loop, not inline here.
    """
    if not topics:
        return
    mastery = await _load_mastery()
    now = time.time()
    remediation_needed: list[str] = []
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
            delta = lr * (1 - m["score"])
            # Cap: single update cannot move score by more than 15pp
            delta = min(delta, 0.15)
            m["score"] = min(MASTERY_CEILING, m["score"] + delta)
        else:
            m["wrong"] = m.get("wrong", 0) + 1
            lr = MASTERY_LR_NEGATIVE * weight
            delta = lr * m["score"]
            # Cap: single update cannot move score by more than 15pp
            delta = min(delta, 0.15)
            m["score"] = max(MASTERY_FLOOR, m["score"] - delta)
        # Hard floor check — flag for remediation if breached
        floor = HARD_FLOORS.get(topic, 0.50)
        if m["score"] < floor:
            m["below_floor"] = True
            m["floor"] = floor
            remediation_needed.append(topic)
        else:
            m.pop("below_floor", None)
            m.pop("floor", None)
    await _save_mastery()

    # Log remediation needs so the weekly report and proactive loop
    # can act on them. Non-blocking fire-and-forget.
    if remediation_needed:
        logger.warning(
            "MASTERY HARD FLOOR BREACH: %s — remediation flagged",
            ", ".join(f"{t} ({mastery[t]['score']:.0%} < {HARD_FLOORS.get(t, 0.5):.0%})" for t in remediation_needed),
        )
        try:
            from . import capability_gaps
            for topic in remediation_needed:
                _t = asyncio.create_task(capability_gaps.record_gap(
                    gap_type="knowledge_gap",
                    detail=f"Mastery for '{topic}' dropped below hard floor ({mastery[topic]['score']:.0%} < {HARD_FLOORS.get(topic, 0.5):.0%}). Remediation: re-inject domain knowledge.",
                    source="student.update_mastery",
                ))
                _t.add_done_callback(lambda t: t.result() if not t.cancelled() and not t.exception() else None)
        except Exception:
            pass


async def reset_mastery_scores() -> dict:
    """Reset all mastery scores to a fair baseline. Use after fixing a
    bug that corrupted the scores (e.g., the weight=0.7 divergence
    penalty that dropped all topics from 81% to 14%)."""
    global _mastery_cache
    mastery = await _load_mastery()
    for topic in mastery:
        # Reset to accuracy-based estimate: correct/samples
        samples = mastery[topic].get("samples", 0)
        correct = mastery[topic].get("correct", 0)
        if samples > 10:
            accuracy = correct / samples
            mastery[topic]["score"] = max(INITIAL_MASTERY, accuracy * 0.9)
        else:
            mastery[topic]["score"] = INITIAL_MASTERY
        mastery[topic].pop("below_floor", None)
        mastery[topic].pop("floor", None)
    await _save_mastery()
    return {t: round(mastery[t]["score"], 3) for t in mastery}


async def get_mastery_report() -> dict:
    mastery = await _load_mastery()
    total_samples = sum(m.get("samples", 0) for m in mastery.values())
    weak = [t for t, m in mastery.items() if m.get("score", 0) < WEAK_THRESHOLD]
    strong = [t for t, m in mastery.items() if m.get("score", 0) >= 0.80]
    overall = (
        sum(m.get("score", 0) * m.get("samples", 0) for m in mastery.values())
        / max(total_samples, 1)
    ) if total_samples > 0 else INITIAL_MASTERY

    # Core-mastery rollup — unweighted mean over the 9 load-bearing tags,
    # independent of sample count. This prevents the sample-weighted
    # `overall` from hiding weak core cells that have been starved of
    # practice. Headline mastery is the minimum of the two so the
    # dashboard cannot read higher than the weakest rollup.
    core_scores = [mastery[t].get("score", INITIAL_MASTERY)
                   for t in CORE_MASTERY_TAGS if t in mastery]
    core_mastery = (sum(core_scores) / len(core_scores)) if core_scores else INITIAL_MASTERY
    headline_mastery = min(overall, core_mastery)
    core_weak = [t for t in CORE_MASTERY_TAGS
                 if mastery.get(t, {}).get("score", 0) < WEAK_THRESHOLD]
    return {
        "overall_mastery": round(overall, 3),
        "core_mastery": round(core_mastery, 3),
        "headline_mastery": round(headline_mastery, 3),
        "core_mastery_breakdown": {
            t: round(mastery.get(t, {}).get("score", INITIAL_MASTERY), 3)
            for t in CORE_MASTERY_TAGS
        },
        "core_weak_topics": core_weak,
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
    await rs.set_json(QUIZ_HISTORY_KEY, history, ex=180 * 86400)

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

    # Pick feeds aligned with weak topics. Include CORE_MASTERY_TAGS in
    # the weak-topic candidate pool — before 2026-04-26 only TOPICS was
    # consulted, which silently excluded lang:* (RU/ZH/AR/PT/FR/ES) and
    # the four cross-cutting capability tags (sanctions / nato_standards
    # / strategic_geography / export_control). Result: TASS+Huanqiu RSS
    # feeds added in `6014587` were fetched but always deprioritised at
    # selection, so lang:ru and lang:zh stayed at the 0.50 floor.
    mastery = await _load_mastery()
    weak_pool = list(TOPICS) + [t for t in CORE_MASTERY_TAGS if t not in TOPICS]
    weak_topics = sorted(
        weak_pool, key=lambda t: mastery.get(t, {}).get("score", INITIAL_MASTERY)
    )[:5]

    # R-F163 (2026-05-11): drain the proactive reading queue first. When
    # `proactive.prepare_weak_topics` or `ecosystem_reassess` enqueue a
    # topic, that's a stronger signal than the global mastery score —
    # the operator's recent work, or detected drift, said "study this".
    # Queue topics that ARE in weak_pool get promoted to the front of
    # weak_topics; queue topics that are NOT (region-specific tags like
    # angola_procurement, uk_compliance) are handed to a targeted web
    # search branch lower in this function so they actually get studied.
    queued_topics: list[str] = []
    starved_queue_topics: list[str] = []
    try:
        from . import proactive as _prc
        queue_entries = await _prc.get_reading_queue(limit=10)
        for q in queue_entries or []:
            t = (q.get("topic") if isinstance(q, dict) else "") or ""
            t = t.strip()
            if not t or t in queued_topics or t in starved_queue_topics:
                continue
            if t in weak_pool:
                queued_topics.append(t)
            else:
                starved_queue_topics.append(t)
        # Promote queued tags to the front, then dedupe by appending the
        # mastery-driven weak set behind. Cap at 5 so the reading session
        # remains time-bounded.
        if queued_topics:
            weak_topics = (queued_topics + [t for t in weak_topics if t not in queued_topics])[:5]
    except Exception as _qe:
        logger.debug("[student] reading_session queue drain failed: %s", _qe)
    logger.info(
        "[student] reading session — focused on weak topics: %s "
        "(queued=%d, starved_queue=%d)",
        weak_topics, len(queued_topics), len(starved_queue_topics),
    )

    # Get fresh articles from a rotating set of feeds.
    # Boost: when a CORE_MASTERY_TAG is weak, force at least one
    # matching feed into the pool. Random sampling alone rarely picks
    # the relevant feeds (lang feeds are 4/~30, export-controls is 1/~30).
    # 2026-04-26 (`ad3dc9e`) added language force-feeds; 2026-04-27
    # extended this to the four cross-cutting capability tags so they
    # don't stay frozen at 1-sample/0%-accuracy.
    weak_topic_to_categories: dict[str, list[str]] = {
        "lang:ru": ["russia_"],
        "lang:zh": ["china_"],
        "lang:ar": ["arabic_"],
        "sanctions": ["export_controls", "arms_trade"],
        "nato_standards": ["europe_defence", "defence_policy", "defence_research"],
        "strategic_geography": ["geopolitics", "strategy"],
        "export_control": ["export_controls", "arms_trade"],
    }
    forced_feeds: list[dict] = []
    for tag, cat_prefixes in weak_topic_to_categories.items():
        if tag not in weak_topics:
            continue
        for f in RESEARCH_FEEDS:
            cat = f.get("category") or ""
            if any(cat.startswith(p) for p in cat_prefixes) and f not in forced_feeds:
                forced_feeds.append(f)
                break  # one feed per weak tag is enough per session
    remaining_slots = max(0, 8 - len(forced_feeds))
    other_pool = [f for f in RESEARCH_FEEDS if f not in forced_feeds]
    feeds_pool = forced_feeds + random.sample(
        other_pool, min(remaining_slots, len(other_pool))
    )
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

    # Score each article by how well its title matches our weak topics.
    # Match against title AND first 200 chars of the description/summary
    # so non-Latin titles aren't penalised (TASS RSS titles are short
    # plain Russian — running detect_topics on title alone yields fewer
    # script chars than the 20-char threshold for lang:ru).
    def _topic_match(art: dict) -> int:
        text = (art.get("title") or "") + " " + (art.get("summary") or art.get("description") or "")[:200]
        topics_in_text = detect_topics(text)
        return sum(1 for t in topics_in_text if t in weak_topics)

    articles_to_read.sort(key=lambda a: -_topic_match(a))
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

    # ── R-F163 (2026-05-11) targeted branch for starved queue topics ──
    # Tags like `angola_procurement`, `uk_compliance`, `uk_export_control`
    # aren't in TOPICS or CORE_MASTERY_TAGS, so the RSS-based selection
    # above cannot lift their mastery. Those tags came in via the
    # proactive reading queue (region-specific weak signals). Hand each
    # one to web_search with a topic-shaped query and ingest the top 2
    # results so the mastery score for the SPECIFIC tag actually moves.
    starved_studied: list[dict] = []
    if starved_queue_topics:
        try:
            from . import researcher as _res
            from . import knowledge as _kb
            for stag in starved_queue_topics[:3]:
                # Tag-name shape: 'angola_procurement' -> 'angola procurement'
                pretty = stag.replace("_", " ").replace(":", " ")
                query = f"{pretty} defence procurement news 2026"
                try:
                    resp = await _res.web_search(query=query, max_results=3)
                except Exception as _se:
                    logger.debug("[student] starved-tag web_search failed for %s: %s", stag, _se)
                    continue
                results = (resp or {}).get("results", []) if isinstance(resp, dict) else []
                for hit in (results or [])[:2]:
                    if not isinstance(hit, dict):
                        continue
                    url = hit.get("url") or hit.get("link") or ""
                    title = hit.get("title") or ""
                    snippet = hit.get("snippet") or hit.get("description") or ""
                    if not url or not title:
                        continue
                    try:
                        await _kb.store_fact(
                            topic=title[:80],
                            content=(snippet or title)[:800],
                            source=f"reading_starved:{stag}",
                            confidence="ASSESSED",
                        )
                    except Exception as _ke:
                        logger.debug("[student] starved-tag kb store failed: %s", _ke)
                    # Lift mastery on the exact starved tag (not the
                    # auto-detected ones — that would re-write to lang:*
                    # or compliance, and we explicitly need the named tag
                    # to move so the proactive alert stops repeating).
                    await update_mastery([stag], correct=True, weight=0.2)
                    starved_studied.append({
                        "tag": stag,
                        "title": title[:120],
                        "url": url,
                    })
        except Exception as _bre:
            logger.warning("[student] starved-tag branch failed: %s", _bre)

    # Log the session
    log = await rs.get_json(READING_LOG_KEY) or []
    log.append({
        "ts": time.time(),
        "articles_read": len(studied) + len(starved_studied),
        "topics_focused": weak_topics,
        "starved_studied": [s["tag"] for s in starved_studied],
    })
    log = log[-100:]
    await rs.set_json(READING_LOG_KEY, log, ex=180 * 86400)

    logger.info(
        "[student] reading session complete: %d articles (incl. %d starved-tag hits)",
        len(studied) + len(starved_studied),
        len(starved_studied),
    )
    return {
        "articles_read": len(studied) + len(starved_studied),
        "weak_topics_studied": weak_topics,
        "studied": studied,
        "starved_studied": starved_studied,
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
    await rs.set_json(DIVERGENCE_KEY, log, ex=180 * 86400)

    # Update mastery from divergence comparison. IMPORTANT: the local
    # reasoning stack produces short, factual answers while the cloud
    # LLM produces long narrative responses. Jaccard similarity between
    # these is ALWAYS low even when both are correct — a 50-word local
    # answer vs a 2000-word cloud answer will never exceed 0.3 Jaccard.
    #
    # Previous weights (correct=False, weight=0.7 on <0.5 similarity)
    # were catastrophically punitive — mastery dropped from 81% to 14%
    # across ALL topics because EVERY comparison triggered the penalty.
    #
    # Fix: only penalise when local DIDN'T ANSWER AT ALL (no response
    # from reasoning router), and use much lower weights. The positive
    # signal from successful cloud responses (weight=0.15 in aria_engine)
    # should be the primary mastery driver, not the divergence comparison.
    if local_response is None or len((local_response or "").strip()) < 20:
        # Local couldn't answer — genuine knowledge gap
        await update_mastery(topics, correct=False, weight=0.1)
    elif similarity < 0.3:
        # Very low similarity — possible gap, but soft penalty
        await update_mastery(topics, correct=False, weight=0.05)
    else:
        # Reasonable similarity or local answered — positive signal
        await update_mastery(topics, correct=True, weight=0.1)


async def compare_local_silently(question: str, cloud_response: str) -> dict:
    """After a cloud LLM responds, run the local stack on the SAME question
    and compare. The cloud's answer is treated as the teacher's; the local
    stack's answer is the student's attempt. The divergence becomes
    study material.

    This is what makes ARIA actively learn from her teacher rather than
    passively cache his answers.
    """
    try:
        # F53 2026-04-28: pass silent=True so the symbolic reasoner does not
        # double-record the same `no_symbolic_rule` capability gap that the
        # pre-LLM try_local_reasoning call already recorded for this query.
        local = await reasoning_router.try_local_reasoning(question, silent=True)
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


# ── Regional mastery (topic × region heat map) ────────────────────────────

# Heat-map regions — updated 2026-04-17 PM per user feedback on the
# NAK-session gap review. Turkey is ITS OWN column (not NATO-merged,
# per aria_global_positioning.md hard rule). LatAm is split between
# Lusophone (Brazil anchor) and non-Lusophone (Peru anchor). Central
# Africa is explicit. South Asia and Southeast Asia are separated.
# Balkans added as a Tier 2 positioning column.
REGIONS = [
    "lusophone", "west_africa", "east_africa", "central_africa",
    "north_africa", "southern_africa", "mena", "gulf",
    "turkey",              # standalone — not NATO
    "south_asia", "southeast_asia",
    "latam_lusophone", "latam_non_lusophone",
    "europe", "balkans", "nato",
    "global",
]

_REGION_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("lusophone", re.compile(
        r"\b(?:angola|mozambique|cape\s+verde|cabo\s+verde|guinea.bissau|"
        r"são\s+tomé|cplp|lusophone|portuguese|fadm|faa|fasb)\b", re.I)),
    ("west_africa", re.compile(
        r"\b(?:nigeria|ghana|senegal|côte\s+d.ivoire|cameroon|ecowas|"
        r"niger|mali|burkina|togo|benin|sierra\s+leone|liberia|aes\s+alliance)\b", re.I)),
    ("east_africa", re.compile(
        r"\b(?:ethiopia|somalia|eac|amisom|djibouti|eritrea|"
        r"south\s+sudan|sudan)\b", re.I)),
    ("central_africa", re.compile(
        r"\b(?:d\.?r\.?c\.?|drc|democratic\s+republic\s+of\s+(?:the\s+)?congo|"
        r"kinshasa|rwanda|kigali|uganda|kampala|tanzania|dodoma|dar\s+es\s+salaam|"
        r"kenya|nairobi|burundi|m23|monusco)\b", re.I)),
    ("north_africa", re.compile(
        r"\b(?:libya|algeria|morocco|tunisia|egypt|sahel|maghreb)\b", re.I)),
    ("southern_africa", re.compile(
        r"\b(?:south\s+africa|sadc|botswana|namibia|zimbabwe|zambia)\b", re.I)),
    ("mena", re.compile(
        r"\b(?:middle\s+east|syria|iraq|iran|jordan|lebanon|palestine|israel)\b", re.I)),
    ("gulf", re.compile(
        r"\b(?:saudi|uae|qatar|oman|kuwait|bahrain|gcc|edge\s+group|sami|tawazun|gami|vision\s+2030)\b", re.I)),
    ("turkey", re.compile(
        r"\b(?:turkey|türkiye|ssb|baykar|aselsan|roketsan|tai|stm|"
        r"savunma\s+sanayii|tb2|bayraktar|nihai\s+kullan)\b", re.I)),
    ("south_asia", re.compile(
        r"\b(?:india|delhi|bengaluru|hindustan|hal\b|drdo|"
        r"pakistan|islamabad|bangladesh|dhaka|sri\s+lanka|nepal|"
        r"make\s+in\s+india|dap\s*2020|dpp)\b", re.I)),
    ("southeast_asia", re.compile(
        r"\b(?:indonesia|jakarta|philippines|manila|vietnam|hanoi|thailand|"
        r"bangkok|myanmar|burma|malaysia|kuala\s+lumpur|singapore|"
        r"asean|aukus|quad)\b", re.I)),
    ("latam_lusophone", re.compile(
        r"\b(?:brazil|brasília|brasilia|brasil|são\s+paulo|rio\s+de\s+janeiro|"
        r"embraer|taurus\s+armas|avibras)\b", re.I)),
    ("latam_non_lusophone", re.compile(
        r"\b(?:colombia|bogotá|bogota|indumil|cotecmar|"
        r"peru|lima|seace|"
        r"chile|santiago|dgmn|fidae|"
        r"argentina|buenos\s+aires|fabricaciones\s+militares|"
        r"ecuador|quito|"
        r"mexico|méxico|sedena|"
        r"mercosur|central\s+america|panama|panamá)\b", re.I)),
    ("europe", re.compile(
        r"\b(?:ukraine|kyiv|poland|warsaw|romania|bucharest|baltic|czech|hungary|"
        r"european|pesco|edirpa|france|germany|italy|spain|portugal)\b", re.I)),
    ("balkans", re.compile(
        r"\b(?:serbia|belgrade|bosnia|sarajevo|kosovo|pristina|"
        r"north\s+macedonia|skopje|albania|tirana|montenegro|podgorica|"
        r"slovenia|ljubljana|croatia|zagreb|balkans|former\s+yugoslav)\b", re.I)),
    ("nato", re.compile(
        r"\b(?:nato|alliance|article\s+5|stanag|nspa|nsn)\b", re.I)),
]

REGIONAL_MASTERY_KEY = "crucix:aria:student:regional_mastery"
_regional_cache: dict | None = None


def detect_regions(text: str) -> list[str]:
    """Detect which regions a text relates to."""
    if not text:
        return ["global"]
    regions = []
    for region, pattern in _REGION_PATTERNS:
        if pattern.search(text):
            regions.append(region)
    return regions or ["global"]


async def _load_regional_mastery() -> dict:
    global _regional_cache
    if _regional_cache is not None:
        return _regional_cache
    raw = await rs.get_json(REGIONAL_MASTERY_KEY)
    if isinstance(raw, dict):
        _regional_cache = raw
    else:
        _regional_cache = {}
    return _regional_cache


async def _save_regional_mastery() -> None:
    if _regional_cache is not None:
        await rs.set_json(REGIONAL_MASTERY_KEY, _regional_cache, ex=180 * 86400)


async def update_regional_mastery(
    topics: list[str], regions: list[str], correct: bool, weight: float = 1.0,
) -> None:
    """Update mastery for topic×region combinations."""
    if not topics or not regions:
        return
    rm = await _load_regional_mastery()
    alpha = min(0.3, 0.1 * weight)
    for topic in topics:
        if topic not in TOPICS:
            continue
        for region in regions:
            key = f"{topic}:{region}"
            if key not in rm:
                rm[key] = {"score": INITIAL_MASTERY, "samples": 0}
            entry = rm[key]
            obs = 1.0 if correct else 0.0
            entry["score"] = entry["score"] + alpha * (obs - entry["score"])
            entry["samples"] = entry.get("samples", 0) + 1
    _regional_cache.update(rm)
    await _save_regional_mastery()


async def get_regional_heatmap() -> dict:
    """Return mastery heat map: topic × region scores."""
    rm = await _load_regional_mastery()
    heatmap: dict[str, dict[str, float]] = {}
    for key, val in rm.items():
        if ":" not in key:
            continue
        topic, region = key.split(":", 1)
        if topic not in heatmap:
            heatmap[topic] = {}
        heatmap[topic][region] = round(val.get("score", INITIAL_MASTERY), 3)
    # Find weak cells
    weak_cells = []
    for topic, regions in heatmap.items():
        for region, score in regions.items():
            if score < WEAK_THRESHOLD:
                weak_cells.append({"topic": topic, "region": region, "score": score})
    weak_cells.sort(key=lambda x: x["score"])
    return {"heatmap": heatmap, "weak_cells": weak_cells[:20]}


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

    # Apply calibration auto-tune offsets. When ARIA is consistently
    # overconfident across runs, the auto-tune module raises these
    # thresholds so more claims fall into the constrained tiers. When
    # underconfident, it lowers them. Falls back to the raw constants
    # if auto_tune is unavailable or Redis is offline.
    try:
        from . import calibration_auto_tune as _cat
        _weak_th = await _cat.get_effective_threshold("weak", WEAK_THRESHOLD)
        _crit_th = await _cat.get_effective_threshold("critical", 0.40)
    except Exception:
        _weak_th = WEAK_THRESHOLD
        _crit_th = 0.40

    weak_relevant: list[tuple[str, float]] = []
    for t in topics:
        m = mastery.get(t, {})
        score = m.get("score", INITIAL_MASTERY)
        samples = m.get("samples", 0)
        if score < _weak_th and samples >= 2:
            weak_relevant.append((t, score))

    if not weak_relevant:
        return ""

    # Separate critical (<_crit_th) from weak (_crit_th..._weak_th) topics
    _CRITICAL_THRESHOLD = _crit_th
    critical = [(t, s) for t, s in weak_relevant if s < _CRITICAL_THRESHOLD]
    weak_only = [(t, s) for t, s in weak_relevant if s >= _CRITICAL_THRESHOLD]

    lines = [
        "⚠ MASTERY ALERT — MANDATORY BEHAVIORAL RULES for this response."
    ]

    # ── CRITICAL mastery (<40%) — human review required ──────────
    if critical:
        lines.append("")
        lines.append("🔴 CRITICAL MASTERY DEFICIT — the following topic(s) "
                     "are below 40%. HUMAN REVIEW REQUIRED on any output "
                     "touching these domains:")
        for topic, score in sorted(critical, key=lambda x: x[1]):
            lines.append(f"  • {topic}: mastery {score:.0%} — CRITICAL")
        lines.append("")
        lines.append("CRITICAL-TIER RULES (override everything below):")
        lines.append("C1. CONFIDENCE FLOOR — Maximum allowed tag: [UNCERTAIN]. "
                     "You MUST NOT use [CONFIRMED], [PROBABLE], or [ASSESSED] "
                     "on any claim touching the critical topic(s). Every claim "
                     "must carry [UNCERTAIN — low mastery, human review required].")
        lines.append("C2. HUMAN REVIEW FLAG — Begin your response with: "
                     "\"⚠ LOW-CONFIDENCE DOMAIN: My track record on [topic] is "
                     "poor (mastery [X]%). This response requires human verification "
                     "before any external use.\" This flag is non-negotiable.")
        lines.append("C3. NO RECOMMENDATIONS — You MUST NOT make GO/NO-GO "
                     "recommendations on critical-mastery topics. Present "
                     "the evidence you have and explicitly state that a human "
                     "must make the call.")
        lines.append("C4. TRIPLE-SOURCE RULE — Every material fact MUST cite "
                     "at least 3 independent sources OR carry [UNVERIFIED]. "
                     "Two sources is not enough at critical mastery.")

    # ── WEAK mastery (40-55%) — constrained but operational ──────
    if weak_only:
        lines.append("")
        lines.append("🟠 LOW MASTERY — the following topic(s) are below "
                     f"{WEAK_THRESHOLD:.0%}:")
        for topic, score in sorted(weak_only, key=lambda x: x[1]):
            lines.append(f"  • {topic}: mastery {score:.0%}")
        lines.append("")
        lines.append("LOW-TIER RULES:")
        lines.append("1. CONFIDENCE CAP — You MUST NOT use [CONFIRMED] on any "
                     "claim touching the weak topic(s). Maximum allowed: "
                     "[PROBABLE] with inline citation. Use [ASSESSED] or "
                     "[UNCERTAIN] if you have fewer than 2 independent sources.")
        lines.append("2. DOUBLE-SOURCE RULE — Every material fact on the weak "
                     "topic(s) MUST cite at least 2 independent sources OR "
                     "carry an explicit [SINGLE SOURCE — UNVERIFIED] flag.")
        lines.append("3. EXPLICIT GAP DISCLOSURE — If you are unsure about "
                     "any aspect of the weak topic(s), say so explicitly: "
                     "\"My knowledge on [topic] has been unreliable recently "
                     "— I recommend independent verification.\"")
        lines.append("4. SEARCH BEFORE RECALL — For weak topic(s), prefer "
                     "running a search tool over relying on memory/RAG.")

    # ── REGIONAL MASTERY CHECK — missing cells and uninformed prior ──
    # Check if the query hits topic×region combinations with no data
    # (-- cells) or uninformed prior (≤55%). These are the most
    # dangerous gaps because ARIA answers confidently from noise.
    _UNINFORMED_PRIOR_THRESHOLD = 0.56  # anything at or below initial EWMA
    try:
        regions = detect_regions(message)
        rm = await _load_regional_mastery()
        missing_cells = []
        prior_cells = []
        for t in topics:
            if t not in TOPICS:
                continue
            for r in regions:
                key = f"{t}:{r}"
                if key not in rm or rm[key].get("samples", 0) < 2:
                    missing_cells.append(f"{t}/{r}")
                elif rm[key].get("score", 0.5) <= _UNINFORMED_PRIOR_THRESHOLD:
                    prior_cells.append((f"{t}/{r}", rm[key].get("score", 0.5)))

        if missing_cells:
            lines.append("")
            lines.append("🔴 NO VERIFIED DATA — the following topic/region "
                         "combinations have ZERO data points:")
            for cell in missing_cells[:8]:
                lines.append(f"  • {cell} — NO DATA")
            lines.append("MANDATORY: Begin your response with: \"⚠ I have no "
                         "verified intelligence for [topic] in [region]. The "
                         "following is based on general training data, not "
                         "verified sources. Human verification required before "
                         "any action.\"")

        if prior_cells:
            lines.append("")
            lines.append("🟠 UNINFORMED PRIOR — the following cells are at or "
                         "below the initial baseline (possibly noise, not knowledge):")
            for cell, score in prior_cells[:8]:
                lines.append(f"  • {cell}: {score:.0%}")
            lines.append("Treat any output on these topic/region combinations "
                         "as UNVERIFIED. Do not use [CONFIRMED] or [PROBABLE].")
    except Exception:
        pass

    lines.append("")
    lines.append("These rules are automatically lifted when mastery "
                 f"recovers above {WEAK_THRESHOLD:.0%}. Critical-tier "
                 f"rules lift at {_CRITICAL_THRESHOLD:.0%}.")
    return "\n".join(lines)


async def lift_all_topics(bump: float) -> dict[str, float]:
    """Directly add `bump` to every topic's mastery score.

    Used by the self-calibration correction in calibration_review when
    ground-truth accuracy is consistently higher than self-assessed
    mastery. This is the "reality pulls self-perception up" feedback
    loop — not organic learning, but it keeps the scores useful when
    the EWMA lags the evidence.

    Capped at MASTERY_CEILING per topic. Returns the new scores keyed
    by topic name.

    Safety: only called when calibration_review detects UNDERCONFIDENT
    (delta < -10pp). Never pulls scores DOWN — that path stays with
    the organic negative learning rate so real gaps surface honestly.
    """
    if bump <= 0:
        return {}
    mastery = await _load_mastery()
    new_scores: dict[str, float] = {}
    now = time.time()
    for topic in TOPICS:
        if topic not in mastery:
            mastery[topic] = {"score": INITIAL_MASTERY, "samples": 0,
                               "correct": 0, "wrong": 0, "last_practiced": 0}
        m = mastery[topic]
        old_score = m.get("score", INITIAL_MASTERY)
        new_score = min(MASTERY_CEILING, old_score + bump)
        m["score"] = new_score
        m["last_practiced"] = now
        # Record that this lift was calibration-driven, NOT organic
        m["last_calibration_lift_at"] = now
        m["last_calibration_lift_bump"] = round(bump, 4)
        new_scores[topic] = new_score
    _mastery_cache.update(mastery)
    await _save_mastery()
    logger.info(
        "Calibration-driven mastery lift: +%.3f on %d topics",
        bump, len(new_scores),
    )
    return new_scores
