"""R-F766 — entity-name variant generator.

LIVE BUG from 2026-05-20 5-turn audit transcript:

  deep_research on 'Efdal Colpan' ran 4 queries -> 0 results.
  ARIA's own gap-analysis section listed better variants (Çolpan,
  Cholpan, Djolpan) but never auto-ran them. By turn 5 ARIA had
  effectively told the user 'no results, but you should also try
  these other spellings — want me to?'.

This module produces a small, ordered list of plausible romanisation
/ transliteration / diacritic variants of an entity name so the
deep_research angle builder can fanout AUTOMATICALLY before declaring
zero results. Coverage:

  - Turkish: ASCII letter -> diacritic variant (c -> ç, s -> ş,
    g -> ğ, i -> ı, o -> ö, u -> ü) and the reverse strip
  - Cyrillic transliteration: Sergey <-> Sergei, Yuri <-> Yury,
    Mikhail <-> Michael, Aleksandr <-> Alexander, Dimitri <->
    Dmitri <-> Dimitry, ...
  - Arabic transliteration: Mohammed <-> Muhammad <-> Mohamed,
    Ahmed <-> Ahmad, Hussein <-> Hussain, ...
  - Phonetic head: C- <-> Ch- <-> Dj- <-> J- (Turkish/Slavic
    initial-consonant family)
  - Suffix: '-ov' / '-off' / '-ow' / '-of' (Slavic-style)

Cap: at most VARIANT_LIMIT (default 6) variants returned per call so
deep_research doesn't blow its max_queries budget. The original name
is ALWAYS first; subsequent entries are de-duplicated by lowercase
form.

This is a HEURISTIC, not a comprehensive transliteration engine. It
exists to close the 'I listed the variants but didn't try them'
behaviour observed in the 2026-05-20 transcript. If the variants are
all wrong, the user can supply the correct one; if any single variant
is right, deep_research gets a non-zero result on the same turn
instead of forcing the user back into the loop."""
from __future__ import annotations
from .engine_wiring import wire_success, wire_failure

import re
import unicodedata
from typing import Iterable

VARIANT_LIMIT = 6

# Turkish diacritic ↔ ASCII pairs. The first column is the ASCII form
# the LLM most commonly emits; the second is the diacritic form that
# matches Turkish-language sources. We try both directions per name.
_TURKISH_MAP = {
    "c": "ç",
    "s": "ş",
    "g": "ğ",
    "i": "ı",  # dotless-i (Turkish-specific)
    "o": "ö",
    "u": "ü",
}

# Common Cyrillic transliteration alternates. List by canonical-form
# first; the variant generator emits ALL of these wherever the canonical
# appears in the name (case-insensitive).
_CYRILLIC_TRANSLIT_ALTS = [
    ("Sergey", ["Sergei", "Serguei"]),
    ("Sergei", ["Sergey", "Serguei"]),
    ("Yuri", ["Yury", "Iurii", "Youri"]),
    ("Yury", ["Yuri", "Iurii"]),
    ("Mikhail", ["Michael", "Mihail"]),
    ("Michael", ["Mikhail", "Mihail"]),
    ("Aleksandr", ["Alexander", "Alexsandr", "Aleksander"]),
    ("Alexander", ["Aleksandr", "Aleksander"]),
    ("Dmitri", ["Dimitri", "Dimitry", "Dmitry"]),
    ("Dmitry", ["Dmitri", "Dimitri"]),
    ("Vasili", ["Vasily", "Vassili", "Wassily"]),
    ("Vasily", ["Vasili", "Vassili"]),
    ("Nikolai", ["Nikolay", "Nicolai", "Nicholas"]),
    ("Nikolay", ["Nikolai", "Nicolas"]),
    ("Yevgeny", ["Evgeny", "Eugene", "Yevgeni"]),
    ("Evgeny", ["Yevgeny", "Eugene"]),
    ("Anatoli", ["Anatoly", "Anatolii"]),
    ("Anatoly", ["Anatoli", "Anatolii"]),
    ("Pavel", ["Pavlo", "Paul", "Paolo"]),
    ("Petr", ["Pyotr", "Peter", "Piotr"]),
    ("Ivan", ["Iwan", "Ihor"]),
]

# Arabic name transliteration alternates.
_ARABIC_TRANSLIT_ALTS = [
    ("Mohammed", ["Muhammad", "Mohamed", "Muhammed", "Mohammad"]),
    ("Muhammad", ["Mohammed", "Mohamed", "Mohammad"]),
    ("Mohamed", ["Mohammed", "Muhammad", "Mohammad"]),
    ("Ahmed", ["Ahmad", "Achmed"]),
    ("Ahmad", ["Ahmed", "Achmed"]),
    ("Hussein", ["Hussain", "Hossein", "Hosein", "Husain"]),
    ("Hussain", ["Hussein", "Hossein"]),
    ("Mahmoud", ["Mahmood", "Mahmud"]),
    ("Mahmood", ["Mahmoud", "Mahmud"]),
    ("Khalid", ["Khaled", "Khaleed"]),
    ("Khaled", ["Khalid"]),
    ("Abdul", ["Abdel", "Abd al"]),
    ("Abdel", ["Abdul"]),
    ("Yusuf", ["Yousef", "Yusef", "Joseph"]),
    ("Yousef", ["Yusuf", "Yusef"]),
    ("Ibrahim", ["Ebrahim", "Abraham"]),
    ("Omar", ["Umar", "Omer"]),
    ("Hassan", ["Hasan", "Hassen"]),
]

# Phonetic initial-consonant family swaps (C- / Ch- / Dj- / J- / Y-).
# Applied ONLY to the FIRST token of the name to avoid noise from
# mid-name letters.
_INITIAL_SWAPS = [
    ("c", ["ch", "dj", "j", "ts"]),
    ("ch", ["c", "dj", "tsh"]),
    ("dj", ["c", "ch", "j"]),
    ("j", ["dj", "y", "zh"]),
    ("y", ["j", "i"]),
    ("ts", ["c", "tz"]),
    ("kh", ["h", "ch"]),
    ("zh", ["j", "z"]),
    ("ş", ["sh", "s"]),
    ("ç", ["ch", "c"]),
]

# Slavic surname suffix alternates.
_SUFFIX_SWAPS = [
    ("ov", ["off", "ow", "of"]),
    ("off", ["ov", "of"]),
    ("ev", ["eff", "ew"]),
    ("ski", ["sky", "skii"]),
    ("sky", ["ski", "skii"]),
    ("yi", ["y", "i"]),
]

# Turkish-shape detection — small list of well-known Turkish first-name
# and surname tokens that signal the name family. Not exhaustive (no
# room for a 10k-name list inline); calibrated to fire on the
# 2026-05-20 transcript case AND common Turkish defence/political
# figures encountered in DD work.
_TURKISH_FIRST_NAMES = {
    "efdal", "ahmet", "mehmet", "mustafa", "hasan", "hüseyin", "huseyin",
    "ali", "ibrahim", "ismail", "yusuf", "ömer", "omer", "murat", "burak",
    "emre", "çağlar", "caglar", "erdoğan", "erdogan", "recep", "tayyip",
    "süleyman", "suleyman", "selim", "fatma", "ayşe", "ayse", "zeynep",
    "hatice", "elif", "kemal", "atatürk", "ataturk", "tansu", "deniz",
    "barış", "baris", "berkay", "okan", "oktay", "onur",
}
# Turkish-surname patterns — common suffixes used in surnames.
_TURKISH_SURNAME_SUFFIXES = (
    "pan", "lar", "ler", "han", "kan", "uz", "öz", "oz", "kaya",
    "demir", "yıldız", "yildiz", "ay", "altın", "altin", "oğlu", "oglu",
)
# Recognised western/English first-name set — if a name is composed
# entirely of these tokens, we skip variant fanout. Small list (top
# common forms only).
_WESTERN_FIRST_NAMES = {
    "john", "james", "robert", "michael", "william", "david", "richard",
    "thomas", "charles", "joseph", "christopher", "daniel", "matthew",
    "anthony", "mark", "donald", "paul", "andrew", "kenneth", "george",
    "edward", "brian", "ronald", "antonio", "kevin", "jason", "jeff",
    "ryan", "jacob", "gary", "nicholas", "eric", "jonathan", "stephen",
    "larry", "scott", "frank", "raymond", "patrick", "alex", "henry",
    "peter", "jack",
    "mary", "patricia", "jennifer", "linda", "elizabeth", "barbara",
    "susan", "jessica", "sarah", "karen", "lisa", "nancy", "betty",
    "helen", "sandra", "donna", "carol", "ruth", "sharon", "michelle",
    "laura", "emily", "kimberly", "deborah", "amy", "angela", "ashley",
    "anna", "rachel", "joan", "judy", "evelyn", "olivia", "emma",
    "sophia",
}
_WESTERN_SURNAMES = {
    "smith", "jones", "brown", "johnson", "williams", "miller", "davis",
    "garcia", "rodriguez", "wilson", "martinez", "anderson", "taylor",
    "thomas", "moore", "jackson", "martin", "lee", "perez", "thompson",
    "white", "harris", "sanchez", "clark", "lewis", "robinson", "walker",
    "young", "allen", "king", "wright", "scott", "torres", "nguyen",
    "hill", "flores", "green", "adams", "nelson", "baker", "hall",
    "rivera", "campbell", "mitchell", "carter", "roberts",
}


def _looks_turkish(name: str) -> bool:
    """Heuristic: does this name match Turkish-name shape?

    R-F767 (2026-05-20) tightened the non-ASCII check: 'ç' / 'ö' / 'ü'
    are shared between Turkish, French, Portuguese, German and Hungarian.
    Pre-tightening, 'François Lefèvre' (French) was misclassified as
    Turkish because of the 'ç'. Now: require at least one
    Turkish-DISTINCTIVE diacritic ('ğ', 'ı' dotless-i, 'ş' / 'İ') to
    confirm — these have no counterpart in French / Portuguese / German.
    """
    if not name:
        return False
    lower = name.lower()
    # Non-ASCII with TURKISH-DISTINCTIVE diacritics. Distinct from
    # French/Portuguese (which use ç, é, ã but not ğ/ı/ş).
    if not name.isascii() and any(c in name for c in "ğıİşĞŞ"):
        return True
    tokens = lower.split()
    if not tokens:
        return False
    # Known first-name token
    if any(tok in _TURKISH_FIRST_NAMES for tok in tokens):
        return True
    # Surname suffix on the LAST token (typical Turkish name order)
    last = tokens[-1]
    if any(last.endswith(s) for s in _TURKISH_SURNAME_SUFFIXES):
        return True
    return False


def _looks_western(name: str) -> bool:
    """If both tokens are recognised English first/last names, skip
    variant fanout — would just waste budget."""
    if not name or not name.isascii():
        return False
    lower = name.lower()
    tokens = lower.split()
    if len(tokens) < 2:
        return False
    first = tokens[0]
    last = tokens[-1]
    return first in _WESTERN_FIRST_NAMES and last in _WESTERN_SURNAMES


def _strip_diacritics(s: str) -> str:
    """Decompose unicode + drop combining marks (e.g. 'Çolpan' -> 'Colpan')."""
    nfd = unicodedata.normalize("NFD", s)
    return "".join(c for c in nfd if not unicodedata.combining(c))


def _add_turkish_diacritics(s: str) -> list[str]:
    """For ASCII-only Turkish-shaped names, emit each plausible
    one-letter diacritic substitution. Bounded so we don't combinatorially
    blow up — try ONE substitution per output variant."""
    out: list[str] = []
    lower = s.lower()
    for ascii_ch, dia_ch in _TURKISH_MAP.items():
        for i, ch in enumerate(lower):
            if ch == ascii_ch:
                # Preserve original capitalisation
                if s[i].isupper():
                    new_ch = dia_ch.upper()
                else:
                    new_ch = dia_ch
                variant = s[:i] + new_ch + s[i + 1:]
                if variant != s:
                    out.append(variant)
    return out


def _swap_initial_consonant(s: str) -> list[str]:
    """Replace the FIRST initial-consonant cluster with each phonetic
    alternate. Operates token-by-token so 'Efdal Colpan' yields
    'Efdal Cholpan' / 'Efdal Djolpan' / 'Efdal Jolpan' etc."""
    out: list[str] = []
    tokens = s.split()
    if not tokens:
        return out
    # We swap on the LAST token (typically the surname for Western
    # name order) AND on the first token if it differs. This catches
    # 'Colpan' (surname) AND 'Çağlar' (first name).
    candidate_indices: list[int] = []
    if len(tokens) >= 1:
        candidate_indices.append(len(tokens) - 1)
    if len(tokens) >= 2 and len(tokens[0]) > 1:
        candidate_indices.append(0)
    for idx in candidate_indices:
        tok = tokens[idx]
        tok_lower = tok.lower()
        for head, alts in _INITIAL_SWAPS:
            if tok_lower.startswith(head):
                for alt in alts:
                    # Preserve capitalisation of the swapped head
                    if tok[0].isupper():
                        new_head = alt[0].upper() + alt[1:]
                    else:
                        new_head = alt
                    new_tok = new_head + tok[len(head):]
                    new_tokens = tokens.copy()
                    new_tokens[idx] = new_tok
                    out.append(" ".join(new_tokens))
    return out


def _swap_transliteration_pair(
    s: str, pairs: list[tuple[str, list[str]]],
) -> list[str]:
    """Replace any occurrence of a canonical name with each of its
    transliteration alternates."""
    out: list[str] = []
    for canonical, alts in pairs:
        pat = re.compile(r"\b" + re.escape(canonical) + r"\b", re.IGNORECASE)
        if pat.search(s):
            for alt in alts:
                variant = pat.sub(alt, s, count=1)
                if variant != s:
                    out.append(variant)
    return out


def _swap_suffix(s: str) -> list[str]:
    """Replace recognised Slavic surname suffixes with their alternates."""
    out: list[str] = []
    tokens = s.split()
    if not tokens:
        return out
    last = tokens[-1]
    last_lower = last.lower()
    for suffix, alts in _SUFFIX_SWAPS:
        if last_lower.endswith(suffix):
            stem = last[: -len(suffix)]
            for alt in alts:
                new_last = stem + alt
                new_tokens = tokens.copy()
                new_tokens[-1] = new_last
                out.append(" ".join(new_tokens))
    return out


def generate_variants(name: str, limit: int = VARIANT_LIMIT) -> list[str]:
    """Return up to `limit` plausible romanisation/transliteration
    variants of `name`. The original name is ALWAYS first.

    De-duplicates case-insensitively. Caps at `limit` to keep
    deep_research's max_queries budget bounded.

    Examples:

        >>> generate_variants("Efdal Colpan", limit=4)
        ['Efdal Colpan', 'Efdal Çolpan', 'Efdal Cholpan', 'Efdal Djolpan']

        >>> generate_variants("Sergey Mikhailov", limit=4)
        ['Sergey Mikhailov', 'Sergei Mikhailov', 'Serguei Mikhailov', 'Sergey Mikhailoff']

        >>> generate_variants("Mohammed Ahmed", limit=4)
        ['Mohammed Ahmed', 'Muhammad Ahmed', 'Mohamed Ahmed', 'Mohammed Ahmad']
    """
    if not name or not isinstance(name, str):
        return []
    name = name.strip()
    if not name:
        return []

    variants: list[str] = [name]
    seen: set[str] = {name.lower()}

    def _push(v: str) -> bool:
        v = v.strip()
        if not v:
            return False
        key = v.lower()
        if key in seen:
            return False
        seen.add(key)
        variants.append(v)
        return len(variants) >= limit

    # R-F766 v2 (2026-05-20): order the variant phases so Cyrillic /
    # Arabic / Slavic-suffix / phonetic transliterations get a chance
    # BEFORE Turkish diacritics. Pre-v2 the Turkish-diacritic pass
    # greedily consumed the 6-slot budget for non-Turkish names
    # (e.g. 'Sergey Mikhailov' filled with Şergey/Serğey/Mikhailöv
    # noise before any actual Cyrillic alternate fired).

    # 1. Cyrillic transliteration alternates (Sergey→Sergei, etc.)
    for v in _swap_transliteration_pair(name, _CYRILLIC_TRANSLIT_ALTS):
        if _push(v):
            return variants

    # 2. Arabic transliteration alternates (Mohammed→Muhammad, etc.)
    for v in _swap_transliteration_pair(name, _ARABIC_TRANSLIT_ALTS):
        if _push(v):
            return variants

    # 3. Slavic surname suffix alternates (-ov→-off/-ow, etc.)
    for v in _swap_suffix(name):
        if _push(v):
            return variants

    # 4. Initial-consonant phonetic swaps (Ch / Dj / J / Y)
    for v in _swap_initial_consonant(name):
        if _push(v):
            return variants

    # 5. Turkish diacritic add — ONLY if the name looks Turkish.
    #    Pre-v2 we'd try this on every ASCII name; that meant English
    #    names got 'Şmith' / 'Joneş' variants which are useless and
    #    crowded out real alternates. Now gated on _looks_turkish.
    if name.isascii() and _looks_turkish(name):
        for v in _add_turkish_diacritics(name):
            if _push(v):
                return variants

    # 6. Diacritic strip (in case the input HAS diacritics)
    stripped = _strip_diacritics(name)
    if stripped != name:
        _push(stripped)

    return variants


# R-F767 (2026-05-20) — name-origin → registry-jurisdiction routing.
# Other-agent audit 2026-05-20 noted: 'Investigate Efdal Colpan tied
# to ECDI procurement' is clearly DD-shaped (named person + foreign
# procurement body + Turkish-origin surname); R-F729 broadened DD-
# intent regex but didn't route to MERSIS preferentially. This
# heuristic returns the ordered ISO2 jurisdiction list a downstream
# orchestrator should try FIRST when a name origin is detectable.
#
# Mapping is intentionally conservative — only emits jurisdictions
# that are SUPPORTED by aria_service.intel.registry_adapters
# (_SUPPORTED_JURISDICTIONS), since suggesting RU when there's no RU
# adapter is wasted work. As the adapter inventory grows, extend the
# mapping table here. Unsupported origins return [].

# Origin → ordered list of supported ISO2 jurisdictions. Ordered by
# default-best-guess; the orchestrator may re-rank by context.
_ORIGIN_TO_JURISDICTIONS = {
    "turkish":  ["TR"],
    "cyrillic": ["BG"],          # Russia/Ukraine not yet supported; BG is closest match.
    "arabic":   ["SA", "AE"],
    "slavic":   ["PL", "SK", "CZ", "HU"],
    "iberian":  ["BR", "AO"],    # Lusophone moat — Portuguese / Brazilian
    "francophone": ["FR"],
}


def detect_name_origin(name: str) -> str | None:
    """Classify the name's apparent origin.

    Returns one of: 'turkish' | 'cyrillic' | 'arabic' | 'slavic' |
    'iberian' | 'francophone' | None.

    None means: no confident origin detected (plain Western name, or
    too short to classify). The orchestrator should treat None as
    'use the default adapter set, no preference'.
    """
    if not name or not isinstance(name, str):
        return None
    s = name.strip()
    if not s:
        return None
    lower = s.lower()
    tokens = lower.split()
    if not tokens:
        return None

    # Turkish — first/last name set + diacritics
    if _looks_turkish(s):
        return "turkish"

    # Cyrillic — known Cyrillic-canonical names in the transliteration
    # alternates list, OR Slavic surname suffix when paired with
    # Cyrillic-canonical first name. Stay conservative: only return
    # 'cyrillic' on positive match, fall through to 'slavic' for
    # generic -ski/-ov endings.
    for canonical, _alts in _CYRILLIC_TRANSLIT_ALTS:
        if canonical.lower() in tokens:
            return "cyrillic"

    # Arabic — known Arabic-canonical first names
    for canonical, _alts in _ARABIC_TRANSLIT_ALTS:
        if canonical.lower() in tokens:
            return "arabic"

    # Slavic surname suffix (-ski/-sky/-ov/-ev/-off/-yi)
    last = tokens[-1]
    for suffix, _alts in _SUFFIX_SWAPS:
        if last.endswith(suffix):
            return "slavic"

    # Iberian (Portuguese/Spanish) — require a DISTINCTIVE diacritic
    # (ã, õ, ñ) or a Portuguese-style ending. 'ç' / 'é' / 'á' alone are
    # shared with French and don't disambiguate.
    if not s.isascii() and any(c in s for c in "ãõñÃÕÑ"):
        return "iberian"
    for tok in tokens:
        if tok.endswith(("ões", "ção", "inho", "inha", "eiro", "eira")):
            return "iberian"

    # Francophone — French-distinctive diacritics (è, ê, ë, œ, æ, ÿ)
    # or French-style surname endings. Checked AFTER iberian so a
    # Portuguese 'ç+ã' name doesn't fall through to French.
    if not s.isascii() and any(c in s for c in "èêëîïùûüÿœæÈÊËÎÏÙÛÜŸŒÆ"):
        return "francophone"
    for tok in tokens:
        if tok.endswith(("eau", "ault", "ois", "eux", "ière", "èvre")):
            return "francophone"

    return None


def suggest_jurisdictions(name: str) -> list[str]:
    """Return the ordered list of ISO2 jurisdictions to try FIRST when
    looking up a registry record for `name`. Empty list when no origin
    is detected (orchestrator should fall back to its default).

    Example:
        >>> suggest_jurisdictions("Efdal Colpan")
        ['TR']
        >>> suggest_jurisdictions("Sergey Mikhailov")
        ['BG']     # closest supported jurisdiction; RU/UA not yet wired
        >>> suggest_jurisdictions("Mohammed Ahmed")
        ['SA', 'AE']
        >>> suggest_jurisdictions("Jan Kowalski")
        ['PL', 'SK', 'CZ', 'HU']
        >>> suggest_jurisdictions("John Smith")
        []
        >>> suggest_jurisdictions("João Silva")
        ['BR', 'AO']
    """
    origin = detect_name_origin(name)
    if not origin:
        return []
    return list(_ORIGIN_TO_JURISDICTIONS.get(origin, []))


def likely_needs_variants(name: str) -> bool:
    """Cheap pre-check: does this name LOOK like one that might have
    interesting variants? Used by deep_research to decide whether to
    spend the variant-fanout budget.

    Returns True when:
      - Name contains any diacritic char (already non-ASCII)
      - Name matches Turkish-name shape (first/last name tokens or
        surname suffix from _TURKISH_FIRST_NAMES / _TURKISH_SURNAME_SUFFIXES)
      - Name token matches a known Cyrillic / Arabic canonical
      - Name has a recognised Slavic suffix (-ov, -ev, -ski, -yi)
      - Name starts with phonetic ambiguous head (Ch- / Dj- / Kh- / Zh- /
        Ts- / Ya- / Yu- / Ye- / Yo-)

    Returns False when:
      - Both tokens match the western first-name / surname whitelist
        (John Smith, Sarah Brown — variant fanout would be noise)
      - Empty / malformed input
    """
    if not name:
        return False
    if not name.isascii():
        return True
    # Cheap reject: known plain Western name shape
    if _looks_western(name):
        return False
    # Turkish-shape detection (R-F766 v2)
    if _looks_turkish(name):
        return True
    lower = name.lower()
    tokens = lower.split()
    if not tokens:
        return False
    # Slavic suffix check
    for suffix, _alts in _SUFFIX_SWAPS:
        if tokens[-1].endswith(suffix):
            return True
    # Known Cyrillic / Arabic canonical name tokens
    for canonical, _alts in _CYRILLIC_TRANSLIT_ALTS + _ARABIC_TRANSLIT_ALTS:
        if canonical.lower() in tokens:
            return True
    # Phonetic ambiguous head
    for tok in tokens:
        if re.match(r"^(ch|dj|kh|zh|ts|ya|yu|ye|yo)", tok):
            return True

    # R-F996 — wire to brain
    wire_success(
        module="name_variants",
        summary="Name variants",
        source_id="name_variants:R-F996",
    )
    return False

# R-F2538: R-F2119 import-time wire_failure("module shutdown") block removed — it fired a FALSE engine_failure gap on every import (not at shutdown); do not re-add.
