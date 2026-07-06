"""Parser for OFAC SDN Enhanced XML → canonical sanctions store.

Source: https://www.treasury.gov/ofac/downloads/sdn_enhanced.xml
Daily refresh. Schema (top-level): <sdnEntry> blocks, each with
<sdnType>, <firstName>/<lastName>/<formattedFullName>, <aliasList>,
<addressList>, <programList>, etc.

We use ElementTree streaming for memory efficiency — the file is
~100 MB raw with ~12 000 entries; parsing it into memory at once
is fine on a fly machine but streaming keeps the door open for
future growth.
"""
from __future__ import annotations

import logging
import time
from typing import Iterator

from defusedxml import ElementTree as ET

from . import store
from .normalise import normalise_name

logger = logging.getLogger("aria.sanctions_canonical.ofac_sdn")

SOURCE_ID = "ofac_sdn"
UPSTREAM_URL = "https://www.treasury.gov/ofac/downloads/sdn_enhanced.xml"


def _text(elem, tag: str) -> str:
    """Find the first direct-child <tag> text or empty string.
    Namespace-agnostic — strips any xmlns prefix from tag matching."""
    for child in elem:
        if child.tag.rsplit("}", 1)[-1] == tag:
            return (child.text or "").strip()
    return ""


def _findall_local(elem, tag: str) -> list:
    """Namespace-agnostic findall — returns all descendants whose
    local-name matches `tag`."""
    return [e for e in elem.iter() if e.tag.rsplit("}", 1)[-1] == tag]


def _extract_aliases(entry: ET.Element) -> list[dict]:
    """All formatted name + AKA / FKA entries on an SDN."""
    out = []
    # In Enhanced schema, alternate names appear as <name> blocks
    # with <isPrimary> + <aliasType> + translations.
    for name_block in _findall_local(entry, "name"):
        is_primary = _text(name_block, "isPrimary").lower() == "true"
        alias_type_el = next(
            (e for e in name_block.iter()
             if e.tag.rsplit("}", 1)[-1] == "aliasType"),
            None,
        )
        alias_type = (alias_type_el.text or "").strip() if alias_type_el is not None else ""
        for trans in _findall_local(name_block, "translation"):
            formatted = _text(trans, "formattedFullName")
            if not formatted:
                first = _text(trans, "formattedFirstName")
                last = _text(trans, "formattedLastName")
                formatted = f"{first} {last}".strip()
            if not formatted:
                continue
            out.append({
                "formatted": formatted,
                "normalised": normalise_name(formatted),
                "alias_type": "primary" if is_primary else (alias_type or "a.k.a."),
            })
    # If the entry has top-level formattedFullName not captured above
    top = _text(entry, "formattedFullName")
    if top and not any(a["formatted"] == top for a in out):
        out.insert(0, {
            "formatted": top,
            "normalised": normalise_name(top),
            "alias_type": "primary",
        })
    return out


def _extract_addresses(entry: ET.Element) -> tuple[list[str], list[str]]:
    """Return (countries, free_text_addresses) from the Enhanced
    SDN schema. Per-<address>: <country>X</country> + <translations>
    <translation><addressParts><addressPart><type>CITY</type>
    <value>Havana</value></addressPart>..."""
    countries: list[str] = []
    addresses: list[str] = []
    for addr_el in _findall_local(entry, "address"):
        country = ""
        parts: dict[str, str] = {}  # type-name → value (CITY, ADDRESS1, etc.)
        for child in addr_el.iter():
            local = child.tag.rsplit("}", 1)[-1]
            if local == "country":
                country = (child.text or "").strip()
        for ap in _findall_local(addr_el, "addressPart"):
            type_text = ""
            value_text = ""
            for c in ap:
                local_c = c.tag.rsplit("}", 1)[-1]
                if local_c == "type":
                    type_text = (c.text or "").strip().upper()
                elif local_c == "value":
                    value_text = (c.text or "").strip()
            if type_text and value_text:
                parts[type_text] = value_text
        if country and country not in countries:
            countries.append(country)
        # Assemble a readable address string
        bits = []
        for k in ("ADDRESS1", "ADDRESS2", "ADDRESS3", "CITY",
                  "STATE/PROVINCE", "POSTAL CODE"):
            v = parts.get(k)
            if v and v not in bits:
                bits.append(v)
        if country and country not in bits:
            bits.append(country)
        full = ", ".join(bits)
        if full and full not in addresses:
            addresses.append(full)
    return countries, addresses


def _extract_programs(entry: ET.Element) -> list[str]:
    """All sanctions program codes on the entry. The Enhanced schema
    uses <sanctionsProgram refId="…">CUBA</sanctionsProgram> inside
    a <sanctionsPrograms> container; older legacy schemas used
    <program>. Accept both."""
    out = []
    for tag in ("sanctionsProgram", "program"):
        for prog in _findall_local(entry, tag):
            if prog.text:
                v = prog.text.strip()
                if v and v not in out:
                    out.append(v)
    return out


def _uid_from_entity(elem) -> str:
    """Extract a stable UID from the OFAC Enhanced <entity> element.
    Schema: <entity id="N"><generalInfo><identityId>..."""
    # Prefer the entity's own id attribute (stable)
    eid = elem.get("id") or ""
    if eid:
        return f"entity_{eid}"
    # Fallback to identityId
    for el in elem.iter():
        if el.tag.rsplit("}", 1)[-1] == "identityId" and el.text:
            return f"identity_{el.text.strip()}"
    return ""


def _entity_type_text(elem) -> str:
    """<generalInfo><entityType>...</entityType></generalInfo>"""
    for el in elem.iter():
        if el.tag.rsplit("}", 1)[-1] == "entityType":
            return (el.text or "").strip()
    return ""


def _primary_name_from_entity(elem) -> str:
    """Find the primary translation's formattedFullName under <names>."""
    for name_block in _findall_local(elem, "name"):
        if _text(name_block, "isPrimary").lower() == "true":
            for trans in _findall_local(name_block, "translation"):
                if _text(trans, "isPrimary").lower() == "true":
                    n = _text(trans, "formattedFullName")
                    if n:
                        return n
    # Fallback: first formattedFullName anywhere
    for el in elem.iter():
        if el.tag.rsplit("}", 1)[-1] == "formattedFullName" and el.text:
            return el.text.strip()
    return ""


_COUNTRY_HINTS = {
    "afghanistan", "albania", "algeria", "andorra", "angola", "argentina",
    "armenia", "australia", "austria", "azerbaijan", "bahamas", "bahrain",
    "bangladesh", "belarus", "belgium", "belize", "benin", "bolivia",
    "bosnia", "botswana", "brazil", "brunei", "bulgaria", "burkina faso",
    "burundi", "cambodia", "cameroon", "canada", "central african republic",
    "chad", "chile", "china", "colombia", "comoros", "congo", "costa rica",
    "croatia", "cuba", "cyprus", "czech", "czech republic", "denmark",
    "djibouti", "dominican republic", "dr congo", "ecuador", "egypt",
    "el salvador", "eritrea", "estonia", "ethiopia", "fiji", "finland",
    "france", "gabon", "gambia", "georgia", "germany", "ghana", "greece",
    "guatemala", "guinea", "guinea-bissau", "haiti", "honduras", "hong kong",
    "hungary", "iceland", "india", "indonesia", "iran", "iraq", "ireland",
    "israel", "italy", "ivory coast", "jamaica", "japan", "jordan",
    "kazakhstan", "kenya", "kosovo", "kuwait", "kyrgyzstan", "laos",
    "latvia", "lebanon", "lesotho", "liberia", "libya", "liechtenstein",
    "lithuania", "luxembourg", "macedonia", "madagascar", "malawi",
    "malaysia", "maldives", "mali", "malta", "mauritania", "mauritius",
    "mexico", "moldova", "monaco", "mongolia", "montenegro", "morocco",
    "mozambique", "myanmar", "burma", "namibia", "nepal", "netherlands",
    "new zealand", "nicaragua", "niger", "nigeria", "north korea", "norway",
    "oman", "pakistan", "palestine", "panama", "papua new guinea", "paraguay",
    "peru", "philippines", "poland", "portugal", "qatar", "romania",
    "russia", "russian federation", "rwanda", "saudi arabia", "senegal",
    "serbia", "sierra leone", "singapore", "slovakia", "slovenia",
    "solomon islands", "somalia", "south africa", "south korea",
    "south sudan", "spain", "sri lanka", "sudan", "suriname", "sweden",
    "switzerland", "syria", "taiwan", "tajikistan", "tanzania", "thailand",
    "togo", "tunisia", "turkey", "turkiye", "turkmenistan", "uganda",
    "ukraine", "united arab emirates", "uae", "united kingdom", "uk",
    "united states", "usa", "uruguay", "uzbekistan", "venezuela", "vietnam",
    "yemen", "zambia", "zimbabwe",
}


def _extract_feature_countries(entry: ET.Element) -> list[str]:
    """OFAC Enhanced stores person-level geographic anchors as
    <feature> nodes (birthplace, nationality, etc.) — NOT as
    <address>. Mine these for country signals so the entity-overlap
    gate can still apply jurisdiction/address rules to persons."""
    out: list[str] = []
    for feat in _findall_local(entry, "feature"):
        for el in feat.iter():
            local = el.tag.rsplit("}", 1)[-1]
            if local in ("value", "comments") and el.text:
                text = el.text.strip()
                # Heuristic 1: last comma-separated token is a country
                tail = text.rsplit(",", 1)[-1].strip().lower()
                if tail in _COUNTRY_HINTS:
                    canonical = tail.title()
                    if canonical not in out:
                        out.append(canonical)
                # Heuristic 2: any country word in the text
                else:
                    for cw in _COUNTRY_HINTS:
                        if cw in text.lower().split():
                            canonical = cw.title()
                            if canonical not in out:
                                out.append(canonical)
                            break
    return out


def parse_xml(xml_path: str) -> Iterator[dict]:
    """Stream over the SDN Enhanced XML, yielding one canonical-store
    dict per <entity>. Memory-efficient via iterparse + clear."""
    context = ET.iterparse(xml_path, events=("end",))
    for _, elem in context:
        local = elem.tag.rsplit("}", 1)[-1]
        if local != "entity":
            continue
        try:
            uid = _uid_from_entity(elem)
            primary_name = _primary_name_from_entity(elem)
            if not primary_name or not uid:
                elem.clear()
                continue
            entity_type = _entity_type_text(elem)
            aliases = _extract_aliases(elem)
            countries, addresses = _extract_addresses(elem)
            # Person-side anchor extraction: birthplace/nationality in
            # <features> often carries the only country signal we have.
            for c in _extract_feature_countries(elem):
                if c not in countries:
                    countries.append(c)
            programs = _extract_programs(elem)
            # R-F527 — tighter excerpt cap (2KB → 800B) reduces peak
            # heap during full-list load from ~95MB to ~40MB. 800B
            # is enough to spot-check a few names + the program + a
            # country tag in the audit trail.
            raw_excerpt = ET.tostring(elem, encoding="unicode")[:800]
            yield {
                "source_uid": uid,
                "formatted_name": primary_name,
                "normalised_name": normalise_name(primary_name),
                "entity_type": entity_type,
                "countries": countries,
                "addresses": addresses,
                "aliases": aliases,
                "programs": programs,
                "designation_at": None,
                "raw_excerpt": raw_excerpt,
            }
        except Exception as e:
            logger.warning("[ofac_sdn] failed to parse one entity: %s", e)
        finally:
            elem.clear()


def load_from_file(xml_path: str) -> int:
    """End-to-end: parse XML → replace source rows in store. Returns
    number of entries loaded.

    R-F527 — pass the parse_xml generator directly to
    store.replace_source. Pre-R-F527 `list(parse_xml(...))`
    materialised all ~19k dicts at once, peaking ~95MB heap during
    load and contributing to the 2026-05-15 08:56-09:08 BST
    production wedge.
    """
    started = time.time()
    rows_loaded = 0
    success = False
    error = ""
    try:
        rows_loaded = store.replace_source(SOURCE_ID, parse_xml(xml_path))
        success = True
        logger.info("[ofac_sdn] loaded %d entries from %s", rows_loaded, xml_path)
        # R-F1304 — wire success to brain (§21a)
        try:
            from ..engine_wiring import wire_success
            wire_success(
                module="sanctions_canonical.ofac_sdn",
                summary=f"Loaded {rows_loaded} OFAC SDN entries",
                source_id="sanctions_canonical:ofac_sdn:load_from_file",
            )
        except Exception:
            pass
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
        logger.error("[ofac_sdn] load failed: %s", error)
        # R-F1304 — wire failure to brain (§21a)
        try:
            from ..engine_wiring import wire_failure
            wire_failure(
                module="sanctions_canonical.ofac_sdn",
                detail=f"OFAC SDN load failed: {error[:200]}",
                gap_type="source_failure",
                source="sanctions_canonical:ofac_sdn:load_from_file",
            )
        except Exception:
            pass
    finally:
        store.record_refresh(
            SOURCE_ID, started, time.time(), rows_loaded, success, error,
        )
    return rows_loaded
