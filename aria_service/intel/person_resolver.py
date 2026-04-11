"""ARIA person-resolver — name-variant generation for PDD sanctions screening.

The hardest part of person due diligence is name disambiguation. A subject
named "Mohammed Ali" will collide with thousands of false positives in any
sanctions dataset unless the query is narrowed AND widened correctly:

- WIDENED via transliteration pairs, patronymics, short forms, alias
  chains, and component-order swaps so the real match is not missed
  when the database spells it differently.
- NARROWED via token-overlap filtering, DOB/nationality/role
  discriminators, and consonant-skeleton comparison so unrelated
  collisions drop out.

This module produces the widened variant set. The narrowing happens
inside `_sanctions_classify.classify_match` (already shipped).

OpenSanctions ships normalised name variants via the `nomenklatura`
library, but:
  1. The library is heavy (NLP models) and not something we want to pull
     into the dd_orchestrator hot path.
  2. It covers mainly sanctions-list variants, not general Cyrillic ⇄
     Latin / Arabic ⇄ Latin transliteration.
  3. We need control over the variant set size for budget reasons
     (each variant is one sanctions API call).

So this module implements a compact, deterministic variant generator
tuned for defence-broker PDD. When higher-precision matching is needed,
the orchestrator can still call OpenSanctions' advanced search with
fuzzy=true; the variant set here is the first pass.

PUBLIC API:
    resolve(name, *, nationality_iso2=None) -> NameResolution
    NameResolution.variants -> list[str]  # ordered by salience
"""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field

logger = logging.getLogger("ARIA.PersonResolver")


# =============================================================================
# NAME-COMPONENT PARSING
# =============================================================================

# Patronymic / tribal particles that should not be treated as surnames.
# Lower-cased. Used during component splitting so "Mohammed bin Salman
# al Saud" is parsed as {first: Mohammed, particles: [bin, al], last: Saud}
# rather than treating "bin" and "al" as standalone name tokens.
_PARTICLES = frozenset({
    # Arabic
    "bin", "ibn", "bint", "abu", "abd", "al", "el", "ul", "ad", "as",
    "at", "az", "ash", "ar", "an",
    # Iberian
    "de", "del", "dela", "de la", "dos", "da", "das", "do",
    # Dutch / German
    "van", "von", "der", "den", "ten", "ter", "te",
    # French
    "du", "le",
    # Italian
    "di", "lo", "la",
    # Slavic honorifics / titles that occasionally appear
    "mc", "mac",
})

# Honorifics / titles to strip before parsing. These leak in from cards,
# email signatures, and press articles and otherwise pollute the variant
# set with "Mr John Smith" → "Mr".
_HONORIFICS = frozenset({
    "mr", "mrs", "ms", "miss", "mx", "dr", "prof", "professor", "sir",
    "dame", "lord", "lady", "hon", "rev", "fr", "br", "sr",
    "eng", "ing", "arch", "capt", "maj", "col", "gen", "lt", "sgt",
    "cmdr", "adm", "ambassador", "amb", "hrh", "hm", "he", "rt",
    "the", "mister", "madam", "madame",
})


@dataclass
class NameComponents:
    """Parsed name components. Case-preserved for rendering; lower-case
    used for matching."""
    given: list[str] = field(default_factory=list)     # first + middle names
    particles: list[str] = field(default_factory=list)  # bin, al, van, de, …
    surname: str = ""                                   # last name (may be compound)
    honorifics: list[str] = field(default_factory=list)
    raw: str = ""

    @property
    def full_normalised(self) -> str:
        """'mohammed bin salman al saud' — lowercase, no honorifics."""
        parts = self.given + self.particles
        if self.surname:
            parts.append(self.surname)
        return " ".join(p.lower() for p in parts).strip()


def parse_components(name: str) -> NameComponents:
    """Parse a full name string into components.

    Handles:
      - Honorifics stripped from both ends
      - Particles (bin, al, van, de…) identified and kept in-order
      - Surname = everything after the last particle block
      - Multi-word given names kept intact
    """
    if not name or not name.strip():
        return NameComponents(raw="")
    raw = name.strip()
    # Normalise whitespace and dashes
    cleaned = re.sub(r"[,\s]+", " ", raw).strip()
    # Remove leading / trailing honorifics (case-insensitive, may be repeated)
    tokens = cleaned.split(" ")

    honorifics: list[str] = []
    # Strip leading honorifics
    while tokens and tokens[0].lower().rstrip(".") in _HONORIFICS:
        honorifics.append(tokens.pop(0))
    # Strip trailing honorifics ('... Jr.', '... PhD')
    _TRAILING_TITLES = {"jr", "sr", "ii", "iii", "iv", "phd", "md", "esq", "mba"}
    while tokens and tokens[-1].lower().rstrip(".") in _TRAILING_TITLES:
        honorifics.append(tokens.pop())

    if not tokens:
        return NameComponents(honorifics=honorifics, raw=raw)

    # Separate particles from given/surname by scanning from the end.
    # A particle is any token whose lower-case form is in _PARTICLES.
    # Everything AFTER the last particle (or the single last token if no
    # particle) is the surname; everything before the first particle is
    # given.
    particles: list[str] = []
    last_particle_idx: int | None = None
    for i, tok in enumerate(tokens):
        if tok.lower() in _PARTICLES:
            particles.append(tok)
            last_particle_idx = i

    if last_particle_idx is not None and last_particle_idx < len(tokens) - 1:
        surname = " ".join(tokens[last_particle_idx + 1 :])
        given = tokens[:last_particle_idx]
        # Remove any particles from given list
        given = [t for t in given if t.lower() not in _PARTICLES]
    else:
        # No particle block — classic first+middle+last
        given = tokens[:-1] if len(tokens) > 1 else []
        surname = tokens[-1] if len(tokens) >= 1 else ""
        particles = []

    return NameComponents(
        given=given,
        particles=particles,
        surname=surname,
        honorifics=honorifics,
        raw=raw,
    )


# =============================================================================
# TRANSLITERATION TABLES
# =============================================================================

# Cyrillic → Latin (Russian GOST 2006 subset + common alternates).
# Each entry is (cyrillic_char, [latin_variant_1, latin_variant_2, ...]).
# Multiple Latin forms are emitted as separate variants where the choice
# materially affects matching (e.g. "Shch/Sh/Sc" for Щ, "Yu/Ju/U" for Ю).
_CYRILLIC_TO_LATIN: dict[str, list[str]] = {
    "а": ["a"], "б": ["b"], "в": ["v"], "г": ["g"], "д": ["d"],
    "е": ["e", "ye"], "ё": ["e", "yo", "io"], "ж": ["zh", "j"],
    "з": ["z"], "и": ["i"], "й": ["y", "j", "i"], "к": ["k"],
    "л": ["l"], "м": ["m"], "н": ["n"], "о": ["o"], "п": ["p"],
    "р": ["r"], "с": ["s"], "т": ["t"], "у": ["u"], "ф": ["f"],
    "х": ["kh", "h"], "ц": ["ts", "c"], "ч": ["ch", "c"],
    "ш": ["sh"], "щ": ["shch", "sh"], "ъ": [""], "ы": ["y", "i"],
    "ь": [""], "э": ["e"], "ю": ["yu", "ju", "iu"], "я": ["ya", "ja", "ia"],
}

# Arabic → Latin (ALA-LC with common press variants).
_ARABIC_TO_LATIN: dict[str, list[str]] = {
    "ا": ["a"], "ب": ["b"], "ت": ["t"], "ث": ["th", "s"], "ج": ["j", "g"],
    "ح": ["h"], "خ": ["kh", "h"], "د": ["d"], "ذ": ["dh", "z", "d"],
    "ر": ["r"], "ز": ["z"], "س": ["s"], "ش": ["sh"], "ص": ["s"],
    "ض": ["d", "dh"], "ط": ["t"], "ظ": ["z", "dh"], "ع": ["a", ""],
    "غ": ["gh", "g"], "ف": ["f"], "ق": ["q", "k"], "ك": ["k"],
    "ل": ["l"], "م": ["m"], "ن": ["n"], "ه": ["h"], "و": ["w", "u", "o"],
    "ي": ["y", "i"], "ى": ["a"], "ء": [""], "ة": ["a", "h"],
}


def _is_cyrillic(s: str) -> bool:
    return any(0x0400 <= ord(c) <= 0x04FF for c in s)


def _is_arabic(s: str) -> bool:
    return any(0x0600 <= ord(c) <= 0x06FF for c in s)


def _transliterate(s: str, table: dict[str, list[str]]) -> list[str]:
    """Expand a source-script string into all Latin variants.

    Bounded: if the variant count would exceed 16, we take the first
    option for every subsequent multi-choice character rather than
    exploding combinatorially. A name with 4 ambiguous characters
    already gives 2^4=16 variants which is enough for sanctions fuzzy
    screening.
    """
    if not s:
        return [""]
    variants = [""]
    low = s.lower()
    for ch in low:
        options = table.get(ch) or [ch]
        if not variants:
            variants = list(options)
            continue
        if len(variants) * len(options) <= 16:
            new_v: list[str] = []
            for v in variants:
                for o in options:
                    new_v.append(v + o)
            variants = new_v
        else:
            # Cap hit — freeze existing variants with first option only
            first = options[0]
            variants = [v + first for v in variants]
    return variants


# =============================================================================
# SHORT FORMS & INITIALS
# =============================================================================

# Well-known short-form ⇄ full-form pairs. These are one-directional
# (full → shorts) because a short "Bill" could map to "William" or
# "Billy" or "Wilhelm" and disambiguation isn't our job here — we want
# recall, not precision.
_SHORT_FORMS: dict[str, list[str]] = {
    "william": ["will", "bill", "billy", "liam"],
    "robert": ["rob", "bob", "bobby"],
    "richard": ["rich", "rick", "dick", "ricky"],
    "michael": ["mike", "mick", "mickey"],
    "james": ["jim", "jimmy", "jamie"],
    "john": ["jack", "johnny", "jon"],
    "joseph": ["joe", "joey"],
    "thomas": ["tom", "tommy"],
    "charles": ["charlie", "chuck", "chaz"],
    "edward": ["ed", "eddie", "ted", "ned"],
    "anthony": ["tony", "ant"],
    "alexander": ["alex", "al", "sasha", "xander"],
    "aleksandr": ["alex", "sasha", "alexandr"],
    "andrei": ["andy", "andre", "andrey"],
    "dmitri": ["dima", "dmitry", "dimitri"],
    "vladimir": ["vlad", "volodymyr", "wladimir"],
    "sergei": ["sergey", "serge", "serghei"],
    "viktor": ["victor", "vitya"],
    "mikhail": ["misha", "michael", "mihail"],
    "ivan": ["vanya", "yvan"],
    "nikolai": ["kolya", "nicholai", "nikolay", "nicolas"],
    "ekaterina": ["katya", "katherine", "catherine"],
    "elizabeth": ["liz", "beth", "betty", "eliza"],
    "katherine": ["kate", "katie", "kathy"],
    "margaret": ["maggie", "meg", "peggy"],
    "patricia": ["pat", "patty", "trish"],
    "christopher": ["chris", "kit"],
    "mohammed": ["mohamed", "muhammad", "muhammed", "mohamad", "mohamet"],
    "muhammad": ["mohammed", "mohamed", "muhammed", "mohamad"],
    "ahmed": ["ahmad", "ahmet"],
    "hassan": ["hasan", "hussein", "hussain", "husain"],
    "youssef": ["yusuf", "joseph", "yousef", "yussuf"],
    "abd": ["abdel", "abdul", "abdou"],
    "abdel": ["abd", "abdul"],
    "abdul": ["abd", "abdel", "abdullah"],
    "abdullah": ["abdulla", "abdullahi"],
}


def _short_form_variants(first_name: str) -> list[str]:
    """Return short-form / alternate-spelling variants of a given name."""
    if not first_name:
        return []
    key = first_name.lower()
    seen: set[str] = set()
    out: list[str] = []
    # Forward: full → shorts
    for v in _SHORT_FORMS.get(key, []):
        if v not in seen:
            seen.add(v)
            out.append(v)
    # Reverse: if the name is itself a short form, add the full form
    for full, shorts in _SHORT_FORMS.items():
        if key in shorts and full not in seen:
            seen.add(full)
            out.append(full)
    return out


# =============================================================================
# CORE RESOLVER
# =============================================================================

@dataclass
class NameResolution:
    """Output of resolve()."""
    query: str                            # original input
    canonical: str                        # best single form (for display)
    components: NameComponents            # parsed components
    variants: list[str] = field(default_factory=list)  # all screening variants
    script: str = "latin"                 # latin | cyrillic | arabic | other
    transliteration_pairs: list[tuple[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "canonical": self.canonical,
            "variants": self.variants,
            "script": self.script,
            "components": {
                "given": self.components.given,
                "particles": self.components.particles,
                "surname": self.components.surname,
                "honorifics": self.components.honorifics,
            },
        }


def _strip_diacritics(s: str) -> str:
    """Remove combining diacritics (áéñ → aen). Used to generate an
    'ascii' search variant alongside the original spelling."""
    return "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )


def resolve(name: str, *, nationality_iso2: str | None = None, max_variants: int = 20) -> NameResolution:
    """Generate a screening-variant set for a person's name.

    Args:
        name: the subject's name as provided.
        nationality_iso2: optional hint used to bias transliteration
            (reserved for future use; currently no effect on the
            variant set).
        max_variants: cap on the number of variants returned. Orchestrator
            budget is ~8-12 sanctions API calls per subject.

    Returns:
        NameResolution with canonical form + ordered variant list.
    """
    if not name or not name.strip():
        return NameResolution(query=name, canonical="", components=NameComponents())

    raw = name.strip()
    script = "latin"
    if _is_cyrillic(raw):
        script = "cyrillic"
    elif _is_arabic(raw):
        script = "arabic"

    components = parse_components(raw)
    canonical = components.full_normalised.title() if components.full_normalised else raw

    variants_ordered: list[str] = []
    seen: set[str] = set()

    def add(v: str) -> None:
        if not v:
            return
        v_norm = re.sub(r"\s+", " ", v).strip()
        if not v_norm:
            return
        key = v_norm.lower()
        if key in seen:
            return
        seen.add(key)
        variants_ordered.append(v_norm)

    # 1) The raw input
    add(raw)
    # 2) The normalised full form
    if components.full_normalised:
        add(components.full_normalised)
    # 3) ASCII-folded form for diacritic-insensitive matching
    ascii_form = _strip_diacritics(raw)
    if ascii_form != raw:
        add(ascii_form)

    # 4) Transliteration expansion for non-Latin scripts
    if script == "cyrillic":
        for v in _transliterate(raw, _CYRILLIC_TO_LATIN):
            add(v)
    elif script == "arabic":
        for v in _transliterate(raw, _ARABIC_TO_LATIN):
            add(v)

    # 5) Component-order swap: "Surname, Given" and "Given Surname"
    if components.given and components.surname:
        first = components.given[0]
        add(f"{first} {components.surname}")
        add(f"{components.surname} {first}")
        add(f"{components.surname}, {first}")
        # Full form with particles
        if components.particles:
            add(" ".join(components.given + components.particles + [components.surname]))

    # 6) First-name short forms
    if components.given:
        first = components.given[0]
        for short in _short_form_variants(first):
            if components.surname:
                add(f"{short} {components.surname}")
            add(short)

    # 7) Initial + surname (J. Smith, J Smith)
    if components.given and components.surname:
        initials = ".".join(g[0] for g in components.given if g)
        if initials:
            add(f"{initials} {components.surname}")
            add(f"{initials}. {components.surname}")
            add(f"{initials.replace('.', '')} {components.surname}")

    # 8) Particle-dropped form (controversial but catches press misspellings)
    if components.particles and components.given and components.surname:
        add(f"{components.given[0]} {components.surname}")

    # 9) Cap at max_variants
    variants = variants_ordered[:max_variants]

    logger.debug(
        "person_resolver: '%s' → %d variants (script=%s, components=%s)",
        raw, len(variants), script, components.full_normalised,
    )
    return NameResolution(
        query=raw,
        canonical=canonical,
        components=components,
        variants=variants,
        script=script,
    )
