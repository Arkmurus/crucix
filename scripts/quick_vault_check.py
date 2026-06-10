"""Quick vault integrity check."""
import urllib.request, json, ssl, time
ctx = ssl.create_default_context()
base = 'https://aria-intel.fly.dev'

def fetch(path):
    for i in range(3):
        try:
            r = urllib.request.urlopen(base + path, context=ctx, timeout=15)
            return json.loads(r.read().decode())
        except Exception as e:
            if i < 2:
                time.sleep(5)
            else:
                return {'_error': str(e)}

d = fetch('/api/aria/vault')
entries = d.get('entries', [])
stats = d.get('stats', {})
total = stats.get('total', 0)
print(f'Vault: {total} entries')
by_status = stats.get('by_status', {})
print(f'  open_api: {by_status.get("open_api", 0)}')
print(f'  pending: {by_status.get("pending", 0)}')
print(f'  registered: {by_status.get("registered", "NOT PRESENT (honest)")}')

# Check all entries have required fields
all_ok = True
for e in entries:
    sid = e.get('site_id', '?')
    missing = []
    for field in ['site_id', 'site_name', 'site_url', 'status', 'agent_id']:
        if not e.get(field):
            missing.append(field)
    if missing:
        print(f'  MISSING fields in {sid}: {missing}')
        all_ok = False

if all_ok:
    print('All entries have required fields: OK')
else:
    print('Some entries have missing fields: FAIL')
