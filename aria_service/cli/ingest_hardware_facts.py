"""Extract key facts from the ARIA Military Hardware Library and store them
in the knowledge base via knowledge.store_fact().

This complements the full RAG corpus ingest (ingest_corpus CLI) by distilling
the ~80 most important market-actionable assertions into discrete knowledge
facts. These surface faster (layer 4 — Knowledge) and benefit from
contradiction detection when future intel updates arrive.

Usage
=====
    # Against deployed service
    python -m aria_service.cli.ingest_hardware_facts \
        --aria-url https://aria-intel.fly.dev \
        --token $ARIA_INT_TOKEN

    # Locally (requires Redis + ARIA service running)
    python -m aria_service.cli.ingest_hardware_facts
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

# ── Key facts extracted from hardware library ─────────────────────────────
# Each tuple: (topic, content, confidence)
# Source is always "corpus:tier_b+:hardware_library"
HARDWARE_FACTS = [
    # ── CPLP Force Equipment Inventory ─────────────────────────────────
    ("Angola small arms inventory",
     "Angola's FAA primarily operates AKM/AK-47 variants as standard infantry weapons, inherited from Soviet/Cuban supply during the civil war.",
     "CONFIRMED"),
    ("Mozambique small arms inventory",
     "Mozambique's FADM operates AKM/AK-47 variants as primary infantry weapons; AK-74 variants also in service.",
     "CONFIRMED"),
    ("Guinea-Bissau small arms inventory",
     "Guinea-Bissau armed forces operate Soviet-legacy small arms including AKM variants and AK-74 variants.",
     "CONFIRMED"),
    ("Angola armoured vehicle fleet",
     "Angola operates T-54/55 and T-72 tank variants; upgrade and sustainment of these platforms is an ongoing procurement opportunity.",
     "CONFIRMED"),
    ("Angola IFV fleet",
     "Angola operates BMP-1/2 IFV variants; upgrade programmes are live procurement opportunities.",
     "CONFIRMED"),
    ("Angola BTR fleet",
     "Angola operates BTR-80/82A wheeled APCs; these are common across CPLP African states.",
     "CONFIRMED"),
    ("Mozambique BTR fleet",
     "Mozambique operates BTR-80/82A wheeled APCs alongside Soviet-legacy armoured vehicles.",
     "CONFIRMED"),
    ("Angola aviation fleet",
     "Angola operates Su-27/30 fighter variants; maintenance, upgrade advisory, and pilot training represent key BD opportunities.",
     "CONFIRMED"),
    ("Angola Yak-130 fleet",
     "Angola operates Yak-130 advanced jet trainers; maintenance and upgrade represent an active market opportunity.",
     "CONFIRMED"),
    ("Angola artillery inventory",
     "Angola operates Soviet-legacy 122mm systems including D-30 howitzers and BM-21 Grad MLRS; ammunition procurement is a consistent market.",
     "CONFIRMED"),
    ("Mozambique artillery inventory",
     "Mozambique operates D-30 122mm howitzers; ammunition procurement is a consistent market.",
     "CONFIRMED"),

    # ── CPLP Air Defence ───────────────────────────────────────────────
    ("CPLP African ZU-23-2 operations",
     "Every CPLP African military operates ZU-23-2 twin 23mm anti-aircraft systems; ammunition procurement for these is consistent and significant.",
     "CONFIRMED"),
    ("Angola Strela MANPADS legacy",
     "Strela-2 (SA-7) MANPADS were extensively used in the Angolan civil war; stockpiles remain and present a proliferation risk to non-state actors.",
     "CONFIRMED"),

    # ── Ammunition Supply Chain ────────────────────────────────────────
    ("5.45mm ammunition supply disruption",
     "5.45x39mm ammunition supply to CPLP African forces is significantly disrupted post-2022 due to Western sanctions on Russia, the primary producer.",
     "CONFIRMED"),
    ("7.62x54mmR ammunition supply risk",
     "7.62x54mmR ammunition (used in SVD Dragunov, PKM) is primarily Russian-produced; sanctions impact procurement for CPLP African forces using Soviet-legacy weapons.",
     "CONFIRMED"),
    ("152mm ammunition supply risk",
     "152mm artillery ammunition is increasingly difficult to procure post-2022; clients with Soviet-legacy 152mm systems face significant supply risk.",
     "CONFIRMED"),
    ("CBC Brazil 5.56mm supplier",
     "CBC Brazil is a key supplier of 5.56x45mm NATO ammunition for Portuguese-speaking markets, enabling procurement without ITAR constraints.",
     "CONFIRMED"),
    ("RPG-7 in CPLP Africa",
     "RPG-7 is standard in ALL CPLP African forces; warhead and launcher ammunition procurement is a consistent and significant market opportunity.",
     "CONFIRMED"),
    ("122mm most common African artillery calibre",
     "122mm remains the most common artillery calibre in African conflicts; Angola, Mozambique, Guinea-Bissau all operate Soviet-legacy 122mm systems.",
     "CONFIRMED"),

    # ── Market Opportunities ───────────────────────────────────────────
    ("Mozambique Cabo Delgado capability needs",
     "Mozambique Cabo Delgado counter-insurgency requires: (1) tactical ISR via small UAS, (2) MRAP armoured mobility for mine/IED threat, (3) maritime patrol OPVs, (4) SOF capability.",
     "CONFIRMED"),
    ("Cape Verde maritime BD opportunity",
     "Cape Verde maritime domain awareness is a consistent BD opportunity — island state requiring EEZ patrol vessels; Damen StanPatrol series is most widely exported OPV to Sub-Saharan Africa.",
     "ASSESSED"),
    ("Angola naval aspirations",
     "Angola is the largest African naval power aspirant; missile corvette/frigate procurement (cost $200M-$800M per platform) involves significant G2G diplomacy.",
     "ASSESSED"),
    ("Super Tucano African market importance",
     "Embraer A-29 Super Tucano is the most important light attack aircraft for African market BD — Brazilian manufacture avoids ITAR entirely. Angola, Mozambique, Cape Verde are candidate markets.",
     "CONFIRMED"),
    ("H225M Caracal non-ITAR option",
     "Brazilian-built H225M Caracal variant provides a non-ITAR option for heavy tactical helicopter procurement in CPLP market context.",
     "CONFIRMED"),

    # ── South African Supply Advantage ─────────────────────────────────
    ("South Africa no-ITAR supplier",
     "South African manufacturers (Denel, BAE Systems SA) are significant suppliers for CPLP African markets as South African-origin equipment avoids ITAR constraints.",
     "CONFIRMED"),
    ("G6 Rhino South African artillery",
     "G6 Rhino 155mm wheeled SPH is South African designed/manufactured; relevant for CPLP market procurement as no ITAR applies. Denel supply chain.",
     "CONFIRMED"),
    ("RG-31 Nyala for CPLP markets",
     "RG-31 Nyala MRAP (BAE Systems South Africa) is significant for CPLP markets as South African manufacture avoids ITAR constraints.",
     "CONFIRMED"),
    ("G7 105mm for CPLP",
     "South African G7 105mm howitzer is available for Portuguese-speaking market procurement without US ITAR controls.",
     "CONFIRMED"),

    # ── Drone Market Dynamics ──────────────────────────────────────────
    ("Bayraktar TB2 African market",
     "Bayraktar TB2 is the most important drone for African market BD — affordable ($1-5M), combat-proven (Libya, Nagorno-Karabakh, Ukraine), available without ITAR constraint. Burkina Faso, Mali, Ethiopia, Togo have procured/discussed.",
     "CONFIRMED"),
    ("MQ-9 Reaper Africa unavailable",
     "MQ-9 Reaper availability to African states is essentially zero through legitimate channels due to ITAR restrictions; this creates an alternative market for Turkish and Chinese UCAVs.",
     "CONFIRMED"),
    ("Wing Loong II China Africa proliferation",
     "China is filling the MALE UCAV gap left by US ITAR restrictions with Wing Loong II ($1-2M); proliferation to Africa (Egypt, Ethiopia, Nigeria) is a significant geopolitical trend.",
     "CONFIRMED"),
    ("FPV drone revolution",
     "Ukraine conflict demonstrates FPV drones ($400-$2,000) used in millions as primary ISR and improvised attack tool; $1,000 drones destroying $3M vehicles.",
     "CONFIRMED"),

    # ── Export Control Key Facts ───────────────────────────────────────
    ("ITAR brokering SITCL trigger",
     "Under 22 CFR Part 129, any person brokering ITAR-controlled items must register with DDTC; this directly triggers UK SITCL requirement if Arkmurus is brokering ITAR-origin items.",
     "CONFIRMED"),
    ("MANPADS highest non-proliferation priority",
     "MANPADS (all types) are the highest priority non-proliferation item due to anti-civil-aviation threat; ML4 classification with additional controls beyond standard Wassenaar categories.",
     "CONFIRMED"),
    ("Chinese arms no Wassenaar restriction",
     "Chinese military exports (MLRS, UCAVs, anti-ship missiles) have no ITAR/Wassenaar restriction; China uses arms sales as a geopolitical tool in Africa.",
     "CONFIRMED"),
    ("F-35 most politically sensitive export",
     "F-35 Lightning II is the most politically sensitive US arms export; non-JSF-partner nation access is restricted. Understanding procurement constraints is essential for BD advisory.",
     "CONFIRMED"),
    ("Patriot politically sensitive",
     "Patriot PAC-3 procurement is one of the most politically sensitive US arms exports; only close allies (Germany, Japan, Netherlands, South Korea, Israel, Saudi Arabia, UAE, etc.) have access.",
     "CONFIRMED"),

    # ── Combat Performance Lessons ─────────────────────────────────────
    ("Ukraine T-72 losses record",
     "Ukraine conflict: 3,000+ T-72 variants destroyed/captured (Oryx-verified), highest verified loss of any single tank type in modern warfare.",
     "CONFIRMED"),
    ("Bradley vs BMP survivability",
     "Ukraine conflict: Bradley IFV (<20 destroyed) demonstrated far better crew survivability than BMP-2 (500+ destroyed); separated ammunition vs carousel autoloader is the key difference.",
     "CONFIRMED"),
    ("HIMARS zero verified losses",
     "Ukraine conflict: HIMARS achieved zero verified losses despite Russian claims; precision strikes on logistics and command nodes transformed the conflict.",
     "CONFIRMED"),
    ("CPLP vulnerability to drone attack",
     "Nagorno-Karabakh lesson: unmodernised Soviet-era armour without air defence is catastrophically vulnerable to precision drone attack. CPLP African forces operate this exact vulnerability profile.",
     "CONFIRMED"),
    ("Angolan civil war equipment legacy",
     "FAA (current Angolan armed forces) is the direct successor to FAPLA and retains much of the same Soviet-era equipment from the civil war (T-54/55, BMP-1, BTR-60, MiG-21/23). Understanding this history is essential for maintenance/upgrade advisory.",
     "CONFIRMED"),
    ("Wheeled over tracked in Sahel",
     "French Sahel operations (2013-2022) demonstrated strategic mobility (wheeled vehicles over tracked) is critical for African operational environments.",
     "CONFIRMED"),

    # ── Pricing Reference Points ───────────────────────────────────────
    ("T-72B3 unit cost",
     "T-72B3 tank unit cost approximately $1.5-2M; relevant baseline for CPLP African armour procurement/upgrade advisory.",
     "ASSESSED"),
    ("Leopard 2A7 unit cost",
     "Leopard 2A7+ tank unit cost approximately $10-13M; NATO standard export tank requiring German export licence.",
     "ASSESSED"),
    ("Javelin missile unit cost",
     "FGM-148 Javelin ATGM costs approximately $176,000 per missile plus $240,000 per CLU; ITAR controlled with significant backlog post-Ukraine.",
     "ASSESSED"),
    ("GMLRS round unit cost",
     "GMLRS (Guided MLRS) round costs approximately $168,000; GPS+INS guided with 70km range and CEP 5-10m.",
     "ASSESSED"),
    ("OPV cost range",
     "Offshore Patrol Vessels (50-90m class) cost $15-60M depending on specification; most relevant vessel type for CPLP African maritime procurement.",
     "ASSESSED"),
]


def _post(url: str, body: dict, token: str, timeout: int = 30) -> tuple[int, dict | str]:
    """POST JSON to ARIA /api/aria/teach endpoint."""
    import json as _json
    data = _json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}" if token else "",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.status, _json.loads(raw)
            except Exception:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        body_text = ""
        try:
            body_text = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        return e.code, body_text or str(e)
    except Exception as e:
        return -1, str(e)


def main(argv: list[str] | None = None) -> int:
    import argparse
    p = argparse.ArgumentParser(
        prog="python -m aria_service.cli.ingest_hardware_facts",
        description="Distil key military hardware facts into ARIA's knowledge base.",
    )
    p.add_argument("--aria-url",
                   default=os.getenv("ARIA_SERVICE_URL", "http://localhost:8000"),
                   help="Base URL of the ARIA service")
    p.add_argument("--token",
                   default=os.getenv("ARIA_API_TOKEN", "") or os.getenv("ARIA_INT_TOKEN", ""),
                   help="Bearer token for authentication")
    p.add_argument("--dry-run", action="store_true",
                   help="Print facts without sending")
    args = p.parse_args(argv)

    source = "corpus:tier_b+:hardware_library"
    endpoint = args.aria_url.rstrip("/") + "/api/aria/knowledge/fact"

    print(f"Hardware Library Fact Extraction")
    print(f"Total facts: {len(HARDWARE_FACTS)}")
    print(f"Target: {endpoint}")
    print()

    if args.dry_run:
        for i, (topic, content, conf) in enumerate(HARDWARE_FACTS, 1):
            print(f"  [{i:2d}] [{conf}] {topic}")
            print(f"       {content[:100]}...")
            print()
        return 0

    n_ok = 0
    n_fail = 0
    for i, (topic, content, confidence) in enumerate(HARDWARE_FACTS, 1):
        body = {
            "topic": topic,
            "content": content,
            "source": source,
            "confidence": confidence,
        }
        status, resp = _post(endpoint, body, args.token)
        if status == 200:
            action = resp.get("action", "?") if isinstance(resp, dict) else "?"
            print(f"  [{i:2d}/{len(HARDWARE_FACTS)}] {topic} — {action}")
            n_ok += 1
        else:
            print(f"  [{i:2d}/{len(HARDWARE_FACTS)}] {topic} — FAIL (HTTP {status})")
            n_fail += 1

    print()
    print(f"Done. {n_ok} succeeded, {n_fail} failed.")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
