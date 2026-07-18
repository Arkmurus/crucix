"""Analyse the DD Reports page — understand the pipeline tools and their value."""
import urllib.request
import ssl
import re

ctx = ssl.create_default_context()
web = 'https://imaria.io'

r = urllib.request.urlopen(web + '/dd-reports.html', context=ctx, timeout=15)
html = r.read().decode('utf-8', errors='replace')

print("=" * 60)
print("DD REPORTS PAGE ANALYSIS")
print("=" * 60)

# 1. Reports library section
print("\n--- 1. REPORTS LIBRARY ---")
if 'No DD reports yet' in html:
    print("  STATUS: Empty — no DD reports exist yet")
    print("  The reports library shows past DD runs. Currently empty because")
    print("  no DD has been run via the web UI yet.")
else:
    print("  Reports exist")

# 2. Pipeline tools
print("\n--- 2. PIPELINE TOOLS (12 deterministic primitives) ---")
print()

# Extract tools from the JavaScript TOOLS array
tools_match = re.search(r'const TOOLS = (\[.*?\]);', html, re.DOTALL)
tools = []
if tools_match:
    tools_js = tools_match.group(1)
    # Parse each tool object manually (JS syntax, not valid JSON)
    tool_objs = re.findall(r'\{([^}]+)\}', tools_js)
    for obj in tool_objs:
        name_m = re.search(r"name:\s*'([^']+)'", obj)
        rfn_m = re.search(r"rfn:\s*'([^']+)'", obj)
        desc_m = re.search(r"desc:\s*'([^']+)'", obj)
        endpoint_m = re.search(r"endpoint:\s*function\([^)]+\)\s*\{[^}]*return\s+'([^']+)'", obj)
        if name_m:
            tools.append({
                'name': name_m.group(1),
                'rfn': rfn_m.group(1) if rfn_m else '?',
                'desc': desc_m.group(1) if desc_m else '?',
                'endpoint': endpoint_m.group(1) if endpoint_m else '?',
            })

print(f"Found {len(tools)} tools\n")

for i, t in enumerate(tools, 1):
    print(f"  {i:2d}. [{t['rfn']}] {t['name']}")
    print(f"     {t['desc'][:150]}")
    print(f"     Endpoint: {t['endpoint'][:100]}")
    print()

# 3. Value analysis
print("\n--- 3. VALUE TO DD DUE DILIGENCE ---")
print("""
Each tool serves a specific DD purpose:

SANCTIONS & SCREENING:
  - Sanctions Divergence (R-F68): Cross-list sanctions lookup — checks an entity
    against US/UK/EU/UN/CA/CH/AU lists. Core DD function.
  - RCA / Relatives (R-F76): FATF Rec 12 — recursive screening through sanctions
    relationships. Catches family/associate links.
  - Crypto Wallet Screen (R-F74): Screens wallet addresses against sanctions lists.
    Essential for crypto/TBML DD.

FINANCIAL CRIME & TYPOLOGY:
  - FATF Typology Match (R-F72): Scores a profile against 8 FATF ML/TBML typologies.
    Flags high-risk patterns (BVI + undisclosed UBO + USDT = red flag).
  - TBML Classifier (R-F73): Trade-Based Money Laundering detection — compares
    declared invoice value against benchmark ranges.
  - Benford's Law (R-F70): Forensic accounting — flags fabricated financial figures.
    Requires 50+ values.

ENTITY VERIFICATION:
  - Economic Substance (R-F77): OECD BEPS/FATF substance test — distinguishes
    real operating companies from shell/front entities.
  - Citation Audit (R-F78): Verifies cited claims against actual source content.
    Returns citation_grounded_rate. Essential for LLM hallucination detection.

INTELLIGENCE & SECURITY:
  - Counter-Intelligence Scan (R-F84): Detects reputation washing, news-outlet
    bursts, credibility anomalies. Flags propaganda/astroturfing.
  - Provenance Lineage (R-F75): Walks backwards from a knowledge node to find
    every source that contributed. Audit trail for DD findings.

SYSTEM DIAGNOSTIC:
  - Prompt-Injection Grade (R-F80): Grades chat responses against OWASP LLM01
    attack patterns. Security audit tool.
  - Tier Router (R-F87a): Diagnostic — explains which LLM tier each intent maps to.

GAPS & OBSERVATIONS:
  1. Tools are standalone HTML forms — they POST to individual API endpoints but
     there's no unified DD pipeline that chains them together.
  2. Results are displayed inline but NOT saved to the DD reports library.
  3. No way to run a full multi-layer DD from this page — it's a toolbox, not
     an orchestrator.
  4. The 'New DD' button and chat-based DD ('screen Acme Defence GmbH') are the
     intended paths for full DD runs, but the reports library is empty because
     those paths haven't been used yet.
""")
