"""Parser for EU Consolidated Sanctions List CSV → canonical store.

Source: https://webgate.ec.europa.eu/fsd/fsf/public/files/
        csvFullSanctionsList_1_1/content?token=dG9rZW4tMjAxNw
Publisher: DG-FISMA. Daily refresh per data.europa.eu metadata.

CSV is denormalised one-row-per-(name-variant × address). Grouping
by `Entity_logical_id` re-assembles one canonical entity with all
its alias rows and address rows.

The token in the URL is part of the public download path — the EU
publishes the static path with an embedded base64 token; rotating
that token will be the operator's signal to refresh the URL constant.
"""
from __future__ import annotations

import csv
import logging
import time
from typing import Iterator

from . import store
from .normalise import normalise_name

logger = logging.getLogger("aria.sanctions_canonical.eu_consolidated")

SOURCE_ID = "eu_consolidated"
UPSTREAM_URL = (
    "https://webgate.ec.europa.eu/fsd/fsf/public/files/"
    "csvFullSanctionsList_1_1/content?token=dG9rZW4tMjAxNw"
)


# EU CSV is semicolon-delimited (typical for EU datasets, French/German
# convention). Columns are stable but spelled inconsistently across
# minor schema revisions. Use forgiving column lookup.
_EU_DELIMITERS = [";", ","]


def _open_csv(path: str):
    """Detect delimiter by reading the header line."""
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        sample = f.read(4096)
    sniffer = csv.Sniffer()
    try:
        dialect = sniffer.sniff(sample, delimiters="".join(_EU_DELIMITERS))
        delim = dialect.delimiter
    except csv.Error:
        # Fallback: count which candidate appears most often in first line
        first = sample.split("\n", 1)[0]
        delim = max(_EU_DELIMITERS, key=lambda d: first.count(d))
    logger.info("[eu_consolidated] CSV delimiter detected: %r", delim)
    return open(path, "r", encoding="utf-8-sig", errors="replace", newline=""), delim


def _col(row: dict, *candidates: str) -> str:
    """Lookup a value across possible column-name spellings.
    EU schema versions vary on snake_case vs camelCase vs spaces."""
    for c in candidates:
        for key in row:
            if key.strip().lower().replace("_", "").replace(" ", "") == \
               c.lower().replace("_", "").replace(" ", ""):
                v = row[key]
                return (v or "").strip() if isinstance(v, str) else ""
    return ""


def parse_csv(csv_path: str) -> Iterator[dict]:
    """Group rows by Entity_logical_id → yield one canonical dict per entity."""
    fh, delim = _open_csv(csv_path)
    try:
        reader = csv.DictReader(fh, delimiter=delim)
        groups: dict[str, dict] = {}
        for raw in reader:
            ent_id = _col(raw, "Entity_logical_id", "EntityLogicalId",
                          "Entity logical id", "Logical_id", "LogicalId")
            if not ent_id:
                continue
            g = groups.get(ent_id)
            if g is None:
                g = {
                    "source_uid": ent_id,
                    "formatted_name": "",
                    "entity_type": _col(raw, "Subject_type", "SubjectType",
                                       "Subject type", "Entity_type"),
                    "countries": [],
                    "addresses": [],
                    "aliases": [],
                    "programs": [],
                    "designation_at": _col(raw, "Listed_on", "ListedOn",
                                          "Date_of_listing", "Listing_date"),
                    "raw_excerpt_parts": [],
                }
                groups[ent_id] = g

            # Name capture
            whole = _col(raw, "Naal_wholename", "Whole_name", "WholeName",
                         "Name_alias_wholename", "name_alias_whole_name")
            first = _col(raw, "Naal_firstname", "First_name", "FirstName")
            last = _col(raw, "Naal_lastname", "Last_name", "LastName",
                        "Family_name", "FamilyName")
            if not whole and (first or last):
                whole = f"{first} {last}".strip()
            if whole:
                norm = normalise_name(whole)
                alias_type_raw = _col(raw, "Naal_aliastype", "Alias_type",
                                     "AliasType", "Name_alias_type")
                alias_type = "primary" if alias_type_raw.lower() in ("a", "main", "primary", "") else "a.k.a."
                # If we don't have a formatted_name yet, this is our primary
                if not g["formatted_name"] and alias_type == "primary":
                    g["formatted_name"] = whole
                # Add to aliases (deduped)
                if not any(a["formatted"] == whole for a in g["aliases"]):
                    g["aliases"].append({
                        "formatted": whole,
                        "normalised": norm,
                        "alias_type": alias_type,
                    })

            # Country / address capture
            country = _col(raw, "Addr_country", "Address_country",
                          "AddressCountry", "Country_iso2",
                          "Birth_country")
            city = _col(raw, "Addr_city", "City")
            street = _col(raw, "Addr_street", "Street", "AddressLine1")
            if country and country not in g["countries"]:
                g["countries"].append(country)
            full_addr = ", ".join(p for p in [street, city, country] if p)
            if full_addr and full_addr not in g["addresses"]:
                g["addresses"].append(full_addr)

            # Programme
            programme = _col(raw, "Programme", "Sanctions_programme",
                            "RegulationProgramme", "regime")
            if programme and programme not in g["programs"]:
                g["programs"].append(programme)

            # Audit excerpt — R-F527 tightened cap (5×400=2000 → 3×200=600
            # bytes). Reduces peak heap during EU parse from ~12MB to ~4MB.
            if len(g["raw_excerpt_parts"]) < 3:
                g["raw_excerpt_parts"].append(str(raw)[:200])

        # Yield each group as canonical dict
        for ent_id, g in groups.items():
            if not g["formatted_name"]:
                # Fallback to first alias
                if g["aliases"]:
                    g["formatted_name"] = g["aliases"][0]["formatted"]
                else:
                    continue
            yield {
                "source_uid": g["source_uid"],
                "formatted_name": g["formatted_name"],
                "normalised_name": normalise_name(g["formatted_name"]),
                "entity_type": g["entity_type"],
                "countries": g["countries"],
                "addresses": g["addresses"],
                "aliases": g["aliases"],
                "programs": g["programs"],
                "designation_at": g["designation_at"] or None,
                "raw_excerpt": " | ".join(g["raw_excerpt_parts"])[:800],
            }
    finally:
        fh.close()


def load_from_file(csv_path: str) -> int:
    """Parse CSV → replace source rows in store. Returns entries loaded."""
    started = time.time()
    rows_loaded = 0
    success = False
    error = ""
    try:
        rows = list(parse_csv(csv_path))
        rows_loaded = store.replace_source(SOURCE_ID, rows)
        success = True
        logger.info("[eu_consolidated] loaded %d entries from %s", rows_loaded, csv_path)
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
        logger.error("[eu_consolidated] load failed: %s", error)
    finally:
        store.record_refresh(
            SOURCE_ID, started, time.time(), rows_loaded, success, error,
        )
    return rows_loaded
