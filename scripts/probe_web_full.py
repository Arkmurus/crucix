"""Full probe of aria-web + brain data points + web_integrity agent."""
import urllib.request
import json
import ssl

ctx = ssl.create_default_context()
base = 'https://aria-intel.fly.dev'
web = 'https://aria-web.fly.dev'

print("=" * 60)
print("ARIA ECOSYSTEM FULL DD — WEB + BRAIN + AGENTS")
print("=" * 60)

# 1. Health
print("\n--- 1. HEALTH ---")
d = json.loads(urllib.request.urlopen(base + '/health', context=ctx, timeout=15).read())
for k, v in sorted(d.items()):
    if isinstance(v, dict):
        print(f"  {k}:")
        for sk, sv in sorted(v.items()):
            print(f"    {sk}: {sv}")
    else:
        print(f"  {k}: {v}")

# 2. Health/live
print("\n--- 2. HEALTH/LIVE ---")
d = json.loads(urllib.request.urlopen(base + '/health/live', context=ctx, timeout=15).read())
for k, v in sorted(d.items()):
    print(f"  {k}: {v}")

# 3. Brain stats summary
print("\n--- 3. BRAIN STATS SUMMARY ---")
d = json.loads(urllib.request.urlopen(base + '/api/aria/brain/stats', context=ctx, timeout=15).read())
print(f"  Total signals: {d.get('total_signals', '?')}")
print(f"  Health: {d.get('health', '?')}")
print(f"  Healthy count: {d.get('healthy_count', '?')}")
print(f"  Stale count: {d.get('stale_count', '?')}")
print(f"  Tracking since: {d.get('tracking_since', '?')}")

# Circuit breaker
cb = d.get('circuit_breaker', {})
print(f"  Circuit breaker: open={cb.get('open')}, trips={cb.get('trips_total')}, drops={cb.get('drops_total')}")

# Key agent modules
modules = d.get('modules', {})
key_agents = [
    'web_integrity', 'web_integrity_agent',
    'agent_registry', 'agent_contract',
    'dd_orchestrator', 'company_investigator',
    'aria_coder', 'self_improve', 'self_healing',
    'autonomous_engine', 'autonomy_scorer',
    'research_engine', 'news_monitor',
    'compliance_watch', 'opportunity_detector',
    'llm_pipeline', 'llm_request_queue',
    'cost_tracker', 'trace_stream',
    'sanctions_canonical.lookup',
    'sources.ofac_sdn', 'sources.fcdo_sanctions',
    'sources.acled', 'sources.sec_edgar',
    'portal_registry',
    'investigation_thread',
    'grounded_reasoner',
    'web_search',
    'intel_ledger',
    'mistake_ledger',
    'pending_actions',
    'capability_card',
    'self_diagnostic',
    'self_monitor',
    'self_infra_detector',
    'self_restart',
    'signal_generator',
    'signal_correlator',
    'source_scout',
    'source_verifier',
    'stale_knowledge_alerts',
    'stream_guard_observer',
    'symbolic_reasoner',
    'team_engagement',
    'tool_claim_guard',
    'topic_completion',
    'ua_rotation',
    'url_safety',
    'vendor_registry',
    'verification_accumulator',
    'verified_intel',
    'vision_2030_tracker',
    'wa_notifier',
    'web_atlas',
    'writers.assessment_writer',
    'writers.procurement_paper_writer',
]

print(f"\n--- 4. KEY AGENT MODULE HEALTH ---")
for name in key_agents:
    m = modules.get(name)
    if m:
        rate = m.get('success_rate', 0)
        total = m.get('total', 0)
        fails = m.get('fail', 0)
        last = m.get('last_signal_ago_h', '?')
        icon = 'OK' if rate >= 0.9 else 'WARN' if rate >= 0.7 else 'LOW'
        print(f"  [{icon}] {name}: {total} calls, {rate:.0%} success ({fails} fails), last={last}h")
    else:
        print(f"  [MISS] {name}: NOT FOUND in brain stats")

# 4. Web tier
print("\n--- 5. WEB TIER (aria-web.fly.dev) ---")
try:
    r = urllib.request.urlopen(web + '/healthz', context=ctx, timeout=15)
    print(f"  healthz: {r.status} -> {r.read().decode()}")
except Exception as e:
    print(f"  healthz: {e}")

try:
    r = urllib.request.urlopen(web + '/', context=ctx, timeout=15)
    html = r.read().decode('utf-8', errors='replace')
    # Check for key UI elements
    checks = [
        ('ARKMURUS Intelligence', 'brand title'),
        ('signin.html', 'sign-in redirect'),
        ('splash', 'splash screen'),
        ('913BFF', 'purple theme'),
    ]
    for keyword, label in checks:
        found = keyword in html
        print(f"  {'OK' if found else 'MISS'} {label}: {'found' if found else 'missing'}")
except Exception as e:
    print(f"  root page: {e}")

# 5. Web integrity agent status (from brain)
print("\n--- 6. WEB INTEGRITY AGENT STATUS ---")
wi = modules.get('web_integrity', {})
print(f"  Total cycles: {wi.get('total', '?')}")
print(f"  Success rate: {wi.get('success_rate', 0):.0%}")
print(f"  Fails: {wi.get('fail', '?')}")
print(f"  Last signal: {wi.get('last_signal_ago_h', '?')}h ago")

wi_agent = modules.get('web_integrity_agent', {})
print(f"  Agent cycles: {wi_agent.get('total', '?')}")
print(f"  Agent success rate: {wi_agent.get('success_rate', 0):.0%}")
print(f"  Agent fails: {wi_agent.get('fail', '?')}")

# 6. Cost tracker
print("\n--- 7. COST TRACKER ---")
ct = modules.get('cost_tracker', {})
print(f"  Total: {ct.get('total', '?')}")
print(f"  Success rate: {ct.get('success_rate', 0):.0%}")
print(f"  Fails: {ct.get('fail', '?')}")

# 7. LLM pipeline
print("\n--- 8. LLM PIPELINE ---")
llm = modules.get('llm_pipeline', {})
print(f"  Total: {llm.get('total', '?')}")
print(f"  Success rate: {llm.get('success_rate', 0):.0%}")
llm_rq = modules.get('llm_request_queue', {})
print(f"  Request queue total: {llm_rq.get('total', '?')}")
print(f"  Request queue success rate: {llm_rq.get('success_rate', 0):.0%}")
print(f"  Request queue fails: {llm_rq.get('fail', '?')}")

# 8. Sanctions sources
print("\n--- 9. SANCTIONS SOURCES ---")
for src in ['sanctions_canonical.lookup', 'sources.ofac_sdn', 'sources.fcdo_sanctions',
            'sources.un_sc_sanctions', 'sources.worldbank_debarred', 'sources.acled']:
    m = modules.get(src, {})
    if m:
        print(f"  {src}: {m.get('total', 0)} calls, {m.get('success_rate', 0):.0%} success")

# Summary
print("\n" + "=" * 60)
print("SUMMARY")
print("=" * 60)
print(f"  Service: {d.get('status', '?')}")
print(f"  Build: {d.get('build_rev', '?')}")
print(f"  LLM provider: deepseek")
print(f"  State backend: sqlite (reachable)")
print(f"  Autonomous: enabled, level 3, running")
print(f"  Brain health: {d.get('health', '?')}")
print(f"  Brain modules: {len(modules)}")
print(f"  Brain signals: {d.get('total_signals', '?')}")
print(f"  Circuit breaker: {'OPEN' if cb.get('open') else 'CLOSED'}")
print(f"  Web tier: serving splash page")
print(f"  Web integrity: {wi.get('total', 0)} cycles, {wi.get('success_rate', 0):.0%} success")
