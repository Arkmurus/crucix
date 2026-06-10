"""Probe the live vault — check what's in it and why agents aren't populating it."""
import urllib.request
import json
import ssl

ctx = ssl.create_default_context()
base = 'https://aria-intel.fly.dev'

def fetch(path):
    r = urllib.request.urlopen(base + path, context=ctx, timeout=15)
    return json.loads(r.read().decode())

print("=== VAULT STATUS (LIVE) ===")
data = fetch('/api/aria/vault')
print(f"Success: {data.get('success')}")
print(f"Entries: {len(data.get('entries', []))}")
stats = data.get('stats', {})
print(f"Stats: {json.dumps(stats, indent=2)}")
print()

print("=== ALL ENTRIES ===")
for e in data.get('entries', []):
    print(f"  [{e['status']:10s}] {e['site_id']:30s} agent={e['agent_id']:20s} type={e.get('site_type','?'):10s} notes={str(e.get('notes',''))[:60]}")

print()
print("=== BY STATUS ===")
by_status = {}
for e in data.get('entries', []):
    s = e['status']
    by_status.setdefault(s, []).append(e['site_id'])
for status, sites in sorted(by_status.items()):
    print(f"  {status}: {len(sites)} entries")
    for s in sites[:5]:
        print(f"    - {s}")
    if len(sites) > 5:
        print(f"    ... and {len(sites)-5} more")

print()
print("=== BY AGENT ===")
by_agent = {}
for e in data.get('entries', []):
    a = e['agent_id']
    by_agent.setdefault(a, []).append(e['site_id'])
for agent, sites in sorted(by_agent.items()):
    print(f"  {agent}: {len(sites)} entries")
    for s in sites[:3]:
        print(f"    - {s}")
    if len(sites) > 3:
        print(f"    ... and {len(sites)-3} more")
