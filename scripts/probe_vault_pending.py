"""Probe why vault entries stay pending — trace the full signup flow."""
import urllib.request
import json
import ssl

ctx = ssl.create_default_context()
base = 'https://aria-intel.fly.dev'

print("=== VAULT STATUS (LIVE) ===")
r = urllib.request.urlopen(base + '/api/aria/vault', context=ctx, timeout=15)
data = json.loads(r.read().decode())
print(f"Total entries: {len(data.get('entries', []))}")
stats = data.get('stats', {})
print(f"Stats: {json.dumps(stats, indent=2)}")
print()

print("=== ENTRIES BY STATUS ===")
by_status = {}
for e in data.get('entries', []):
    s = e['status']
    by_status.setdefault(s, []).append(e)
for status, entries in sorted(by_status.items()):
    print(f"  {status}: {len(entries)}")
    for e in entries[:5]:
        print(f"    {e['site_id']}: agent={e['agent_id']}, notes={str(e.get('notes',''))[:100]}")
    if len(entries) > 5:
        print(f"    ... and {len(entries)-5} more")

print()
print("=== PENDING ENTRIES (full detail) ===")
pending = by_status.get('pending', [])
print(f"Total pending: {len(pending)}")
for e in pending:
    print(f"  {e['site_id']}:")
    print(f"    name: {e.get('site_name','?')}")
    print(f"    url: {e.get('site_url','?')}")
    print(f"    agent: {e.get('agent_id','?')}")
    print(f"    type: {e.get('site_type','?')}")
    print(f"    created: {e.get('created_at','?')}")
    print(f"    notes: {e.get('notes','?')}")
    print()
