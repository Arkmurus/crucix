"""Verify all session fixes are live and working in production."""
import urllib.request
import json
import ssl
import time

ctx = ssl.create_default_context()
base = 'https://aria-intel.fly.dev'

def fetch(path):
    r = urllib.request.urlopen(base + path, context=ctx, timeout=15)
    return json.loads(r.read().decode())

print("=" * 60)
print("SESSION FIXES VERIFICATION")
print("=" * 60)

# 1. Build info
print("\n--- 1. BUILD INFO ---")
d = fetch('/health/live')
print(f"  build_rev: {d.get('build_rev')}")
print(f"  status: {d.get('status')}")

# 2. R-F1483: Per-module success rates
print("\n--- 2. R-F1483: PER-MODULE SUCCESS RATES ---")
d = fetch('/api/aria/brain/stats')
modules = d.get('modules', {})
print(f"  Total modules: {len(modules)}")
print(f"  Total signals: {d.get('total_signals')}")
print(f"  Health: {d.get('health')}")
print(f"  Healthy count: {d.get('healthy_count')}")

# Check the modules that were at 0% due to the breaker mis-attribution
check_modules = ['agent_registry', 'agent_contract', 'signal_generator', 'pending_actions']
for name in check_modules:
    m = modules.get(name)
    if m:
        total = m.get('total', 0)
        success = m.get('success', 0)
        fail = m.get('fail', 0)
        rate = m.get('success_rate', 0)
        print(f"  {name}: {total} calls, {success} success, {fail} fail, {rate:.0%} rate")
    else:
        print(f"  {name}: NOT FOUND")

# 3. R-F1492: Adversarial/security/constitution audits
print("\n--- 3. R-F1492: AUDIT STATUS ---")
# Check if the audits are in the stale modules list (means they haven't fired)
stale = d.get('stale_modules', [])
print(f"  Stale modules: {len(stale)}")
for s in stale:
    print(f"    - {s}")

# 4. R-F1493/94: State_store lock contention
print("\n--- 4. R-F1493/94: STATE_STORE HEALTH ---")
# Check circuit breaker status
cb = d.get('circuit_breaker', {})
print(f"  Circuit breaker: open={cb.get('open')}, trips={cb.get('trips_total')}, drops={cb.get('drops_total')}")

# 5. R-F1495/96/97: Portal registration
print("\n--- 5. R-F1495/96/97: PORTAL REGISTRATION ---")
vault = fetch('/api/aria/vault')
stats = vault.get('stats', {})
print(f"  Vault entries: {stats.get('total', 0)}")
by_status = stats.get('by_status', {})
for status, count in sorted(by_status.items()):
    print(f"    {status}: {count}")

# 6. Overall health
print("\n--- 6. OVERALL HEALTH ---")
d = fetch('/health')
print(f"  Service: {d.get('status')}")
print(f"  LLM provider: {d.get('llm_provider')}")
print(f"  State backend: {d.get('state_backend', {}).get('status')}")
print(f"  Autonomous: enabled={d.get('autonomous', {}).get('enabled')}, running={d.get('autonomous', {}).get('running')}")
diag = d.get('diagnostic', {})
print(f"  Diagnostic: {diag.get('overall')} ({diag.get('counts', {}).get('pass')} pass / {diag.get('counts', {}).get('warn')} warn / {diag.get('counts', {}).get('fail')} fail)")

print("\n" + "=" * 60)
print("VERIFICATION COMPLETE")
print("=" * 60)
