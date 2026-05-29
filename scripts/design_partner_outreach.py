#!/usr/bin/env python
"""R-F1079 — Design Partner Outreach Tool (Phase A Gate #7).

Generates structured outreach materials for design partner conversations.
Tracks outreach status in a local JSON file so the operator can see progress.

Usage:
    python scripts/design_partner_outreach.py list          # Show current status
    python scripts/design_partner_outreach.py add <name> <domain> <notes>
    python scripts/design_partner_outreach.py generate      # Generate outreach templates
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_FILE = DATA_DIR / "design_partner_pipeline.json"

# Target: 4+ design partner conversations underway (Phase A Gate #7)
# Suggested domains for defence-compliance design partners:
SUGGESTED_TARGETS = [
    {
        "name": "Defence compliance consultancy (UK)",
        "domain": "compliance",
        "why": "Existing Arkmurus network — compliance officers who understand DD pain points",
        "priority": 1,
    },
    {
        "name": "Defence broker / intermediary (Lusophone Africa)",
        "domain": "broking",
        "why": "Lusophone moat — they need Portuguese-language DD that competitors don't offer",
        "priority": 1,
    },
    {
        "name": "Export control lawyer / firm",
        "domain": "legal",
        "why": "ITAR/EAR/SITCL advisory firms who could white-label ARIA DD reports",
        "priority": 2,
    },
    {
        "name": "MoD procurement advisor (former)",
        "domain": "procurement",
        "why": "Understands the buyer side — can validate ARIA's DD methodology against real procurement needs",
        "priority": 2,
    },
    {
        "name": "Defence industry association (ADS / SBAC / similar)",
        "domain": "industry",
        "why": "Access to member companies who need DD services",
        "priority": 3,
    },
    {
        "name": "Risk intelligence platform (competing/complementary)",
        "domain": "platform",
        "why": "Integration partnership — ARIA DD as a module within their platform",
        "priority": 3,
    },
]


def _load() -> list[dict[str, Any]]:
    """Load the design partner pipeline."""
    if DATA_FILE.exists():
        try:
            with open(DATA_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return []
    return []


def _save(entries: list[dict[str, Any]]) -> None:
    """Save the design partner pipeline."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(DATA_FILE, "w") as f:
        json.dump(entries, f, indent=2, default=str)


def cmd_list() -> None:
    """List current design partner pipeline status."""
    entries = _load()
    if not entries:
        print("No design partner entries yet. Add one with:")
        print("  python scripts/design_partner_outreach.py add <name> <domain> <notes>")
        print()
        print("Suggested targets:")
        for t in SUGGESTED_TARGETS:
            print(f"  [P{t['priority']}] {t['name']} ({t['domain']})")
            print(f"         {t['why']}")
        return

    print(f"Design Partner Pipeline ({len(entries)} entries)")
    print("=" * 60)
    for i, e in enumerate(entries, 1):
        status_icon = {
            "identified": "[ID]",
            "contacted": "[CT]",
            "conversation": "[CV]",
            "interested": "[OK]",
            "onboarded": "[GO]",
            "declined": "[NO]",
        }.get(e.get("status", "identified"), "[??]")
        print(f"{status_icon} {i}. {e['name']}")
        print(f"   Domain: {e.get('domain', '?')}")
        print(f"   Status: {e.get('status', 'identified')}")
        print(f"   Notes:  {e.get('notes', '')}")
        if e.get("contacted_at"):
            print(f"   Contacted: {e['contacted_at']}")
        print()


def cmd_add(name: str, domain: str, notes: str) -> None:
    """Add a design partner entry."""
    entries = _load()
    # Check for duplicates
    for e in entries:
        if e["name"].lower() == name.lower():
            print(f"Entry already exists for '{name}'")
            return
    entries.append({
        "name": name,
        "domain": domain,
        "status": "identified",
        "notes": notes,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "contacted_at": None,
    })
    _save(entries)
    print(f"Added: {name} ({domain})")
    print(f"Pipeline now has {len(entries)} entries — target is 4+ for Phase A Gate #7")


def cmd_generate() -> None:
    """Generate outreach templates for all identified entries."""
    entries = _load()
    identified = [e for e in entries if e.get("status") == "identified"]
    if not identified:
        print("No identified entries to generate templates for.")
        return

    for e in identified:
        print(f"\n{'=' * 60}")
        print(f"OUTREACH TEMPLATE: {e['name']}")
        print(f"{'=' * 60}")
        print(f"""
Subject: Arkmurus ARIA — design partner opportunity

Hi [Name],

I'm reaching out because [connection/referral context].

Arkmurus has built ARIA — an AI platform purpose-built for defence due
diligence. It covers 10 DD layers (sanctions, UBO, export control,
deception detection, commercial coherence, etc.) across 188+ sources
in 11 languages, with a particular strength in Lusophone/MENA markets.

We're looking for 3-5 design partners to:
- Use ARIA for real DD work (free during pilot)
- Give structured feedback on methodology, output quality, and gaps
- Shape the product roadmap before general availability

For a compliance/DD professional, this means:
- Your team gets free, structured DD reports for 90 days
- You influence how defence DD AI should work
- Early access to a platform that's methodology-published and
  constitution-governed (23-clause honesty constitution)

Would you be open to a 30-min call to explore whether this fits?

Best,
[Your name]
Arkmurus
""")
        print(f"{'=' * 60}\n")


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1]
    if cmd == "list":
        cmd_list()
    elif cmd == "add":
        if len(sys.argv) < 5:
            print("Usage: python scripts/design_partner_outreach.py add <name> <domain> <notes>")
            sys.exit(1)
        cmd_add(sys.argv[2], sys.argv[3], sys.argv[4])
    elif cmd == "generate":
        cmd_generate()
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
