"""Check key brain module stats."""
import urllib.request, json, ssl
ctx = ssl.create_default_context()
d = json.loads(urllib.request.urlopen('https://aria-intel.fly.dev/api/aria/brain/stats', context=ctx, timeout=15).read())
modules = d.get('modules', {})
print(f"Health: {d.get('health')}")
print(f"Signals: {d.get('total_signals')}")
cb = d.get('circuit_breaker', {})
print(f"Breaker open: {cb.get('open')}, drops: {cb.get('drops_total')}")
print()

for name in ['agent_registry', 'agent_contract', 'signal_generator', 'pending_actions',
             'web_integrity', 'aria_coder', 'self_healing', 'cost_tracker',
             'llm_request_queue', 'trace_stream']:
    m = modules.get(name)
    if m:
        total = m.get('total', 0)
        rate = m.get('success_rate', 0)
        fails = m.get('fail', 0)
        print(f"  {name}: {total} calls, {rate:.0%} success, {fails} fails")
    else:
        print(f"  {name}: NOT FOUND")
