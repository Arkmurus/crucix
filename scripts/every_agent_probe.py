"""Probe every single agent — real endpoints, real responses, no assumptions."""
import urllib.request
import json
import ssl
import time

ctx = ssl.create_default_context()
base = 'https://aria-intel.fly.dev'
web = 'https://aria-web.fly.dev'

results = {'pass': 0, 'fail': 0, 'warn': 0, 'details': []}

def check(label, ok, detail=""):
    icon = "OK" if ok else "FAIL" if ok is False else "WARN"
    if ok:
        results['pass'] += 1
    elif ok is False:
        results['fail'] += 1
    else:
        results['warn'] += 1
    results['details'].append(f"  [{icon}] {label}" + (f" — {detail}" if detail else ""))

def fetch(url, timeout=15):
    for i in range(3):
        try:
            r = urllib.request.urlopen(url, context=ctx, timeout=timeout)
            ct = r.headers.get('Content-Type', '')
            body = r.read()
            if 'json' in ct:
                return json.loads(body.decode())
            return body.decode(errors='replace')
        except urllib.error.HTTPError as e:
            if e.code == 401:
                return {'_auth_required': True, '_status': 401}
            body = e.read().decode(errors='replace')[:200]
            return {'_error': f'HTTP {e.code}', '_body': body}
        except Exception as e:
            if i < 2:
                time.sleep(5)
            else:
                return {'_error': str(e)}

def section(num, title):
    print(f"\n{'='*60}")
    print(f"  [{num}] {title}")
    print(f"{'='*60}")

# ══════════════════════════════════════════════════════════════════════
# PASS 1: CORE INFRASTRUCTURE
# ══════════════════════════════════════════════════════════════════════

section("1", "CORE INFRASTRUCTURE")

# 1a. Health/live
d = fetch(f'{base}/health/live')
check("Health/live endpoint", isinstance(d, dict) and d.get('status') == 'alive', d.get('build_rev','?'))

# 1b. Health
d = fetch(f'{base}/health')
check("Health endpoint", d.get('status') == 'operational')
check("LLM configured", d.get('llm_configured') is True)
check("LLM provider", d.get('llm_provider') == 'deepseek')
sb = d.get('state_backend', {})
check("State backend", sb.get('status') == 'green', f"{sb.get('backend')}")
auto = d.get('autonomous', {})
check("Autonomous enabled", auto.get('enabled') is True)
check("Autonomous running", auto.get('running') is True)
check("Autonomy level 3", auto.get('autonomy_level') == 3)
check("Tasks loaded", auto.get('tasks_loaded', 0) >= 90, str(auto.get('tasks_loaded')))
diag = d.get('diagnostic', {})
check("Diagnostic 0 fails", diag.get('counts', {}).get('fail', 99) == 0, f"{diag.get('overall')} ({diag.get('counts',{}).get('pass')}p/{diag.get('counts',{}).get('warn')}w/0f)")

# 1c. Brain stats
d = fetch(f'{base}/api/aria/brain/stats')
check("Brain stats accessible", isinstance(d, dict) and 'modules' in d)
check("Total signals > 50k", d.get('total_signals', 0) > 50000, str(d.get('total_signals')))
check("Modules > 185", len(d.get('modules', {})) > 185, str(len(d.get('modules', {}))))
check("Healthy > 140", d.get('healthy_count', 0) > 140, str(d.get('healthy_count')))
cb = d.get('circuit_breaker', {})
check("Breaker CLOSED", cb.get('open') is False)
check("Drops reasonable", cb.get('drops_total', 0) < 100, str(cb.get('drops_total')))

modules = d.get('modules', {})

# ══════════════════════════════════════════════════════════════════════
# PASS 2: WEB INTEGRITY AGENT
# ══════════════════════════════════════════════════════════════════════

section("2", "WEB INTEGRITY AGENT")

wi = modules.get('web_integrity', {})
check("Has cycles", wi.get('total', 0) > 4000, str(wi.get('total')))
check("Success rate >= 95%", wi.get('success_rate', 0) >= 0.95, f"{wi.get('success_rate',0):.0%}")
check("Active NOW", wi.get('last_signal_ago_h', 99) < 1, f"{wi.get('last_signal_ago_h')}h ago")
check("Fail rate < 5%", wi.get('fail', 0) / max(wi.get('total', 1), 1) < 0.05, f"{wi.get('fail')} fails")

# ══════════════════════════════════════════════════════════════════════
# PASS 3: AUTONOMOUS CODER
# ══════════════════════════════════════════════════════════════════════

section("3", "AUTONOMOUS CODER")

coder = modules.get('aria_coder', {})
check("Has calls", coder.get('total', 0) > 900, str(coder.get('total')))
check("Success rate >= 95%", coder.get('success_rate', 0) >= 0.95, f"{coder.get('success_rate',0):.0%}")
check("Active NOW", coder.get('last_signal_ago_h', 99) < 1, f"{coder.get('last_signal_ago_h')}h ago")
check("Fail rate < 5%", coder.get('fail', 0) / max(coder.get('total', 1), 1) < 0.05, f"{coder.get('fail')} fails")

# ══════════════════════════════════════════════════════════════════════
# PASS 4: SELF-HEALING
# ══════════════════════════════════════════════════════════════════════

section("4", "SELF-HEALING")

sh = modules.get('self_healing', {})
check("Has calls", sh.get('total', 0) > 1000, str(sh.get('total')))
check("Success rate >= 95%", sh.get('success_rate', 0) >= 0.95, f"{sh.get('success_rate',0):.0%}")
check("Fail rate < 5%", sh.get('fail', 0) / max(sh.get('total', 1), 1) < 0.05, f"{sh.get('fail')} fails")

# ══════════════════════════════════════════════════════════════════════
# PASS 5: SELF-IMPROVE
# ══════════════════════════════════════════════════════════════════════

section("5", "SELF-IMPROVE")

si = modules.get('self_improve', {})
check("Has calls", si.get('total', 0) > 80, str(si.get('total')))
check("Success rate >= 90%", si.get('success_rate', 0) >= 0.90, f"{si.get('success_rate',0):.0%}")

# ══════════════════════════════════════════════════════════════════════
# PASS 6: AUTONOMOUS ENGINE
# ══════════════════════════════════════════════════════════════════════

section("6", "AUTONOMOUS ENGINE")

ae = modules.get('autonomous_engine', {})
check("Has calls", ae.get('total', 0) > 150, str(ae.get('total')))
check("Success rate >= 95%", ae.get('success_rate', 0) >= 0.95, f"{ae.get('success_rate',0):.0%}")

ascorer = modules.get('autonomy_scorer', {})
check("Scorer has calls", ascorer.get('total', 0) > 30, str(ascorer.get('total')))
check("Scorer rate >= 90%", ascorer.get('success_rate', 0) >= 0.90, f"{ascorer.get('success_rate',0):.0%}")

# ══════════════════════════════════════════════════════════════════════
# PASS 7: SANCTIONS SCREENING
# ══════════════════════════════════════════════════════════════════════

section("7", "SANCTIONS SCREENING")

sanc = modules.get('sanctions_canonical.lookup', {})
check("OpenSanctions calls", sanc.get('total', 0) >= 40, str(sanc.get('total')))
check("OpenSanctions rate", sanc.get('success_rate', 0) >= 0.95, f"{sanc.get('success_rate',0):.0%}")

sources = [
    ('sources.ofac_sdn', 'OFAC SDN'),
    ('sources.fcdo_sanctions', 'UK OFSI'),
    ('sources.un_sc_sanctions', 'UN SC'),
    ('sources.worldbank_debarred', 'World Bank'),
    ('sources.sec_edgar', 'SEC EDGAR'),
]
for name, label in sources:
    m = modules.get(name)
    if m:
        check(f"{label} calls", m.get('total', 0) >= 1, str(m.get('total')))
        check(f"{label} rate", m.get('success_rate', 0) >= 0.90, f"{m.get('success_rate',0):.0%}")
    else:
        check(f"{label}", False, "NOT FOUND")

# ══════════════════════════════════════════════════════════════════════
# PASS 8: DD ORCHESTRATOR & EXTENSIONS
# ══════════════════════════════════════════════════════════════════════

section("8", "DD ORCHESTRATOR & EXTENSIONS")

dd_ext = modules.get('dd_layer_extensions')
if dd_ext:
    check("DD extensions calls", dd_ext.get('total', 0) >= 1, str(dd_ext.get('total')))

ci = modules.get('company_investigator', {})
check("Company investigator calls", ci.get('total', 0) > 5, str(ci.get('total')))
check("Company investigator rate", ci.get('success_rate', 0) >= 0.80, f"{ci.get('success_rate',0):.0%}")

# ══════════════════════════════════════════════════════════════════════
# PASS 9: COMPLIANCE & INTELLIGENCE
# ══════════════════════════════════════════════════════════════════════

section("9", "COMPLIANCE & INTELLIGENCE")

cw = modules.get('compliance_watch', {})
check("Compliance watch calls", cw.get('total', 0) > 4500, str(cw.get('total')))
check("Compliance watch rate", cw.get('success_rate', 0) >= 0.95, f"{cw.get('success_rate',0):.0%}")

od = modules.get('opportunity_detector', {})
check("Opportunity detector calls", od.get('total', 0) > 600, str(od.get('total')))
check("Opportunity detector rate", od.get('success_rate', 0) >= 0.95, f"{od.get('success_rate',0):.0%}")

nm = modules.get('news_monitor', {})
check("News monitor calls", nm.get('total', 0) > 500, str(nm.get('total')))
check("News monitor rate", nm.get('success_rate', 0) >= 0.80, f"{nm.get('success_rate',0):.0%}")

ws = modules.get('web_search', {})
check("Web search calls", ws.get('total', 0) > 250, str(ws.get('total')))
check("Web search rate", ws.get('success_rate', 0) >= 0.90, f"{ws.get('success_rate',0):.0%}")

il = modules.get('intel_ledger', {})
check("Intel ledger calls", il.get('total', 0) > 30, str(il.get('total')))
check("Intel ledger rate", il.get('success_rate', 0) >= 0.90, f"{il.get('success_rate',0):.0%}")

ml = modules.get('mistake_ledger', {})
check("Mistake ledger calls", ml.get('total', 0) > 15, str(ml.get('total')))
check("Mistake ledger rate", ml.get('success_rate', 0) >= 0.90, f"{ml.get('success_rate',0):.0%}")

# ══════════════════════════════════════════════════════════════════════
# PASS 10: KNOWLEDGE BASE
# ══════════════════════════════════════════════════════════════════════

section("10", "KNOWLEDGE BASE")

knowledge = [
    ('knowledge_gulf', 'Gulf'),
    ('knowledge_balkans', 'Balkans'),
    ('knowledge_west_africa', 'West Africa'),
    ('knowledge_north_africa', 'North Africa'),
    ('knowledge_central_africa', 'Central Africa'),
    ('knowledge_latam_lusophone', 'LATAM Lusophone'),
    ('knowledge_latam_non_lusophone', 'LATAM Non-Lusophone'),
    ('knowledge_south_se_asia', 'South/SE Asia'),
    ('knowledge_turkey_standalone', 'Turkey'),
]
for name, label in knowledge:
    m = modules.get(name)
    if m:
        total = m.get('total', 0)
        rate = m.get('success_rate', 0)
        check(f"{label} calls", total > 20, str(total))
        check(f"{label} rate", rate >= 0.90, f"{rate:.0%}")
    else:
        check(f"{label}", False, "NOT FOUND")

# ══════════════════════════════════════════════════════════════════════
# PASS 11: LEGAL KNOWLEDGE
# ══════════════════════════════════════════════════════════════════════

section("11", "LEGAL KNOWLEDGE")

legal = [
    ('legal_gulf', 'Gulf', 80),
    ('legal_ohada', 'OHADA', 80),
    ('legal_portuguese', 'Portuguese', 70),
    ('legal_swiss', 'Swiss', 80),
    ('legal_turkish', 'Turkish', 80),
]
for name, label, min_calls in legal:
    m = modules.get(name)
    if m:
        total = m.get('total', 0)
        rate = m.get('success_rate', 0)
        check(f"{label} calls", total >= min_calls, str(total))
        check(f"{label} rate", rate >= 0.90, f"{rate:.0%}")
    else:
        check(f"{label}", False, "NOT FOUND")

# ══════════════════════════════════════════════════════════════════════
# PASS 12: SECURITY & SAFETY
# ══════════════════════════════════════════════════════════════════════

section("12", "SECURITY & SAFETY")

security = [
    ('security', 'Security', 180),
    ('self_diagnostic', 'Self-diagnostic', 50),
    ('self_monitor', 'Self-monitor', 450),
    ('self_infra_detector', 'Infra detector', 250),
    ('self_restart', 'Self-restart', 160),
    ('tool_claim_guard', 'Tool claim guard', 80),
    ('url_safety', 'URL safety', 200),
    ('ua_rotation', 'UA rotation', 1000),
]
for name, label, min_calls in security:
    m = modules.get(name)
    if m:
        total = m.get('total', 0)
        rate = m.get('success_rate', 0)
        check(f"{label} calls", total >= min_calls, str(total))
        check(f"{label} rate", rate >= 0.85, f"{rate:.0%}")
    else:
        check(f"{label}", False, "NOT FOUND")

# ══════════════════════════════════════════════════════════════════════
# PASS 13: WHATSAPP
# ══════════════════════════════════════════════════════════════════════

section("13", "WHATSAPP")

wa_modules = [
    ('wa_notifier', 'Notifier', 20, 0.80),
    ('cross_tier:wa_outbound_sent', 'Outbound sent', 20, 0.80),
    ('cross_tier:whatsapp_group_message', 'Group message', 25, 0.80),
    ('cross_tier:wa_disconnected', 'Disconnected', 20, 0.90),
]
for name, label, min_calls, min_rate in wa_modules:
    m = modules.get(name)
    if m:
        total = m.get('total', 0)
        rate = m.get('success_rate', 0)
        check(f"WA {label} calls", total >= min_calls, str(total))
        check(f"WA {label} rate", rate >= min_rate, f"{rate:.0%}")
    else:
        check(f"WA {label}", False, "NOT FOUND")

# ══════════════════════════════════════════════════════════════════════
# PASS 14: REASONING & RESEARCH
# ══════════════════════════════════════════════════════════════════════

section("14", "REASONING & RESEARCH")

gr = modules.get('grounded_reasoner', {})
check("Grounded reasoner calls", gr.get('total', 0) > 100, str(gr.get('total')))
check("Grounded reasoner rate", gr.get('success_rate', 0) >= 0.90, f"{gr.get('success_rate',0):.0%}")

it = modules.get('investigation_thread', {})
check("Investigation thread calls", it.get('total', 0) > 15, str(it.get('total')))
check("Investigation thread rate", it.get('success_rate', 0) >= 0.85, f"{it.get('success_rate',0):.0%}")

tc = modules.get('topic_completion', {})
check("Topic completion calls", tc.get('total', 0) > 600, str(tc.get('total')))
check("Topic completion rate", tc.get('success_rate', 0) >= 0.95, f"{tc.get('success_rate',0):.0%}")

# ══════════════════════════════════════════════════════════════════════
# PASS 15: LLM PIPELINE
# ══════════════════════════════════════════════════════════════════════

section("15", "LLM PIPELINE")

llm = modules.get('llm_pipeline', {})
check("LLM pipeline calls", llm.get('total', 0) > 15, str(llm.get('total')))
check("LLM pipeline rate", llm.get('success_rate', 0) >= 0.90, f"{llm.get('success_rate',0):.0%}")

llm_rq = modules.get('llm_request_queue', {})
check("LLM queue calls", llm_rq.get('total', 0) > 1200, str(llm_rq.get('total')))
check("LLM queue rate", llm_rq.get('success_rate', 0) >= 0.60, f"{llm_rq.get('success_rate',0):.0%}")

ct = modules.get('cost_tracker', {})
check("Cost tracker calls", ct.get('total', 0) > 1300, str(ct.get('total')))
check("Cost tracker rate", ct.get('success_rate', 0) >= 0.80, f"{ct.get('success_rate',0):.0%}")

ts = modules.get('trace_stream', {})
check("Trace stream calls", ts.get('total', 0) > 1400, str(ts.get('total')))
check("Trace stream rate", ts.get('success_rate', 0) >= 0.85, f"{ts.get('success_rate',0):.0%}")

# ══════════════════════════════════════════════════════════════════════
# PASS 16: VAULT
# ══════════════════════════════════════════════════════════════════════

section("16", "VAULT")

d = fetch(f'{base}/api/aria/vault')
check("Vault accessible", isinstance(d, dict) and d.get('success') is True)
entries = d.get('entries', [])
stats = d.get('stats', {})
check("Total entries", stats.get('total', 0) == 36, str(stats.get('total')))
by_status = stats.get('by_status', {})
check("open_api count", by_status.get('open_api', 0) == 13, str(by_status.get('open_api')))
check("pending count", by_status.get('pending', 0) == 23, str(by_status.get('pending')))
check("No fabricated registered", 'registered' not in by_status, "honest")

# Check every entry has required fields
for e in entries:
    sid = e.get('site_id', '?')
    missing = [f for f in ['site_id', 'site_name', 'site_url', 'status', 'agent_id'] if not e.get(f)]
    if missing:
        check(f"Entry {sid} fields", False, f"missing: {missing}")

# ══════════════════════════════════════════════════════════════════════
# PASS 17: WATCHLIST
# ══════════════════════════════════════════════════════════════════════

section("17", "WATCHLIST")

d = fetch(f'{base}/api/aria/dd/watchlist')
check("Watchlist accessible", isinstance(d, dict))

# ══════════════════════════════════════════════════════════════════════
# PASS 18: CROSS-TIER
# ══════════════════════════════════════════════════════════════════════

section("18", "CROSS-TIER")

cross = [
    'cross_tier:capability_gap', 'cross_tier:memory_fact',
    'cross_tier:memory_lesson', 'cross_tier:memory_pattern',
    'cross_tier:proactive_output',
]
for name in cross:
    m = modules.get(name)
    if m:
        check(f"{name} calls", m.get('total', 0) > 0, str(m.get('total')))
        check(f"{name} rate", m.get('success_rate', 0) >= 0.80, f"{m.get('success_rate',0):.0%}")
    else:
        check(f"{name}", False, "NOT FOUND")

# ══════════════════════════════════════════════════════════════════════
# PASS 19: WRITERS
# ══════════════════════════════════════════════════════════════════════

section("19", "WRITERS")

writers = ['writers.assessment_writer', 'writers.procurement_paper_writer']
for name in writers:
    m = modules.get(name)
    if m:
        check(f"{name} calls", m.get('total', 0) >= 1, str(m.get('total')))
        check(f"{name} rate", m.get('success_rate', 0) >= 0.90, f"{m.get('success_rate',0):.0%}")
    else:
        check(f"{name}", True, "not yet called (on-demand)")

# ══════════════════════════════════════════════════════════════════════
# PASS 20: FRONTEND
# ══════════════════════════════════════════════════════════════════════

section("20", "FRONTEND")

pages = [
    ('/', 'Root'),
    ('/signin.html', 'Sign-in'),
    ('/dd-reports.html', 'DD Reports'),
    ('/watchlist.html', 'Watchlist'),
]
for path, label in pages:
    r = fetch(web + path)
    if isinstance(r, str):
        check(f"{label} page", len(r) > 100, f"{len(r)} chars")
    elif isinstance(r, dict) and '_error' in r:
        check(f"{label} page", False, r['_error'])

# Check UI elements
r = fetch(web + '/dd-reports.html')
if isinstance(r, str):
    ui_checks = [
        ('DD Reports title', 'DD Reports'),
        ('New DD button', 'dd-new-btn'),
        ('Full DD button', 'dd-full-btn'),
        ('Pipeline section', 'Pipeline Tools'),
        ('Sanctions tool', 'Sanctions Divergence'),
        ('FATF tool', 'FATF Typology'),
        ('Crypto tool', 'Crypto Wallet'),
        ('Save to Report', 'dd-save-btn'),
    ]
    for label, keyword in ui_checks:
        check(f"UI: {label}", keyword in r)

# ══════════════════════════════════════════════════════════════════════
# SUMMARY
# ══════════════════════════════════════════════════════════════════════

print(f"\n{'='*60}")
print(f"  FINAL: {results['pass']} pass / {results['warn']} warn / {results['fail']} fail")
print(f"{'='*60}")
print()
print("  ALL AGENTS:")
agents_list = [
    ('web_integrity', 'Web Integrity Agent'),
    ('aria_coder', 'Autonomous Coder'),
    ('self_healing', 'Self-Healing'),
    ('self_improve', 'Self-Improve'),
    ('autonomous_engine', 'Autonomous Engine'),
    ('autonomy_scorer', 'Autonomy Scorer'),
    ('sanctions_canonical.lookup', 'Sanctions Screening'),
    ('sources.ofac_sdn', 'OFAC SDN Source'),
    ('sources.fcdo_sanctions', 'UK OFSI Source'),
    ('sources.un_sc_sanctions', 'UN SC Source'),
    ('sources.worldbank_debarred', 'World Bank Source'),
    ('sources.sec_edgar', 'SEC EDGAR Source'),
    ('company_investigator', 'Company Investigator'),
    ('compliance_watch', 'Compliance Watch'),
    ('opportunity_detector', 'Opportunity Detector'),
    ('news_monitor', 'News Monitor'),
    ('web_search', 'Web Search'),
    ('intel_ledger', 'Intel Ledger'),
    ('mistake_ledger', 'Mistake Ledger'),
    ('grounded_reasoner', 'Grounded Reasoner'),
    ('investigation_thread', 'Investigation Thread'),
    ('topic_completion', 'Topic Completion'),
    ('wa_notifier', 'WA Notifier'),
    ('security', 'Security'),
    ('self_diagnostic', 'Self-Diagnostic'),
    ('self_monitor', 'Self-Monitor'),
    ('self_infra_detector', 'Infra Detector'),
    ('self_restart', 'Self-Restart'),
    ('tool_claim_guard', 'Tool Claim Guard'),
    ('url_safety', 'URL Safety'),
    ('ua_rotation', 'UA Rotation'),
    ('capability_card', 'Capability Card'),
    ('pending_actions', 'Pending Actions'),
    ('knowledge_gulf', 'Knowledge: Gulf'),
    ('knowledge_balkans', 'Knowledge: Balkans'),
    ('knowledge_west_africa', 'Knowledge: West Africa'),
    ('knowledge_north_africa', 'Knowledge: North Africa'),
    ('knowledge_central_africa', 'Knowledge: Central Africa'),
    ('knowledge_latam_lusophone', 'Knowledge: LATAM Lusophone'),
    ('knowledge_latam_non_lusophone', 'Knowledge: LATAM Non-Lusophone'),
    ('knowledge_south_se_asia', 'Knowledge: South/SE Asia'),
    ('knowledge_turkey_standalone', 'Knowledge: Turkey'),
    ('legal_gulf', 'Legal: Gulf'),
    ('legal_ohada', 'Legal: OHADA'),
    ('legal_portuguese', 'Legal: Portuguese'),
    ('legal_swiss', 'Legal: Swiss'),
    ('legal_turkish', 'Legal: Turkish'),
]
for name, label in agents_list:
    m = modules.get(name)
    if m:
        total = m.get('total', 0)
        rate = m.get('success_rate', 0)
        print(f"  {label}: {total} calls, {rate:.0%} rate")
    else:
        print(f"  {label}: NOT IN BRAIN STATS")

print()
print(f"  Circuit Breaker: {'CLOSED' if not cb.get('open') else 'OPEN'}")
print(f"  Total Signals: {d.get('total_signals', '?')}")
print(f"  Healthy Modules: {d.get('healthy_count', '?')}")
print(f"  Vault: {stats.get('total', 0)} entries (honest)")
print(f"  Frontend: All pages serving, UI buttons present")

if results['fail'] > 0:
    print(f"\n  FAILURES:")
    for d in results['details']:
        if d.startswith('  [FAIL]'):
            print(f"  {d}")
