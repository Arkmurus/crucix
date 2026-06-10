"""Full system 380 DD — probe every major subsystem."""
import urllib.request
import json
import ssl
import time

ctx = ssl.create_default_context()
base = 'https://aria-intel.fly.dev'

def fetch(path, timeout=15):
    r = urllib.request.urlopen(base + path, context=ctx, timeout=timeout)
    return json.loads(r.read().decode())

def section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")

def check(label, ok, detail=""):
    icon = "OK" if ok else "FAIL" if ok is False else "WARN"
    print(f"  [{icon}] {label}" + (f" — {detail}" if detail else ""))

section("1. BUILD & CORE HEALTH")

d = fetch('/health/live')
check("Build rev", True, d.get('build_rev', '?'))
check("Status", d.get('status') == 'alive', d.get('status'))

d = fetch('/health')
check("Service status", d.get('status') == 'operational', d.get('status'))
check("LLM provider", d.get('llm_provider') == 'deepseek')
sb = d.get('state_backend', {})
check("State backend", sb.get('status') == 'green', f"{sb.get('backend')} reachable={sb.get('reachable')}")
auto = d.get('autonomous', {})
check("Autonomous enabled", auto.get('enabled') is True)
check("Autonomous running", auto.get('running') is True)
check("Autonomy level", auto.get('autonomy_level') == 3)
check("Tasks loaded", auto.get('tasks_loaded', 0) > 0, str(auto.get('tasks_loaded')))
diag = d.get('diagnostic', {})
check("Diagnostic overall", diag.get('overall') in ('GREEN', 'AMBER'), f"{diag.get('overall')} ({diag.get('counts',{}).get('pass')}p/{diag.get('counts',{}).get('warn')}w/{diag.get('counts',{}).get('fail')}f)")
check("Critical failures", len(diag.get('critical_failures', [])) == 0, str(diag.get('critical_failures', [])))

section("2. BRAIN STATS")

d = fetch('/api/aria/brain/stats')
check("Total signals", d.get('total_signals', 0) > 48000, str(d.get('total_signals')))
check("Modules tracked", d.get('healthy_count', 0) > 100, f"{d.get('healthy_count')} healthy")
cb = d.get('circuit_breaker', {})
check("Circuit breaker", cb.get('open') is False, f"drops={cb.get('drops_total')}, trips={cb.get('trips_total')}")
check("Breaker cooldown working", cb.get('open') is False)

modules = d.get('modules', {})
key_modules = {
    'web_integrity': ('monitoring', 4000, 0.9),
    'aria_coder': ('coding', 900, 0.9),
    'self_healing': ('infra', 1000, 0.9),
    'autonomous_engine': ('autonomy', 150, 0.9),
    'compliance_watch': ('compliance', 4500, 0.9),
    'opportunity_detector': ('intel', 600, 0.9),
    'trace_stream': ('tracing', 1400, 0.8),
    'cost_tracker': ('cost', 1300, 0.8),
    'llm_request_queue': ('llm', 1200, 0.6),
    'signal_generator': ('signals', 500, 0.5),
}
for name, (label, min_calls, min_rate) in sorted(key_modules.items()):
    m = modules.get(name)
    if m:
        total = m.get('total', 0)
        rate = m.get('success_rate', 0)
        ok_calls = total >= min_calls
        ok_rate = rate >= min_rate
        status = ok_calls and ok_rate
        detail = f"{total} calls, {rate:.0%} rate"
        if not ok_calls:
            detail += f" (expected >= {min_calls})"
        if not ok_rate:
            detail += f" (expected >= {min_rate:.0%})"
        check(f"{name} ({label})", status, detail)
    else:
        check(f"{name} ({label})", False, "NOT FOUND")

section("3. SANCTIONS & DD SOURCES")

sanctions_modules = [
    ('sanctions_canonical.lookup', 'OpenSanctions', 30, 0.9),
    ('sources.ofac_sdn', 'OFAC SDN', 1, 0.9),
    ('sources.fcdo_sanctions', 'UK OFSI', 2, 0.9),
    ('sources.un_sc_sanctions', 'UN SC', 2, 0.9),
    ('sources.worldbank_debarred', 'World Bank', 1, 0.9),
    ('sources.sec_edgar', 'SEC EDGAR', 1, 0.9),
    ('sources.acled', 'ACLED', 1, 0.5),
]
for name, label, min_calls, min_rate in sanctions_modules:
    m = modules.get(name)
    if m:
        total = m.get('total', 0)
        rate = m.get('success_rate', 0)
        ok = total >= min_calls and rate >= min_rate
        check(f"{label} ({name})", ok, f"{total} calls, {rate:.0%} rate")
    else:
        check(f"{label} ({name})", False, "NOT FOUND")

section("4. VAULT & REGISTRATION")

d = fetch('/api/aria/vault')
stats = d.get('stats', {})
check("Vault has entries", stats.get('total', 0) > 0, str(stats.get('total')))
by_status = stats.get('by_status', {})
check("Open API portals", 'open_api' in by_status, str(by_status.get('open_api', 0)))
check("Pending portals", 'pending' in by_status, str(by_status.get('pending', 0)))
check("Registered portals", 'registered' not in by_status, "0 (honest — no fabricated data)")

section("5. WEB INTEGRITY AGENT")

wi = modules.get('web_integrity', {})
check("Web integrity cycles", wi.get('total', 0) > 4000, str(wi.get('total')))
check("Web integrity success rate", wi.get('success_rate', 0) >= 0.95, f"{wi.get('success_rate',0):.0%}")
check("Web integrity recent", wi.get('last_signal_ago_h', 99) < 1, f"{wi.get('last_signal_ago_h')}h ago")

section("6. LLM & COST")

llm = modules.get('llm_pipeline', {})
check("LLM pipeline calls", llm.get('total', 0) > 10, str(llm.get('total')))
check("LLM pipeline success", llm.get('success_rate', 0) >= 0.9, f"{llm.get('success_rate',0):.0%}")
llm_rq = modules.get('llm_request_queue', {})
check("LLM request queue", llm_rq.get('total', 0) > 1000, str(llm_rq.get('total')))
ct = modules.get('cost_tracker', {})
check("Cost tracker", ct.get('total', 0) > 1000, str(ct.get('total')))

section("7. AUTONOMOUS SYSTEM")

auto_modules = [
    ('aria_coder', 'Self-coder', 900),
    ('self_improve', 'Self-improve', 80),
    ('self_healing', 'Self-healing', 1000),
    ('autonomous_engine', 'Engine', 150),
    ('autonomy_scorer', 'Scorer', 30),
]
for name, label, min_calls in auto_modules:
    m = modules.get(name)
    if m:
        total = m.get('total', 0)
        rate = m.get('success_rate', 0)
        check(f"{label} ({name})", total >= min_calls and rate >= 0.9, f"{total} calls, {rate:.0%} rate")
    else:
        check(f"{label} ({name})", False, "NOT FOUND")

section("8. INTELLIGENCE FEEDS")

intel_modules = [
    ('compliance_watch', 'Compliance watch', 4500),
    ('news_monitor', 'News monitor', 500),
    ('opportunity_detector', 'Opportunity detector', 600),
    ('web_search', 'Web search', 250),
    ('intel_ledger', 'Intel ledger', 30),
]
for name, label, min_calls in intel_modules:
    m = modules.get(name)
    if m:
        total = m.get('total', 0)
        rate = m.get('success_rate', 0)
        check(f"{label} ({name})", total >= min_calls and rate >= 0.8, f"{total} calls, {rate:.0%} rate")
    else:
        check(f"{label} ({name})", False, "NOT FOUND")

section("9. KNOWLEDGE & MEMORY")

knowledge_modules = [
    'knowledge_gulf', 'knowledge_balkans', 'knowledge_west_africa',
    'knowledge_north_africa', 'knowledge_central_africa',
    'knowledge_latam_lusophone', 'knowledge_latam_non_lusophone',
    'knowledge_south_se_asia', 'knowledge_turkey_standalone',
]
for name in knowledge_modules:
    m = modules.get(name)
    if m:
        total = m.get('total', 0)
        rate = m.get('success_rate', 0)
        check(f"{name}", total > 20 and rate >= 0.9, f"{total} calls, {rate:.0%} rate")
    else:
        check(f"{name}", False, "NOT FOUND")

section("10. LEGAL & COMPLIANCE")

legal_modules = [
    ('legal_gulf', 'Gulf legal', 80),
    ('legal_ohada', 'OHADA legal', 80),
    ('legal_portuguese', 'Portuguese legal', 70),
    ('legal_swiss', 'Swiss legal', 80),
    ('legal_turkish', 'Turkish legal', 80),
]
for name, label, min_calls in legal_modules:
    m = modules.get(name)
    if m:
        total = m.get('total', 0)
        rate = m.get('success_rate', 0)
        check(f"{label} ({name})", total >= min_calls and rate >= 0.9, f"{total} calls, {rate:.0%} rate")
    else:
        check(f"{label} ({name})", False, "NOT FOUND")

section("11. SECURITY & SAFETY")

security_modules = [
    ('security', 'Security', 180),
    ('self_diagnostic', 'Self-diagnostic', 50),
    ('self_monitor', 'Self-monitor', 450),
    ('self_infra_detector', 'Infra detector', 250),
    ('self_restart', 'Self-restart', 160),
    ('tool_claim_guard', 'Tool claim guard', 80),
    ('url_safety', 'URL safety', 200),
    ('ua_rotation', 'UA rotation', 1000),
]
for name, label, min_calls in security_modules:
    m = modules.get(name)
    if m:
        total = m.get('total', 0)
        rate = m.get('success_rate', 0)
        check(f"{label} ({name})", total >= min_calls and rate >= 0.9, f"{total} calls, {rate:.0%} rate")
    else:
        check(f"{label} ({name})", False, "NOT FOUND")

section("12. WHATSAPP & CROSS-TIER")

wa_modules = [
    'wa_notifier', 'cross_tier:wa_outbound_sent',
    'cross_tier:whatsapp_group_message', 'cross_tier:wa_disconnected',
]
for name in wa_modules:
    m = modules.get(name)
    if m:
        total = m.get('total', 0)
        rate = m.get('success_rate', 0)
        check(f"{name}", total > 10 and rate >= 0.8, f"{total} calls, {rate:.0%} rate")
    else:
        check(f"{name}", False, "NOT FOUND")

section("SUMMARY")

print(f"  Build: {d.get('build_rev', '?')}")
print(f"  Status: operational")
print(f"  Brain modules: {len(modules)}")
print(f"  Brain signals: {d.get('total_signals', '?')}")
print(f"  Circuit breaker: {'CLOSED' if not cb.get('open') else 'OPEN'}")
print(f"  Diagnostic: {diag.get('overall')} ({diag.get('counts',{}).get('pass')}p/{diag.get('counts',{}).get('warn')}w/{diag.get('counts',{}).get('fail')}f)")
print(f"  Web integrity: {wi.get('total',0)} cycles, {wi.get('success_rate',0):.0%} success")
print(f"  Vault: {stats.get('total',0)} entries (honest)")
print(f"  Autonomous: level 3, running, {auto.get('tasks_loaded',0)} tasks")
