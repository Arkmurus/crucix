"""R-F639 — Treaty-eliminated / non-existent / treaty-prohibited weapons watchlist.

Why this module exists
──────────────────────
The 2026-05-17 ADSM/Saudi DD case had a line item "SS-20 rockets — 10,000"
on the procurement list. The SS-20 is the Soviet RSD-10 Pioneer
intermediate-range ballistic missile — all 654 missiles and 509
launchers were destroyed between 1988 and 1991 under the INF Treaty.
It is non-procurable. ARIA's existing ECCN classifier tags it as
"ML4 missile system" but does not catch the "this weapon was
ELIMINATED under treaty 35 years ago" insight.

That insight is what crystallises the verdict: a counterparty listing
an INF-eliminated weapon on a procurement list is either
inexperienced, deliberately seeding test items, or has copied from
unreliable source material. All three readings disqualify engagement.

This module is the deterministic detector for that class.

Three categories
────────────────
1. ELIMINATED — physically destroyed under treaty. Cannot be procured
   anywhere. (SS-20, Pershing II, BGM-109G Gryphon — all INF Treaty.)
2. CATEGORICALLY_BANNED — exists chemically/biologically but
   universally prohibited for all states. (CWC, BWC.)
3. TREATY_PROHIBITED_STATE_PARTY — exists, can be procured by
   non-parties, BUT state parties are prohibited from producing,
   stockpiling, transferring or using. (Ottawa anti-personnel mines,
   CCM cluster munitions, for the ~100+ state parties.)

DD usage
────────
- ELIMINATED match → automatic HARD STOP + reputational flag for
  the originator (they listed a non-existent weapon)
- CATEGORICALLY_BANNED match → automatic HARD STOP for ANY buyer
- TREATY_PROHIBITED_STATE_PARTY match → check buyer's state-party
  status; HARD STOP if buyer is state-party

Honesty discipline
──────────────────
Treaty status is dated. The INF Treaty is now collapsed (US withdrew
2019; Russia followed). New weapons like the 9M729 Novator and the
US Dark Eagle hypersonic are EXEMPT under the (now-defunct) treaty,
distinct from the SS-20 / Pershing II which were physically eliminated.
This module specifies which weapons were ELIMINATED (irreversible)
vs which were merely TREATY_LIMITED (could be re-produced post-
withdrawal). Citation matters.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .engine_wiring import wire_success, wire_failure


# ─────────────────────────────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────────────────────────────


class EliminationStatus:
    """Three discrete categories with distinct DD implications."""
    ELIMINATED = "eliminated"                                 # destroyed under treaty
    CATEGORICALLY_BANNED = "categorically_banned"             # universal ban (CWC, BWC)
    TREATY_PROHIBITED_STATE_PARTY = "treaty_prohibited_state_party"  # ban applies to parties


@dataclass(frozen=True)
class WatchlistEntry:
    canonical_name: str
    aliases: tuple[str, ...]
    elimination_status: str          # EliminationStatus enum value
    treaty: str                       # treaty / convention reference
    treaty_year: int                  # signed/effective year
    elimination_year: Optional[int]   # when destroyed (if applicable)
    notes: str = ""
    # For TREATY_PROHIBITED_STATE_PARTY entries, callers must check the
    # buyer's state-party status against the treaty signatory list.
    treaty_party_list_url: str = ""

    def is_eliminated(self) -> bool:
        return self.elimination_status == EliminationStatus.ELIMINATED

    def is_categorically_banned(self) -> bool:
        return self.elimination_status == EliminationStatus.CATEGORICALLY_BANNED


# ─────────────────────────────────────────────────────────────────────
# SEED — 30 treaty-eliminated / banned / prohibited entries
# ─────────────────────────────────────────────────────────────────────


_SEED: tuple[WatchlistEntry, ...] = (

    # ─── INF TREATY (1987) ELIMINATED ──────────────────────────────
    # All Soviet + US weapons in this category were physically
    # destroyed 1988-1991 under verified treaty inspection.
    # Eliminated forever — current procurement is non-existent.

    WatchlistEntry(
        canonical_name="SS-20 Saber (RSD-10 Pioneer)",
        aliases=("SS-20", "SS-20 Saber", "SS20", "RSD-10", "RSD-10 Pioneer",
                 "Pioneer IRBM", "SS-20 rockets", "SS-20 missile"),
        elimination_status=EliminationStatus.ELIMINATED,
        treaty="INF Treaty (Intermediate-Range Nuclear Forces Treaty)",
        treaty_year=1987,
        elimination_year=1991,
        notes=(
            "Soviet intermediate-range ballistic missile, range ~5,500km. "
            "654 missiles + 509 launchers destroyed 1988-1991 under "
            "verified US-USSR inspection. Cannot be procured. ONLY "
            "remaining examples are static-display exhibits at the "
            "Smithsonian National Air and Space Museum (Washington) "
            "and Central Museum of the Armed Forces (Moscow). "
            "Frequently confused in procurement lists with 'SA-20' "
            "(S-300PMU air-defence — a different system that DOES "
            "exist, see weapon_origin_catalogue)."
        ),
    ),
    WatchlistEntry(
        canonical_name="Pershing II",
        aliases=("Pershing II", "Pershing-II", "MGM-31C", "Pershing 2"),
        elimination_status=EliminationStatus.ELIMINATED,
        treaty="INF Treaty",
        treaty_year=1987,
        elimination_year=1991,
        notes=(
            "US intermediate-range ballistic missile, range ~1,800km. "
            "247 missiles destroyed under INF Treaty. Cannot be procured."
        ),
    ),
    WatchlistEntry(
        canonical_name="BGM-109G Gryphon",
        aliases=("BGM-109G", "Gryphon", "GLCM", "Ground-Launched Cruise Missile",
                 "Tomahawk GLCM"),
        elimination_status=EliminationStatus.ELIMINATED,
        treaty="INF Treaty",
        treaty_year=1987,
        elimination_year=1991,
        notes=(
            "US ground-launched cruise missile, range ~2,500km. 442 "
            "missiles destroyed under INF Treaty. The naval/air "
            "Tomahawk (BGM-109/RGM-109/UGM-109) variants were NOT "
            "subject to INF and remain in service."
        ),
    ),
    WatchlistEntry(
        canonical_name="SS-4 Sandal (R-12)",
        aliases=("SS-4", "SS-4 Sandal", "R-12", "R-12 Dvina"),
        elimination_status=EliminationStatus.ELIMINATED,
        treaty="INF Treaty",
        treaty_year=1987,
        elimination_year=1991,
        notes=(
            "Soviet medium-range ballistic missile. Famous for 1962 "
            "Cuban Missile Crisis deployment. Eliminated under INF."
        ),
    ),
    WatchlistEntry(
        canonical_name="SS-5 Skean (R-14)",
        aliases=("SS-5", "SS-5 Skean", "R-14", "R-14 Chusovaya"),
        elimination_status=EliminationStatus.ELIMINATED,
        treaty="INF Treaty",
        treaty_year=1987,
        elimination_year=1991,
    ),
    WatchlistEntry(
        canonical_name="SS-12 Scaleboard (TR-1 Temp)",
        aliases=("SS-12", "SS-12 Scaleboard", "TR-1 Temp", "SS-22"),
        elimination_status=EliminationStatus.ELIMINATED,
        treaty="INF Treaty",
        treaty_year=1987,
        elimination_year=1991,
        notes="Soviet short/medium-range ballistic missile.",
    ),
    WatchlistEntry(
        canonical_name="SS-23 Spider (OTR-23 Oka)",
        aliases=("SS-23", "SS-23 Spider", "OTR-23", "Oka"),
        elimination_status=EliminationStatus.ELIMINATED,
        treaty="INF Treaty",
        treaty_year=1987,
        elimination_year=1991,
        notes=(
            "Soviet eliminated 102 missiles to comply with INF even "
            "though the system's range was contested as just below "
            "treaty threshold."
        ),
    ),

    # ─── CWC 1993 — CATEGORICALLY BANNED ───────────────────────────
    # Chemical Weapons Convention prohibits production, stockpiling,
    # transfer or use of chemical weapons by ALL signatory states.
    # 193 state parties (effectively universal except DPRK, Egypt,
    # South Sudan, Israel-signed-not-ratified).

    WatchlistEntry(
        canonical_name="VX nerve agent",
        aliases=("VX", "VX nerve agent", "VX gas", "Methylphosphonothioic acid",
                 "V-series nerve agent"),
        elimination_status=EliminationStatus.CATEGORICALLY_BANNED,
        treaty="Chemical Weapons Convention (CWC)",
        treaty_year=1993,
        elimination_year=None,
        notes=(
            "Schedule 1 chemical weapon. Production, stockpiling, "
            "transfer prohibited under CWC. Used in 2017 assassination "
            "of Kim Jong-nam, attributed to DPRK. Listing on a "
            "procurement is criminal trafficking, not commerce."
        ),
        treaty_party_list_url="https://www.opcw.org/about-us/member-states",
    ),
    WatchlistEntry(
        canonical_name="Sarin (GB)",
        aliases=("Sarin", "GB", "Sarin gas", "Methylphosphonofluoridate"),
        elimination_status=EliminationStatus.CATEGORICALLY_BANNED,
        treaty="Chemical Weapons Convention (CWC)",
        treaty_year=1993,
        elimination_year=None,
        notes="Schedule 1 nerve agent. CWC universal ban.",
    ),
    WatchlistEntry(
        canonical_name="Mustard gas (HD / sulfur mustard)",
        aliases=("Mustard gas", "HD", "sulfur mustard", "yperite",
                 "S-mustard", "H agent"),
        elimination_status=EliminationStatus.CATEGORICALLY_BANNED,
        treaty="Chemical Weapons Convention (CWC)",
        treaty_year=1993,
        elimination_year=None,
        notes="Schedule 1 blister agent. CWC universal ban.",
    ),
    WatchlistEntry(
        canonical_name="Novichok agents",
        aliases=("Novichok", "A-230", "A-232", "A-234", "Novichok-5",
                 "Novichok-7"),
        elimination_status=EliminationStatus.CATEGORICALLY_BANNED,
        treaty="Chemical Weapons Convention (CWC)",
        treaty_year=1993,
        elimination_year=None,
        notes=(
            "Soviet/Russian-developed 4th-generation nerve agents. "
            "Added to CWC Schedule 1 in 2019 after the 2018 Skripal "
            "case (UK Salisbury). Universal ban."
        ),
    ),
    WatchlistEntry(
        canonical_name="Tabun (GA)",
        aliases=("Tabun", "GA"),
        elimination_status=EliminationStatus.CATEGORICALLY_BANNED,
        treaty="Chemical Weapons Convention (CWC)",
        treaty_year=1993,
        elimination_year=None,
    ),
    WatchlistEntry(
        canonical_name="Soman (GD)",
        aliases=("Soman", "GD"),
        elimination_status=EliminationStatus.CATEGORICALLY_BANNED,
        treaty="Chemical Weapons Convention (CWC)",
        treaty_year=1993,
        elimination_year=None,
    ),
    WatchlistEntry(
        canonical_name="Cyclosarin (GF)",
        aliases=("Cyclosarin", "GF"),
        elimination_status=EliminationStatus.CATEGORICALLY_BANNED,
        treaty="Chemical Weapons Convention (CWC)",
        treaty_year=1993,
        elimination_year=None,
    ),

    # ─── BWC 1972 — CATEGORICALLY BANNED ───────────────────────────
    # Biological Weapons Convention prohibits the development,
    # production, stockpiling and transfer of biological agents
    # for non-defensive purposes by all 184 state parties.

    WatchlistEntry(
        canonical_name="Weaponised anthrax",
        aliases=("Weaponised anthrax", "Bacillus anthracis (weaponised)",
                 "anthrax spore weapons"),
        elimination_status=EliminationStatus.CATEGORICALLY_BANNED,
        treaty="Biological Weapons Convention (BWC)",
        treaty_year=1972,
        elimination_year=None,
        notes=(
            "Schedule 1 biological agent (Australia Group). Production "
            "or transfer of weaponised B. anthracis prohibited under "
            "BWC universal ban."
        ),
    ),
    WatchlistEntry(
        canonical_name="Weaponised ricin",
        aliases=("Weaponised ricin", "ricin toxin (weaponised)"),
        elimination_status=EliminationStatus.CATEGORICALLY_BANNED,
        treaty="Biological Weapons Convention (BWC)",
        treaty_year=1972,
        elimination_year=None,
    ),
    WatchlistEntry(
        canonical_name="Weaponised smallpox / Variola major",
        aliases=("Weaponised smallpox", "Variola major weapon",
                 "smallpox bioweapon"),
        elimination_status=EliminationStatus.CATEGORICALLY_BANNED,
        treaty="Biological Weapons Convention (BWC) + WHO repository control",
        treaty_year=1972,
        elimination_year=None,
        notes=(
            "Wild smallpox declared eradicated by WHO 1980. Authorised "
            "stocks held only at CDC Atlanta + Vector Russia. Any other "
            "stock or weaponisation is BWC violation."
        ),
    ),
    WatchlistEntry(
        canonical_name="Botulinum toxin (weaponised)",
        aliases=("Botulinum toxin weapon", "weaponised botulinum",
                 "Clostridium botulinum (weaponised)"),
        elimination_status=EliminationStatus.CATEGORICALLY_BANNED,
        treaty="Biological Weapons Convention (BWC)",
        treaty_year=1972,
        elimination_year=None,
    ),

    # ─── OTTAWA TREATY 1997 — anti-personnel mines (victim-activated) ─
    # Prohibits production, stockpiling, transfer and use by the
    # ~165 state parties. NON-PARTIES (notably US, Russia, China,
    # India, Pakistan, Iran, North/South Korea, Saudi Arabia, UAE,
    # Singapore) are NOT bound. CRITICAL: COMMAND-DETONATED mines
    # (like M18A1 Claymore in standard config) are NOT prohibited.
    # Only VICTIM-ACTIVATED (pressure, tripwire, etc.) anti-personnel
    # mines fall under the ban.

    WatchlistEntry(
        canonical_name="PMN-1 / PMN-2 (Russian anti-personnel mine)",
        aliases=("PMN-1", "PMN-2", "PMN", "Russian AP mine PMN"),
        elimination_status=EliminationStatus.TREATY_PROHIBITED_STATE_PARTY,
        treaty="Ottawa Anti-Personnel Mine Ban Convention",
        treaty_year=1997,
        elimination_year=None,
        notes=(
            "Soviet/Russian victim-activated anti-personnel mine. "
            "Russia is NOT a state party to Ottawa — production continues. "
            "But state-party buyers (UK, France, Germany, EU, Canada, "
            "Australia, etc.) are PROHIBITED from acquiring. Saudi Arabia "
            "is NOT a state party (acceded but later raised concerns)."
        ),
        treaty_party_list_url="https://www.icbl.org/en-gb/the-treaty/treaty-status.aspx",
    ),
    WatchlistEntry(
        canonical_name="POMZ-2 (stake-mounted fragmentation mine)",
        aliases=("POMZ-2", "POMZ", "stake mine POMZ"),
        elimination_status=EliminationStatus.TREATY_PROHIBITED_STATE_PARTY,
        treaty="Ottawa Anti-Personnel Mine Ban Convention",
        treaty_year=1997,
        elimination_year=None,
        notes="Soviet/Russian tripwire-activated AP mine.",
    ),
    WatchlistEntry(
        canonical_name="PMD-6 (wooden box mine)",
        aliases=("PMD-6", "PMD-7"),
        elimination_status=EliminationStatus.TREATY_PROHIBITED_STATE_PARTY,
        treaty="Ottawa Anti-Personnel Mine Ban Convention",
        treaty_year=1997,
        elimination_year=None,
    ),
    WatchlistEntry(
        canonical_name="Type 72 (Chinese AP mine)",
        aliases=("Type 72", "Type-72", "Type 72A", "Chinese AP mine"),
        elimination_status=EliminationStatus.TREATY_PROHIBITED_STATE_PARTY,
        treaty="Ottawa Anti-Personnel Mine Ban Convention",
        treaty_year=1997,
        elimination_year=None,
        notes="China is NOT a state party. State-party buyers prohibited.",
    ),
    WatchlistEntry(
        canonical_name="Valmara 69 (Italian bounding mine)",
        aliases=("Valmara 69", "Valmara", "Italian bounding AP mine"),
        elimination_status=EliminationStatus.TREATY_PROHIBITED_STATE_PARTY,
        treaty="Ottawa Anti-Personnel Mine Ban Convention",
        treaty_year=1997,
        elimination_year=None,
        notes="Italy is state party — production ceased post-1999.",
    ),
    WatchlistEntry(
        canonical_name="VS-50 / VS-MK2 (Italian/Spanish AP mine)",
        aliases=("VS-50", "VS-MK2"),
        elimination_status=EliminationStatus.TREATY_PROHIBITED_STATE_PARTY,
        treaty="Ottawa Anti-Personnel Mine Ban Convention",
        treaty_year=1997,
        elimination_year=None,
    ),

    # ─── CCM 2008 — cluster munitions (for state parties) ──────────
    # Convention on Cluster Munitions prohibits cluster bombs and
    # submunitions for the ~110 state parties. Non-parties include
    # US, Russia, China, India, Pakistan, Israel, Iran, Saudi Arabia,
    # Brazil — but state-party buyers (UK, France, Germany, NATO
    # majority, Canada, Australia, Japan) are prohibited from
    # acquiring, transferring, stockpiling.

    WatchlistEntry(
        canonical_name="CBU-87 / CBU-87B (US cluster bomb)",
        aliases=("CBU-87", "CBU-87B", "BLU-97 dispenser"),
        elimination_status=EliminationStatus.TREATY_PROHIBITED_STATE_PARTY,
        treaty="Convention on Cluster Munitions (CCM)",
        treaty_year=2008,
        elimination_year=None,
        notes=(
            "US cluster bomb (US is NOT party to CCM). Contains "
            "BLU-97 submunitions. State-party buyers prohibited."
        ),
        treaty_party_list_url="https://www.clusterconvention.org/states-parties/",
    ),
    WatchlistEntry(
        canonical_name="CBU-97 Sensor Fuzed Weapon (SFW)",
        aliases=("CBU-97", "SFW", "Sensor Fuzed Weapon", "BLU-108"),
        elimination_status=EliminationStatus.TREATY_PROHIBITED_STATE_PARTY,
        treaty="Convention on Cluster Munitions (CCM)",
        treaty_year=2008,
        elimination_year=None,
        notes="US air-dropped anti-armour cluster munition.",
    ),
    WatchlistEntry(
        canonical_name="M483A1 / M864 155mm DPICM",
        aliases=("M483A1", "M864", "DPICM 155mm",
                 "Dual-Purpose Improved Conventional Munition",
                 "155mm cluster shell"),
        elimination_status=EliminationStatus.TREATY_PROHIBITED_STATE_PARTY,
        treaty="Convention on Cluster Munitions (CCM)",
        treaty_year=2008,
        elimination_year=None,
        notes=(
            "155mm artillery shell carrying dual-purpose improved "
            "conventional submunitions. Common in legacy NATO stocks "
            "but state-party transfers/use prohibited."
        ),
    ),
    WatchlistEntry(
        canonical_name="M261 MPSM (Multi-Purpose Submunition)",
        aliases=("M261 MPSM", "M261", "Hydra-70 cluster warhead"),
        elimination_status=EliminationStatus.TREATY_PROHIBITED_STATE_PARTY,
        treaty="Convention on Cluster Munitions (CCM)",
        treaty_year=2008,
        elimination_year=None,
        notes="70mm rocket-delivered submunition warhead.",
    ),
)


# ─────────────────────────────────────────────────────────────────────
# Indices
# ─────────────────────────────────────────────────────────────────────

_BY_ALIAS: dict[str, WatchlistEntry] = {}
for _ws in _SEED:
    _BY_ALIAS[_ws.canonical_name.lower()] = _ws
    for _a in _ws.aliases:
        _BY_ALIAS[_a.lower()] = _ws


# ─────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────


def lookup_by_name(name: str) -> Optional[WatchlistEntry]:
    """Exact-or-alias lookup. None for clean weapons."""
    if not name or not isinstance(name, str):
        return None
    result = _BY_ALIAS.get(name.strip().lower())
    # R-F1046 — wire to brain on both paths so ARIA learns from watchlist hits
    if result is not None:
        wire_success(
            module="eliminated_weapons_watchlist",
            summary=f"Watchlist hit: {result.canonical_name} matched alias '{name[:80]}'",
            entity_name=result.canonical_name,
            source_id="eliminated_weapons_watchlist:lookup_by_name",
        )
    return result


def best_match_for_text(text: str) -> Optional[WatchlistEntry]:
    """Fuzzy match against goods-list line text. Returns the watchlist
    entry whose canonical/alias name appears as a word-boundary match."""
    if not text or not isinstance(text, str):
        return None
    text_lc = text.lower()
    best: tuple[int, Optional[WatchlistEntry]] = (0, None)
    for key, entry in _BY_ALIAS.items():
        if len(key) < 3:
            continue
        if re.search(rf"\b{re.escape(key)}\b", text_lc):
            if len(key) > best[0]:
                best = (len(key), entry)
    return best[1]


def is_eliminated(name: str) -> bool:
    """Convenience: True iff the weapon is in ELIMINATED status."""
    entry = lookup_by_name(name)
    return entry is not None and entry.is_eliminated()


def is_categorically_banned(name: str) -> bool:
    """Convenience: True iff CWC/BWC universally banned."""
    entry = lookup_by_name(name)
    return entry is not None and entry.is_categorically_banned()


def list_by_status(status: str) -> list[WatchlistEntry]:
    """All entries at a given status."""
    if not status:
        return []
    s = status.lower().strip()
    return [e for e in _SEED if e.elimination_status == s]


def list_by_treaty(treaty_keyword: str) -> list[WatchlistEntry]:
    """All entries citing a treaty matching `treaty_keyword` (case-insensitive)."""
    if not treaty_keyword:
        return []
    kw = treaty_keyword.lower().strip()
    return [e for e in _SEED if kw in e.treaty.lower()]


def all_entries() -> tuple[WatchlistEntry, ...]:
    """Full seed (immutable)."""
    return _SEED


def stats() -> dict:
    """Watchlist coverage metrics."""
    return {
        "total_entries": len(_SEED),
        "eliminated": len(list_by_status(EliminationStatus.ELIMINATED)),
        "categorically_banned": len(list_by_status(EliminationStatus.CATEGORICALLY_BANNED)),
        "treaty_prohibited_state_party": len(list_by_status(
            EliminationStatus.TREATY_PROHIBITED_STATE_PARTY
        )),
        "treaties_referenced": len({e.treaty for e in _SEED}),
    }


def render_finding_for_text(text: str) -> Optional[dict]:
    """Convenience: match text against watchlist + return a Finding-shaped
    dict for the DD orchestrator's compliance section."""
    entry = best_match_for_text(text)
    if entry is None:
        return None
    severity_map = {
        EliminationStatus.ELIMINATED: "hard_stop",
        EliminationStatus.CATEGORICALLY_BANNED: "hard_stop",
        EliminationStatus.TREATY_PROHIBITED_STATE_PARTY: "amber",
    }
    title_prefix = {
        EliminationStatus.ELIMINATED: "ELIMINATED WEAPON",
        EliminationStatus.CATEGORICALLY_BANNED: "TREATY-BANNED WEAPON",
        EliminationStatus.TREATY_PROHIBITED_STATE_PARTY: "TREATY-PROHIBITED (state-party)",
    }

    # R-F1046 — wire to brain on match
    wire_success(
        module="eliminated_weapons_watchlist",
        summary=f"Eliminated weapon match: {entry.canonical_name}",
        entity_name=entry.canonical_name,
        source_id="eliminated_weapons_watchlist:render_finding_for_text",
    )
    return {
        "severity": severity_map.get(entry.elimination_status, "amber"),
        "title": (
            f"{title_prefix.get(entry.elimination_status, '')}: "
            f"{entry.canonical_name} ({entry.treaty})"
        ),
        "detail": (
            f"Matched line-item text to {entry.canonical_name}. "
            f"Treaty: {entry.treaty} ({entry.treaty_year}). "
            + (f"Eliminated {entry.elimination_year}. "
               if entry.elimination_year else "")
            + f"Status: {entry.elimination_status}. {entry.notes}"
        ),
        "source": (
            f"eliminated_weapons_watchlist.lookup (R-F639) — "
            f"treaty: {entry.treaty}"
        ),
        "confidence": "CONFIRMED",  # treaty status is authoritative
        "elimination_status": entry.elimination_status,
        "treaty": entry.treaty,
        "matched_text": text[:200],
    }
