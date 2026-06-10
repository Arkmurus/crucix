"""Audit what data sources are actually working — not what the vault claims."""
import urllib.request
import json
import ssl

ctx = ssl.create_default_context()
base = 'https://aria-intel.fly.dev'

print("=== WHAT'S ACTUALLY WORKING ===")
print()

# 1. Check brain stats for sanctions sources
print("--- SANCTIONS SOURCES (from brain stats) ---")
d = json.loads(urllib.request.urlopen(base + '/api/aria/brain/stats', context=ctx, timeout=15).read())
modules = d.get('modules', {})

sanctions_sources = [
    'sanctions_canonical.lookup',
    'sources.ofac_sdn',
    'sources.fcdo_sanctions',
    'sources.un_sc_sanctions',
    'sources.worldbank_debarred',
    'sources.acled',
    'sources.sec_edgar',
]
for src in sanctions_sources:
    m = modules.get(src)
    if m:
        rate = m.get('success_rate', 0)
        total = m.get('total', 0)
        last = m.get('last_signal_ago_h', '?')
        print(f"  {src}: {total} calls, {rate:.0%} success, last={last}h ago")
    else:
        print(f"  {src}: NOT FOUND in brain stats")

print()

# 2. Check what the DD orchestrator actually uses
print("--- DD ORCHESTRATOR LAYERS ---")
print("  The 7-layer orchestrator uses these sources internally:")
print("  - sanctions.screen_with_aliases (OpenSanctions)")
print("  - sources.ofac_sdn (OFAC SDN list)")
print("  - sources.fcdo_sanctions (UK OFSI)")
print("  - sources.un_sc_sanctions (UN SC)")
print("  - sources.worldbank_debarred (World Bank)")
print("  - sources.acled (ACLED)")
print("  - sources.sec_edgar (SEC EDGAR)")
print("  - companies_house (UK Companies House)")
print("  - web_search (Brave/Google/Bing)")
print("  - news_monitor (RSS feeds)")
print()

# 3. Check what intelligence feeds are running
print("--- INTELLIGENCE FEEDS (from web_integrity cycles) ---")
wi = modules.get('web_integrity', {})
print(f"  web_integrity: {wi.get('total', 0)} cycles, {wi.get('success_rate', 0):.0%} success")
print()

# 4. Check the vault API for what it actually stores
print("--- VAULT (what it actually stores) ---")
r = urllib.request.urlopen(base + '/api/aria/vault', context=ctx, timeout=15)
vault_data = json.loads(r.read().decode())
entries = vault_data.get('entries', [])
print(f"  Total entries: {len(entries)}")
by_status = {}
for e in entries:
    s = e['status']
    by_status.setdefault(s, []).append(e)
for status, items in sorted(by_status.items()):
    print(f"  {status}: {len(items)}")
    for item in items[:3]:
        print(f"    {item['site_id']}: {item.get('notes','')[:80]}")
    if len(items) > 3:
        print(f"    ... and {len(items)-3} more")

print()
print("=== REALITY ===")
print("The vault is a wishlist, not a registry of working data sources.")
print("The DD orchestrator works because it uses free/open APIs and")
print("the OpenSanctions aggregate, NOT because of vault registrations.")
print("The vault's 'registered' entries are fabricated by import_open_portals")
print("which marks registration_type='none' portals as 'registered' even")
print("though no registration ever happened.")
