"""R-F441 — source-language adverse-media probes.

ARIA's 11-language fanout (web_explorer._LANG_FANOUT_TRIGGERS)
already detects which target languages a query is relevant for —
e.g. an entity with "Russia" in the description triggers a `ru`
search. But the fanout passes a GENERIC query string. R-F441 adds
per-language ADVERSE-MEDIA keyword sets so the search actually
probes for corruption / sanctions / fraud / money-laundering /
war-crime mentions in the native language, where the real
reporting often lives untranslated.

Coverage: 11 languages with operator-relevant adverse terms each.
Lists are conservative and deliberately literal — translation
nuance is preserved by using the native script + a transliteration
fallback only where helpful.
"""
from __future__ import annotations

from typing import Iterable

# Per-language adverse-media term lists. Each set is small (8-12
# terms) — broad enough to catch real reporting, narrow enough to
# avoid "happy news" false positives. Native script preferred; ASCII
# transliteration only when the search backend can't handle the
# script reliably.
ADVERSE_TERMS: dict[str, list[str]] = {
    # ── Russian — covers Kremlin-friendly outlets + opposition reporting
    "ru": [
        "коррупция",       # corruption
        "санкции",         # sanctions
        "мошенничество",   # fraud
        "отмывание",       # laundering
        "взяточничество",  # bribery
        "хищение",         # embezzlement
        "обвинение",       # accusation
        "расследование",   # investigation
    ],
    # ── Mandarin (simplified) — Chinese state + Hong Kong reporting
    "zh": [
        "制裁",   # sanctions
        "腐败",   # corruption
        "贪污",   # graft / embezzlement
        "贿赂",   # bribery
        "欺诈",   # fraud
        "洗钱",   # money-laundering
        "调查",   # investigation
        "指控",   # accusation
    ],
    # ── Arabic — Gulf + Levant + North Africa reporting
    "ar": [
        "فساد",      # corruption
        "عقوبات",    # sanctions
        "احتيال",    # fraud
        "غسل أموال", # money laundering
        "رشوة",      # bribery
        "اختلاس",    # embezzlement
        "تحقيق",     # investigation
        "اتهام",     # accusation
    ],
    # ── Persian (Farsi) — Iran-relevant DD
    "fa": [
        "فساد",      # corruption
        "تحریم",     # sanctions
        "کلاهبرداری",# fraud
        "پولشویی",   # money laundering
        "رشوه",      # bribery
        "اختلاس",    # embezzlement
        "تحقیقات",   # investigation
        "متهم",      # accused
    ],
    # ── Portuguese — Lusophone Africa + Brazil + Portugal
    "pt": [
        "corrupção",
        "sanções",
        "fraude",
        "lavagem de dinheiro",
        "suborno",
        "desvio",
        "investigação",
        "acusação",
    ],
    # ── Spanish — LatAm + Spain
    "es": [
        "corrupción",
        "sanciones",
        "fraude",
        "lavado de dinero",
        "soborno",
        "desfalco",
        "investigación",
        "acusación",
    ],
    # ── French — France + Francophone Africa + Lebanon
    "fr": [
        "corruption",
        "sanctions",
        "fraude",
        "blanchiment",
        "pot-de-vin",
        "détournement",
        "enquête",
        "accusation",
    ],
    # ── German — DACH + EU enforcement
    "de": [
        "korruption",
        "sanktionen",
        "betrug",
        "geldwäsche",
        "bestechung",
        "veruntreuung",
        "ermittlung",
        "anklage",
    ],
    # ── Italian
    "it": [
        "corruzione",
        "sanzioni",
        "frode",
        "riciclaggio",
        "tangente",
        "appropriazione indebita",
        "indagine",
        "accusa",
    ],
    # ── Turkish — Turkey + diaspora
    "tr": [
        "yolsuzluk",
        "yaptırım",
        "dolandırıcılık",
        "kara para aklama",
        "rüşvet",
        "zimmet",
        "soruşturma",
        "iddianame",
    ],
    # ── Ukrainian — context for sanctions / Russia-adjacent DD
    "uk": [
        "корупція",
        "санкції",
        "шахрайство",
        "відмивання",
        "хабар",
        "розкрадання",
        "розслідування",
        "звинувачення",
    ],
}


def supported_languages() -> list[str]:
    """Languages with adverse-media term sets defined."""
    return sorted(ADVERSE_TERMS.keys())


def build_adverse_query(entity: str, lang: str, *, max_terms: int = 6) -> str:
    """Build a single-language adverse-media query string.

    Returns `<entity> (term1 OR term2 OR ...)` — the OR-joined term
    block lets a backend with proper boolean support match ANY of the
    adverse terms in the same article as the entity name. Backends
    that don't honour OR will degrade to the first term. `max_terms`
    caps the OR-block size so query length stays sane.

    If the language is unsupported, returns the bare entity name —
    callers can fall back to the standard fanout.
    """
    entity = (entity or "").strip()
    if not entity:
        return ""
    lang_lc = (lang or "").lower()
    terms = ADVERSE_TERMS.get(lang_lc) or []
    if not terms:
        return entity
    use_terms = terms[:max_terms]
    # R-F996 — wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="adverse_media_polyglot",
        summary="Build Adverse Query",
        source_id="adverse_media_polyglot:R-F996",
    )

    return f'"{entity}" (' + " OR ".join(use_terms) + ")"


def build_queries_for_languages(
    entity: str,
    languages: Iterable[str],
    *,
    max_terms: int = 6,
) -> list[tuple[str, str]]:
    """Return list of (lang, query_string) for every supported language.

    Languages not in ADVERSE_TERMS are silently skipped — the caller
    can still run a vanilla search for them via the existing fanout.
    """
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for lang in languages or []:
        lang_lc = (lang or "").lower()
        if lang_lc in seen or lang_lc not in ADVERSE_TERMS:
            continue
        seen.add(lang_lc)
        q = build_adverse_query(entity, lang_lc, max_terms=max_terms)
        if q:
            out.append((lang_lc, q))
    return out

# R-F2119 §21a — wire failure handler for adverse_media_polyglot
try:
    wire_failure(module="adverse_media_polyglot", detail="module shutdown",
                gap_type="engine_failure", source="adverse_media_polyglot:shutdown")
except Exception:
    pass
