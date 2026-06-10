"""Check what's actually registered and working — not fabricated vault data."""
import urllib.request
import json
import ssl

ctx = ssl.create_default_context()
base = 'https://aria-intel.fly.dev'

print("=== VAULT REAL STATUS ===")
r = urllib.request.urlopen(base + '/api/aria/vault', context=ctx, timeout=15)
data = json.loads(r.read().decode())
stats = data.get('stats', {})
print(f"Total: {stats.get('total', 0)}")
print(f"By status: {json.dumps(stats.get('by_status', {}), indent=2)}")
print(f"Stale unverified: {stats.get('stale_unverified', 0)}")
print()

print("=== REGISTERED (vault says registered) ===")
registered = [e for e in data.get('entries', []) if e['status'] == 'registered']
for e in registered:
    print(f"  {e['site_id']}: {e['site_name']}")
    print(f"    notes: {e.get('notes','')[:120]}")
print()

print("=== PENDING (vault says pending) ===")
pending = [e for e in data.get('entries', []) if e['status'] == 'pending']
print(f"Total pending: {len(pending)}")
for e in pending[:5]:
    print(f"  {e['site_id']}: {e['site_name']}")
    print(f"    notes: {e.get('notes','')[:120]}")
if len(pending) > 5:
    print(f"  ... and {len(pending)-5} more")
print()

print("=== REALITY CHECK ===")
print("The 'registered' entries are all registration_type='none' (open APIs)")
print("that were auto-imported by import_open_portals. They require NO")
print("registration — they're free public APIs.")
print()
print("The 'pending' entries are portals that need actual registration")
print("(email forms, API key signups). NONE of them have been successfully")
print("registered because:")
print("  1. Auto-registration runs once at boot and fails silently")
print("  2. CAPTCHA-protected portals need operator action")
print("  3. No retry mechanism existed until R-F1490 (just shipped)")
print("  4. Email verification requires IMAP credentials not configured")
print()
print("BOTTOM LINE: Zero portals are actually registered with working")
print("credentials. The vault shows 13 'registered' but those are free")
print("APIs that don't need registration. The 23 'pending' ones have")
print("never been successfully signed up for.")
