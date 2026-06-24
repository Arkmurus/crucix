"""Check vault state."""
import json
import urllib.request

req = urllib.request.Request(
    'https://aria-intel.fly.dev/api/aria/vault?limit=5',
    headers={'Authorization': 'Bearer temp-token-123'}
)
r = urllib.request.urlopen(req, timeout=15)
data = json.loads(r.read())
entries = data.get('entries', [])
for e in entries[:3]:
    print(f"{e['site_id']}: status={e['status']}, notes={e.get('notes','')[:80]}")
