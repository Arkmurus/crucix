"""R-F2911 — curated real-entity liveness sweep of every company-registry adapter.

WHY THIS EXISTS
    /api/aria/registry/coverage reported 0 live / 27 unproven. That was HONEST, not a
    display bug: registry_coverage derives liveness only from recorded observations
    (registry_coverage.py:182 `_status_for`), and never assumes. But "unproven" is an
    absence, and an absence is not something you can show a customer as coverage.
    The only way to turn absence into evidence is to exercise each adapter against a
    real entity in its own jurisdiction and record what actually happened.

WHAT IT DOES
    Calls `registry_adapters.lookup_entity(name, iso2)` once per jurisdiction with a
    KNOWN-REAL, large, long-lived company registered there. lookup_entity already
    records the outcome to the coverage vault (`_record_coverage_outcome`,
    registry_adapters.py:212), so a successful pass flips that jurisdiction to `live`
    with a timestamp as the evidence.

HONESTY RULES BUILT IN
    * A `None`/empty result is reported as EMPTY, never as failure and never as live.
      The adapter may be perfectly healthy and the entity simply absent (that is what
      Turkey's two prior observations were).
    * An exception is reported as ERROR with the exception text — no swallowing.
    * The probe entity is a real registered company chosen per jurisdiction; if a
      lookup returns a DIFFERENT company, that is surfaced, because a wrong match is
      worse than no match for a due-diligence product.
    * Nothing here writes `live` directly. Liveness is only ever a consequence of a
      real lookup succeeding.

RUN IT ON THE BOX so observations land in the production vault:
    flyctl ssh console -a aria-intel -C "python -m scripts.probe_registry_liveness"
"""
from __future__ import annotations

import asyncio
import json
import sys
import time

# Real, large, long-registered companies — one per jurisdiction. Chosen so a healthy
# adapter should find them; if one of these cannot be found the adapter, the endpoint
# or the query shape is the suspect, not the entity.
PROBES: dict[str, dict] = {
    "AE": {"name": "DP WORLD", "note": "UAE — major state-linked logistics group"},
    "AO": {"name": "SONANGOL", "note": "Angola — state oil company"},
    "BG": {"name": "LUKOIL NEFTOHIM BURGAS", "note": "Bulgaria — largest refinery"},
    # R-F2911 (cont) — IDENTIFIER-BASED adapters cannot be judged by a name.
    # _lookup_brazil starts with `cnpj = _extract_cnpj(...)` and returns None when it
    # finds none; _lookup_romania does the same with a CUI. A name-only probe therefore
    # made a HEALTHY adapter look like a FALLBACK, which is a defect in this instrument,
    # not in the adapter. Verified 2026-07-23: with the CNPJ, BR returns
    # adapter=brazil_cnpj / "PETROLEO BRASILEIRO S A PETROBRAS" / 33.000.167/0001-01.
    #
    # Only identifiers I have actually verified against the live adapter are set here.
    # Guessing a registration number would inject fabricated input and produce a
    # confident wrong verdict — the exact failure this sweep exists to prevent.
    "BR": {"name": "PETROLEO BRASILEIRO S.A.", "reg": "33000167000101",
           "note": "Brazil — Petrobras (CNPJ verified live)"},
    "CH": {"name": "Nestle", "note": "Switzerland — Zefix/LINDAS"},
    "CZ": {"name": "SKODA AUTO", "note": "Czechia"},
    "DE": {"name": "Siemens AG", "note": "Germany"},
    "EE": {"name": "Bolt Technology", "note": "Estonia — ariregister"},
    "FI": {"name": "Nokia Oyj", "note": "Finland — PRH/YTJ"},
    "FR": {"name": "THALES", "note": "France — INSEE/RNE"},
    "GB": {"name": "BAE SYSTEMS PLC", "note": "UK — Companies House"},
    "GH": {"name": "MTN GHANA", "note": "Ghana"},
    "GI": {"name": "GVC HOLDINGS", "note": "Gibraltar"},
    "HU": {"name": "MOL", "note": "Hungary"},
    "IL": {"name": "ELBIT SYSTEMS", "note": "Israel — defence prime"},
    "IN": {"name": "RELIANCE INDUSTRIES LIMITED", "note": "India — MCA"},
    "KE": {"name": "SAFARICOM", "note": "Kenya"},
    "NG": {"name": "DANGOTE CEMENT", "note": "Nigeria — CAC"},
    "NO": {"name": "EQUINOR ASA", "note": "Norway — Brreg"},
    "PA": {"name": "COPA HOLDINGS", "note": "Panama"},
    "PL": {"name": "ORLEN", "note": "Poland — KRS"},
    "RO": {"name": "OMV PETROM", "note": "Romania"},
    "SA": {"name": "SAUDI ARAMCO", "note": "Saudi Arabia"},
    "SK": {"name": "SLOVNAFT", "note": "Slovakia — RPO"},
    "TR": {"name": "ASELSAN", "note": "Turkey — defence electronics"},
    "US": {"name": "Lockheed Martin Corporation", "address": "Bethesda, Maryland",
           "note": "US — state SoS routing needs an address"},
    "ZA": {"name": "SASOL LIMITED", "note": "South Africa — CIPC"},
}

_TIMEOUT_S = 45.0


async def _probe_one(iso2: str, spec: dict) -> dict:
    from aria_service.intel import registry_adapters as ra

    started = time.monotonic()
    row = {"iso2": iso2, "query": spec["name"], "note": spec.get("note", ""),
           "probe_input": "name+id" if spec.get("reg") else "name-only"}
    try:
        result = await asyncio.wait_for(
            ra.lookup_entity(spec["name"], iso2, spec.get("reg"), spec.get("address")),
            timeout=_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        row.update(outcome="ERROR", detail=f"timeout after {_TIMEOUT_S:.0f}s")
    except Exception as exc:                       # never swallow — the text is the evidence
        row.update(outcome="ERROR", detail=f"{type(exc).__name__}: {str(exc)[:160]}")
    else:
        if not result:
            # Working-but-found-nothing. NOT a failure and NOT liveness.
            if not spec.get("reg"):
                # R-F2911 (cont) — several adapters require a registration number
                # and return None without one. Reporting that as a verdict on the
                # adapter would be wrong; say what was actually tested.
                row.update(outcome="EMPTY",
                           detail="no match for a NAME-ONLY probe — inconclusive if this "
                                  "adapter requires a registration number")
            else:
                row.update(outcome="EMPTY", detail="no match even with a verified identifier")
        else:
            profile = (result or {}).get("profile") or {}
            found = str(profile.get("company_name") or profile.get("name") or "?")
            answering = str(result.get("adapter") or "")
            row.update(
                adapter=answering,
                source_url=str(result.get("source_url") or "")[:110],
                officers=len(result.get("officers") or []),
            )
            # CRITICAL — lookup_entity falls back to the GLEIF global index when the
            # NATIONAL adapter finds nothing (registry_adapters.py:201). A GLEIF match
            # therefore proves GLEIF is reachable; it proves NOTHING about the national
            # registry, which is what this jurisdiction's row on vault.html claims.
            # Counting it as LIVE would manufacture exactly the false coverage this
            # sweep exists to eliminate. The product already records it honestly
            # (`empty` is written for the native adapter BEFORE the fallback runs) —
            # this report must agree with the vault, not flatter it.
            if answering.lower().startswith("gleif"):
                row.update(
                    outcome="FALLBACK",
                    detail=f"NATIVE REGISTRY FOUND NOTHING — GLEIF answered ({found[:44]})",
                )
            elif answering.endswith("_stub"):
                # R-F2915 — a stub does not read a registry. It echoes the QUERY back
                # as company_name and attaches data_gaps saying no public API exists.
                # Counting that as liveness would mean treating ARIA quoting itself
                # back as proof a national registry answered. It is a real DD outcome
                # (the report shows the gap) but it is NOT coverage.
                row.update(
                    outcome="STUB",
                    detail=f"NO REGISTRY READ — stub echoed the query ({answering})",
                )
            else:
                row.update(outcome="LIVE", detail=f"matched: {found[:70]}")
    row["elapsed_s"] = round(time.monotonic() - started, 1)
    return row


async def main() -> int:
    only = {a.upper() for a in sys.argv[1:] if len(a) == 2}
    targets = {k: v for k, v in PROBES.items() if not only or k in only}
    print(f"[R-F2911] probing {len(targets)} registry adapters with real entities\n")

    rows = []
    for iso2, spec in sorted(targets.items()):
        row = await _probe_one(iso2, spec)
        rows.append(row)
        mark = {"LIVE": "LIVE ", "EMPTY": "EMPTY", "ERROR": "ERROR", "FALLBACK": "FBACK", "STUB": "STUB "}[row["outcome"]]
        print(f"{mark} {iso2}  {row['elapsed_s']:>5}s  {row['query'][:30]:32} {row['detail'][:78]}")
        await asyncio.sleep(1.0)          # be a good citizen to public registries

    live = [r for r in rows if r["outcome"] == "LIVE"]
    fb = [r for r in rows if r["outcome"] == "FALLBACK"]
    stub = [r for r in rows if r["outcome"] == "STUB"]
    empty = [r for r in rows if r["outcome"] == "EMPTY"]
    err = [r for r in rows if r["outcome"] == "ERROR"]
    print("\n" + "=" * 72)
    print(f"LIVE  national registry answered: {len(live)}/{len(rows)}  -> {[r['iso2'] for r in live]}")
    print(f"FBACK GLEIF answered, NATIVE DID NOT (proves nothing about the registry): "
          f"{len(fb)}  -> {[r['iso2'] for r in fb]}")
    print(f"STUB  no registry read, query echoed back (NOT coverage): "
          f"{len(stub)}  -> {[r['iso2'] for r in stub]}")
    print(f"EMPTY adapter ran, no match:      {len(empty)}  -> {[r['iso2'] for r in empty]}")
    print(f"ERROR adapter or endpoint:        {len(err)}  -> {[r['iso2'] for r in err]}")
    print("=" * 72)
    for r in err:
        print(f"  {r['iso2']}: {r['detail']}")
    print("\nJSON:" )
    print(json.dumps(rows, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
