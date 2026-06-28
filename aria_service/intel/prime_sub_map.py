"""Defence prime → sub-contractor ecosystem map.

Static knowledge table of the major defence primes and their known
sub-contractor networks, indexed so an intel alert mentioning a prime
+ programme can immediately surface credible Arkmurus-brokerage angles.

Scope
─────
Covers the 15 primes Arkmurus most frequently encounters in FMS
pipeline work. For each prime we capture:
  - headquarters jurisdiction (affects ITAR / EAR applicability)
  - 3-8 flagship product families
  - per-product-family: typical sub-tier suppliers, Arkmurus-access
    rating (HIGH / MEDIUM / LOW / NONE), and the normal entry
    pathway (offset, MRO, sustainment, consumable resupply, etc.)

Design choices
──────────────
- Data is code-embedded (not YAML/JSON) so every lookup is offline,
  instant, and versioned by commit. Public-record facts only —
  no proprietary intelligence in the map itself.
- Each sub-contractor gets a short ARKMURUS_FIT note explaining the
  credible route. "HIGH" means brokerage intermediaries are
  normal in that supply chain; "NONE" means the product is
  ITAR-prime-direct and a broker has no room.
- Kept deliberately NARROW. Better to be honest about 15 primes than
  pretend we know all 60+. Missing primes return {"ok": False,
  "reason": "not_mapped"} — the caller should treat that as a
  signal to run deep_research rather than confabulate.

Public API
──────────
  lookup(prime: str, product: str = "") -> dict
  list_primes() -> list[dict]
  arkmurus_opportunities(prime: str, product: str = "") -> list[dict]
"""
from __future__ import annotations

from typing import Any


# ══════════════════════════════════════════════════════════════════════════
# Prime ecosystem data
# ══════════════════════════════════════════════════════════════════════════
#
# Shape per prime:
#   {
#     "hq_country": str,
#     "itar_regime": "US_ITAR" | "UK_EXPORT" | "EU_EXPORT" | "OTHER",
#     "aliases": [str] — names the entity might also appear under,
#     "product_families": {
#        <product>: {
#           "type": str,
#           "sub_contractors": [
#             {"name": str, "country": str, "role": str,
#              "arkmurus_fit": "HIGH|MEDIUM|LOW|NONE",
#              "entry_path": str}
#           ],
#        }
#     }
#   }

_PRIMES: dict[str, dict[str, Any]] = {

    "boeing": {
        "hq_country": "US",
        "itar_regime": "US_ITAR",
        "aliases": ["Boeing Defense", "Boeing Defense, Space & Security", "Boeing Global Services"],
        "product_families": {
            "P-8 Poseidon": {
                "type": "Maritime patrol aircraft (MPA / ASW)",
                "sub_contractors": [
                    {"name": "Raytheon", "country": "US", "role": "APY-10 radar + mission systems",
                     "arkmurus_fit": "NONE", "entry_path": "ITAR-direct; no broker channel"},
                    {"name": "Ultra Electronics", "country": "UK", "role": "Sonobuoys + launchers",
                     "arkmurus_fit": "HIGH", "entry_path": "Recurring-consumable resupply; UK prime used to broker intermediaries"},
                    {"name": "Leonardo UK", "country": "UK/IT", "role": "Acoustic processing / MAD",
                     "arkmurus_fit": "MEDIUM", "entry_path": "UK defence-industry participation; broker-friendly on smaller nations"},
                    {"name": "L3Harris", "country": "US", "role": "Comms + datalinks",
                     "arkmurus_fit": "LOW", "entry_path": "US-dominated; only non-ITAR ground support credible"},
                    {"name": "CAE", "country": "CA", "role": "Training systems + simulators",
                     "arkmurus_fit": "MEDIUM", "entry_path": "Training-device integrator paths; follows aircraft by 6-18 months"},
                    {"name": "Boeing Global Services", "country": "US", "role": "Sustainment / MRO",
                     "arkmurus_fit": "HIGH", "entry_path": "In-country MRO footprint built in every new P-8 nation; broker into local industry partner"},
                    {"name": "Terma A/S", "country": "DK", "role": "Avionics / self-protection (on Danish P-8 contracts)",
                     "arkmurus_fit": "HIGH", "entry_path": "Danish industrial-participation partner; relationship-driven"},
                ],
            },
            "F-15 Eagle II": {
                "type": "Multi-role fighter",
                "sub_contractors": [
                    {"name": "BAE Systems", "country": "UK", "role": "Electronic warfare suite (EPAWSS)",
                     "arkmurus_fit": "MEDIUM", "entry_path": "UK defence-industry partner; broker access via UK supply chain"},
                    {"name": "Pratt & Whitney", "country": "US", "role": "F100 engines",
                     "arkmurus_fit": "NONE", "entry_path": "Prime-direct; ITAR-dense"},
                    {"name": "Northrop Grumman", "country": "US", "role": "AESA radar (APG-82)",
                     "arkmurus_fit": "NONE", "entry_path": "ITAR-direct"},
                ],
            },
            "AH-64 Apache": {
                "type": "Attack helicopter",
                "sub_contractors": [
                    {"name": "Lockheed Martin", "country": "US", "role": "Longbow / targeting",
                     "arkmurus_fit": "NONE", "entry_path": "ITAR-direct"},
                    {"name": "General Electric", "country": "US", "role": "T700 engines",
                     "arkmurus_fit": "LOW", "entry_path": "Only non-ITAR MRO tooling"},
                ],
            },
            "CH-47 Chinook": {
                "type": "Heavy-lift helicopter",
                "sub_contractors": [
                    {"name": "Honeywell", "country": "US", "role": "T55 engines",
                     "arkmurus_fit": "NONE", "entry_path": "Prime-direct"},
                    {"name": "Leonardo Helicopters (AgustaWestland)", "country": "IT/UK", "role": "UK-assembly Chinook Mk7",
                     "arkmurus_fit": "MEDIUM", "entry_path": "UK assembly line → UK offset pathway"},
                ],
            },
        },
    },

    "lockheed_martin": {
        "hq_country": "US",
        "itar_regime": "US_ITAR",
        "aliases": ["Lockheed Martin", "LM", "Lockheed", "LMT"],
        "product_families": {
            "F-35 Lightning II": {
                "type": "5th-gen multi-role fighter",
                "sub_contractors": [
                    {"name": "BAE Systems", "country": "UK", "role": "Aft fuselage / EW",
                     "arkmurus_fit": "LOW", "entry_path": "Tier-1 JSF partner; broker access limited"},
                    {"name": "Rolls-Royce", "country": "UK", "role": "LiftSystem (F-35B STOVL)",
                     "arkmurus_fit": "NONE", "entry_path": "Prime-direct"},
                    {"name": "Pratt & Whitney", "country": "US", "role": "F135 engine",
                     "arkmurus_fit": "NONE", "entry_path": "Prime-direct, ITAR-dense"},
                    {"name": "Local in-country depots", "country": "various", "role": "Regional MRO centres (e.g. Cameri IT, Sewol KR)",
                     "arkmurus_fit": "MEDIUM", "entry_path": "In-country sustainment sub-tier; broker access via local industry partners"},
                ],
            },
            "C-130J Super Hercules": {
                "type": "Tactical airlifter",
                "sub_contractors": [
                    {"name": "Rolls-Royce", "country": "UK", "role": "AE 2100 engines",
                     "arkmurus_fit": "MEDIUM", "entry_path": "Engine MRO network, UK/EU broker-friendly"},
                    {"name": "Marshall Aerospace", "country": "UK", "role": "Airframe MRO",
                     "arkmurus_fit": "HIGH", "entry_path": "UK MRO prime; works with brokers on export sustainment"},
                ],
            },
            "HIMARS / MLRS": {
                "type": "Artillery rocket system",
                "sub_contractors": [
                    {"name": "Diehl Defence", "country": "DE", "role": "European integration / ammunition",
                     "arkmurus_fit": "MEDIUM", "entry_path": "European offset partner"},
                ],
            },
            "PAC-3 / Patriot": {
                "type": "Air-defence missile",
                "sub_contractors": [
                    {"name": "Raytheon (RTX)", "country": "US", "role": "Prime system integrator on older Patriot variants",
                     "arkmurus_fit": "NONE", "entry_path": "Prime-direct"},
                    {"name": "MBDA Germany", "country": "DE", "role": "European integration (selected users)",
                     "arkmurus_fit": "MEDIUM", "entry_path": "EU industrial participation"},
                ],
            },
        },
    },

    "bae_systems": {
        "hq_country": "UK",
        "itar_regime": "UK_EXPORT",
        "aliases": ["BAE Systems", "BAE", "British Aerospace"],
        "product_families": {
            "Typhoon / Eurofighter": {
                "type": "Multi-role fighter (quadrilateral consortium)",
                "sub_contractors": [
                    {"name": "Leonardo", "country": "IT/UK", "role": "Radar (Captor-E) + avionics",
                     "arkmurus_fit": "MEDIUM", "entry_path": "EU industrial participation; broker-friendly on sustainment"},
                    {"name": "MBDA", "country": "UK/FR/IT", "role": "Meteor + ASRAAM missiles",
                     "arkmurus_fit": "LOW", "entry_path": "Prime-direct on munitions"},
                    {"name": "Rolls-Royce + MTU + ITP + Avio", "country": "UK/DE/ES/IT", "role": "EJ200 engine consortium",
                     "arkmurus_fit": "LOW", "entry_path": "Prime-direct"},
                ],
            },
            "Hawk Trainer": {
                "type": "Advanced jet trainer",
                "sub_contractors": [
                    {"name": "Rolls-Royce / Honeywell (Adour)", "country": "UK/US", "role": "Adour engines",
                     "arkmurus_fit": "MEDIUM", "entry_path": "Engine MRO + spares network, broker-friendly on non-US customers"},
                ],
            },
            "Type 26 / Hunter-class Frigate": {
                "type": "Anti-submarine frigate",
                "sub_contractors": [
                    {"name": "Thales UK", "country": "UK", "role": "Sonar 2087 + combat management",
                     "arkmurus_fit": "MEDIUM", "entry_path": "UK sub-tier; broker access via UK supply chain"},
                    {"name": "MTU / Rolls-Royce", "country": "DE/UK", "role": "Propulsion",
                     "arkmurus_fit": "LOW", "entry_path": "Prime-direct"},
                ],
            },
        },
    },

    "airbus_defence": {
        "hq_country": "EU (FR/DE/ES)",
        "itar_regime": "EU_EXPORT",
        "aliases": ["Airbus Defence and Space", "Airbus Defence", "ADS"],
        "product_families": {
            "A400M Atlas": {
                "type": "Strategic airlifter",
                "sub_contractors": [
                    {"name": "Europrop International (ITP/MTU/Safran/RR)", "country": "EU", "role": "TP400 engines",
                     "arkmurus_fit": "LOW", "entry_path": "Prime-direct"},
                    {"name": "Marshall Aerospace", "country": "UK", "role": "UK in-service-support partner",
                     "arkmurus_fit": "HIGH", "entry_path": "UK MRO broker-friendly"},
                ],
            },
            "C-295 / C-295 MPA": {
                "type": "Tactical airlifter / maritime patrol",
                "sub_contractors": [
                    {"name": "Airbus Defence Seville", "country": "ES", "role": "Assembly",
                     "arkmurus_fit": "LOW", "entry_path": "Prime-direct"},
                    {"name": "Elbit Systems", "country": "IL", "role": "MPA mission suite (selected customers)",
                     "arkmurus_fit": "MEDIUM", "entry_path": "Israeli sub-tier; broker access on export-controlled markets"},
                ],
            },
            "H225M / Tiger": {
                "type": "Military helicopters",
                "sub_contractors": [
                    {"name": "Safran Helicopter Engines", "country": "FR", "role": "Engines",
                     "arkmurus_fit": "MEDIUM", "entry_path": "French sub-tier; broker-friendly on non-FR customers"},
                ],
            },
        },
    },

    "leonardo": {
        "hq_country": "IT",
        "itar_regime": "EU_EXPORT",
        "aliases": ["Leonardo", "Leonardo S.p.A.", "Finmeccanica", "AgustaWestland"],
        "product_families": {
            "AW101 / AW149 / AW159": {
                "type": "Military helicopters",
                "sub_contractors": [
                    {"name": "Leonardo UK (Yeovil)", "country": "UK", "role": "UK assembly line",
                     "arkmurus_fit": "HIGH", "entry_path": "UK industrial-participation partner"},
                    {"name": "GE Aerospace", "country": "US", "role": "CT7 engines on AW159",
                     "arkmurus_fit": "LOW", "entry_path": "US sub-tier, ITAR for engine spares"},
                ],
            },
            "M-346 Master": {
                "type": "Lead-in fighter trainer",
                "sub_contractors": [
                    {"name": "IAI / Elbit", "country": "IL", "role": "Training-system variants (IAF M-346I)",
                     "arkmurus_fit": "MEDIUM", "entry_path": "Export-control friendly on non-IL customers"},
                ],
            },
            "EFA radar (Captor-E) / Mission systems": {
                "type": "Radar + avionics",
                "sub_contractors": [],
            },
        },
    },

    "mbda": {
        "hq_country": "UK/FR/IT (consortium)",
        "itar_regime": "EU_EXPORT",
        "aliases": ["MBDA", "MBDA Missile Systems"],
        "product_families": {
            "Meteor / ASRAAM / Brimstone": {
                "type": "Air-launched missiles",
                "sub_contractors": [
                    {"name": "MBDA UK Stevenage", "country": "UK", "role": "Final assembly (UK customers)",
                     "arkmurus_fit": "LOW", "entry_path": "Prime-direct on munitions"},
                ],
            },
            "CAMM / Sea Ceptor": {
                "type": "Ground / naval air defence",
                "sub_contractors": [
                    {"name": "Lockheed Martin UK", "country": "UK", "role": "Integrator on some naval customers",
                     "arkmurus_fit": "MEDIUM", "entry_path": "UK sub-tier on integration"},
                ],
            },
        },
    },

    "thales": {
        "hq_country": "FR",
        "itar_regime": "EU_EXPORT",
        "aliases": ["Thales", "Thales Group", "Thales UK"],
        "product_families": {
            "CATHERINE / Optical sights": {
                "type": "Thermal imagers + electro-optical sights",
                "sub_contractors": [],
            },
            "SMART radars / naval radar family": {
                "type": "Naval surveillance radar",
                "sub_contractors": [],
            },
            "Radio / comms suites (Synaps family)": {
                "type": "Tactical comms",
                "sub_contractors": [],
            },
        },
    },

    "saab": {
        "hq_country": "SE",
        "itar_regime": "EU_EXPORT",
        "aliases": ["Saab", "Saab AB"],
        "product_families": {
            "Gripen E/F": {
                "type": "Multi-role fighter",
                "sub_contractors": [
                    {"name": "General Electric", "country": "US", "role": "F414 engine",
                     "arkmurus_fit": "NONE", "entry_path": "US ITAR-prime direct"},
                    {"name": "Leonardo UK", "country": "UK/IT", "role": "ES-05 Raven AESA radar (exported)",
                     "arkmurus_fit": "MEDIUM", "entry_path": "UK sub-tier"},
                ],
            },
            "Giraffe / Erieye radars": {
                "type": "Ground + airborne early warning radars",
                "sub_contractors": [],
            },
            "Global Eye AEW&C": {
                "type": "Airborne early warning",
                "sub_contractors": [
                    {"name": "Bombardier", "country": "CA", "role": "Airframe (Global 6000)",
                     "arkmurus_fit": "MEDIUM", "entry_path": "Canadian airframe sub-tier"},
                ],
            },
        },
    },

    "kongsberg": {
        "hq_country": "NO",
        "itar_regime": "EU_EXPORT",
        "aliases": ["Kongsberg", "Kongsberg Defence & Aerospace", "KDA"],
        "product_families": {
            "NSM (Naval Strike Missile)": {
                "type": "Anti-ship missile",
                "sub_contractors": [
                    {"name": "Raytheon", "country": "US", "role": "US-market packaging (JSM for F-35)",
                     "arkmurus_fit": "LOW", "entry_path": "US-co-production; ITAR-controlled"},
                ],
            },
            "NASAMS": {
                "type": "Ground-based air defence",
                "sub_contractors": [
                    {"name": "Raytheon", "country": "US", "role": "AMRAAM missile + launcher",
                     "arkmurus_fit": "NONE", "entry_path": "US ITAR-prime direct"},
                ],
            },
            "Protector RWS": {
                "type": "Remote weapon station",
                "sub_contractors": [],
            },
        },
    },

    "raytheon": {
        "hq_country": "US",
        "itar_regime": "US_ITAR",
        "aliases": ["Raytheon", "RTX", "Raytheon Technologies", "Raytheon Missiles & Defense"],
        "product_families": {
            "Patriot / PAC-3 (Raytheon part)": {
                "type": "Air defence",
                "sub_contractors": [
                    {"name": "MBDA Germany", "country": "DE", "role": "European integration",
                     "arkmurus_fit": "MEDIUM", "entry_path": "EU offset channel"},
                ],
            },
            "AMRAAM / Sidewinder / Stinger": {
                "type": "Air-to-air and surface-to-air missiles",
                "sub_contractors": [],
            },
            "SPY-1 / SPY-6 radars": {
                "type": "Naval AESA radar",
                "sub_contractors": [],
            },
        },
    },

    "general_dynamics": {
        "hq_country": "US",
        "itar_regime": "US_ITAR",
        "aliases": ["General Dynamics", "GD", "GDLS", "GDELS"],
        "product_families": {
            "Abrams M1A2": {
                "type": "Main battle tank",
                "sub_contractors": [],
            },
            "Stryker / ASCOD / Ajax": {
                "type": "Armoured fighting vehicle family",
                "sub_contractors": [
                    {"name": "GDLS-UK / General Dynamics UK", "country": "UK", "role": "Ajax assembly",
                     "arkmurus_fit": "HIGH", "entry_path": "UK industrial participation + MRO"},
                ],
            },
        },
    },

    "rheinmetall": {
        "hq_country": "DE",
        "itar_regime": "EU_EXPORT",
        "aliases": ["Rheinmetall", "Rheinmetall AG", "Rheinmetall Defence"],
        "product_families": {
            "Leopard 2": {
                "type": "Main battle tank (co-prime with KMW / KNDS)",
                "sub_contractors": [
                    {"name": "KNDS Deutschland (Krauss-Maffei Wegmann)", "country": "DE", "role": "Airframe co-prime",
                     "arkmurus_fit": "MEDIUM", "entry_path": "EU offset + sustainment"},
                ],
            },
            "Puma IFV": {
                "type": "Infantry fighting vehicle (joint with KNDS)",
                "sub_contractors": [],
            },
            "120mm ammunition family": {
                "type": "Tank ammunition",
                "sub_contractors": [],
            },
        },
    },

    "knds": {
        "hq_country": "DE/FR",
        "itar_regime": "EU_EXPORT",
        "aliases": ["KNDS", "KNDS Deutschland", "KNDS France", "Nexter", "KMW", "Krauss-Maffei Wegmann"],
        "product_families": {
            "Leopard 2 / Panther KF51": {
                "type": "Main battle tank",
                "sub_contractors": [],
            },
            "CAESAR self-propelled howitzer": {
                "type": "155mm SPH (wheeled)",
                "sub_contractors": [],
            },
            "Leclerc": {
                "type": "French MBT",
                "sub_contractors": [],
            },
        },
    },

    "dassault": {
        "hq_country": "FR",
        "itar_regime": "EU_EXPORT",
        "aliases": ["Dassault Aviation", "Dassault"],
        "product_families": {
            "Rafale": {
                "type": "Multi-role fighter",
                "sub_contractors": [
                    {"name": "Safran", "country": "FR", "role": "M88 engines",
                     "arkmurus_fit": "MEDIUM", "entry_path": "French sub-tier; non-FR customers broker-friendly"},
                    {"name": "Thales", "country": "FR", "role": "RBE2 AESA radar + SPECTRA EW",
                     "arkmurus_fit": "LOW", "entry_path": "Prime-direct on sensor suites"},
                    {"name": "MBDA", "country": "EU", "role": "Meteor / MICA missiles",
                     "arkmurus_fit": "LOW", "entry_path": "Prime-direct on munitions"},
                ],
            },
            "Falcon 2000 / 6X / 8X (military variants)": {
                "type": "Business-jet-derived special mission aircraft",
                "sub_contractors": [],
            },
        },
    },

    "textron": {
        "hq_country": "US",
        "itar_regime": "US_ITAR",
        "aliases": ["Textron", "Bell Textron", "Textron Systems"],
        "product_families": {
            "Bell AH-1Z / UH-1Y": {
                "type": "Attack / utility helicopters",
                "sub_contractors": [],
            },
            "V-22 Osprey (with Boeing)": {
                "type": "Tilt-rotor",
                "sub_contractors": [
                    {"name": "Rolls-Royce", "country": "UK", "role": "AE1107C engines",
                     "arkmurus_fit": "MEDIUM", "entry_path": "Engine MRO network"},
                ],
            },
        },
    },
}


# ══════════════════════════════════════════════════════════════════════════
# Normalisation + lookup
# ══════════════════════════════════════════════════════════════════════════

def _normalise_prime_key(prime: str) -> str:
    """Turn a free-text prime name into the dict key. Falls back to the
    lower-snake-case form so lookups still work on new primes that
    share an alias pattern with existing entries."""
    p = (prime or "").strip().lower()
    if not p:
        return ""
    # Fast path: direct key match
    if p in _PRIMES:
        return p
    # Alias search
    for key, data in _PRIMES.items():
        aliases = [a.lower() for a in data.get("aliases", [])]
        if p in aliases or any(p in a or a in p for a in aliases):
            return key
        # Also try the key's pretty form
        if p == key.replace("_", " "):
            return key
    # Heuristic: snake-case it
    snake = p.replace(" ", "_").replace("-", "_")
    return snake if snake in _PRIMES else ""


def _normalise_product(product: str) -> str:
    return (product or "").strip().lower()


def _product_matches(query: str, family_name: str) -> bool:
    """Fuzzy match a product query (e.g. "P-8A") against a family name
    (e.g. "P-8 Poseidon"). Handles minor variant suffixes (A/B/C) and
    acronym-only queries. Pre-2026-04-20 the match was a plain
    `q in name` substring, which returned False for "P-8A" vs
    "P-8 Poseidon" and caused the caller to fall through to ALL
    product families (pulling Chinook sub-contractors into a P-8 query).
    """
    q = query.strip().lower()
    n = family_name.strip().lower()
    if not q or not n:
        return False
    if q in n:
        return True
    # Token-level: does the first token of name appear in query, or
    # vice versa? Catches "P-8A" ↔ "P-8 Poseidon".
    n_tokens = n.replace("-", " ").replace("/", " ").split()
    q_tokens = q.replace("-", " ").replace("/", " ").split()
    if not n_tokens or not q_tokens:
        return False
    # Exact first-token match (after normalising hyphens)
    if n_tokens[0] == q_tokens[0]:
        return True
    # Stem match: "p8a" and "p8" collapse to "p8"
    def _stem(s: str) -> str:
        s2 = "".join(c for c in s if c.isalnum()).lower()
        # Strip trailing single letter variant (a / b / c)
        if len(s2) > 2 and s2[-1].isalpha() and s2[-2].isdigit():
            return s2[:-1]
        return s2
    if _stem(q_tokens[0]) == _stem(n_tokens[0]) and len(_stem(q_tokens[0])) >= 2:
        return True
    return False


def lookup(prime: str, product: str = "") -> dict[str, Any]:
    """Resolve a prime (and optionally a product family) to its
    structured record. Returns:

      {"ok": True, "prime_key": str, "prime_data": {...},
       "matched_products": [<family>, ...],
       "arkmurus_opportunities": [<sub-entry with fit>=MEDIUM>]}

    Or {"ok": False, "reason": "not_mapped"} if the prime isn't in
    the table.
    """
    key = _normalise_prime_key(prime)
    if not key:
        return {"ok": False, "reason": "not_mapped", "prime": prime}
    data = _PRIMES[key]
    families = data["product_families"]
    if not product:
        matched = sorted(families.keys())
    else:
        matched = [name for name in families if _product_matches(product, name)]
        if not matched:
            matched = sorted(families.keys())   # fall back to "all"
    # R-F1001 - wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="prime_sub_map",
        summary="Lookup",
        source_id="prime_sub_map:R-F1001",
    )

    return {
        "ok": True,
        "prime_key": key,
        "prime_data": {
            "hq_country": data["hq_country"],
            "itar_regime": data["itar_regime"],
            "aliases": data.get("aliases", []),
        },
        "matched_products": matched,
        "products": {p: families[p] for p in matched},
        "arkmurus_opportunities": arkmurus_opportunities(prime, product),
    }


def list_primes() -> list[dict[str, Any]]:
    """Return a summary of every prime in the map, suitable for a
    dashboard or capability-manifest check."""
    out = []
    for key, data in _PRIMES.items():
        out.append({
            "key": key,
            "aliases": data.get("aliases", []),
            "hq_country": data["hq_country"],
            "itar_regime": data["itar_regime"],
            "product_families": list(data["product_families"].keys()),
        })
    return out


def arkmurus_opportunities(prime: str, product: str = "") -> list[dict[str, Any]]:
    """Extract only the MEDIUM+ Arkmurus-fit sub-contractor entries
    across the matched product families. The fast-path answer to
    'given this alert, what's our angle?'"""
    key = _normalise_prime_key(prime)
    if not key:
        return []
    data = _PRIMES[key]
    families = data["product_families"]
    if product:
        target_families = [name for name in families if _product_matches(product, name)]
        if not target_families:
            target_families = list(families.keys())
    else:
        target_families = list(families.keys())
    out: list[dict[str, Any]] = []
    for fam_name in target_families:
        for sub in families[fam_name].get("sub_contractors", []):
            if sub.get("arkmurus_fit") in ("HIGH", "MEDIUM"):
                out.append({
                    "prime": key,
                    "product_family": fam_name,
                    "sub_contractor": sub["name"],
                    "country": sub["country"],
                    "role": sub["role"],
                    "fit": sub["arkmurus_fit"],
                    "entry_path": sub["entry_path"],
                })
    # Sort HIGH first, then MEDIUM, alphabetical within
    out.sort(key=lambda e: (0 if e["fit"] == "HIGH" else 1, e["sub_contractor"]))
    return out
