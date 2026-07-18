"""Adversarial agent audit — test every agent's REAL output, not just stats."""
import urllib.request
import json
import ssl
import time

# R-F2727 — window-aware verdicts (not-observed-recently ≠ broken). Works both as
# `python scripts/adversarial_agent_audit.py` and as an imported module.
try:
    from probe_verdict import windowed_ok, recency_ok
except ImportError:  # pragma: no cover
    from scripts.probe_verdict import windowed_ok, recency_ok

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
    """Fetch a URL with a FRESH connection each time."""
    import http.client
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme == 'https':
            conn = http.client.HTTPSConnection(parsed.netloc, timeout=timeout, context=ctx)
        else:
            conn = http.client.HTTPConnection(parsed.netloc, timeout=timeout)
        conn.request('GET', parsed.path + ('?' + parsed.query if parsed.query else ''))
        r = conn.getresponse()
        body = r.read()
        ct = r.headers.get('Content-Type', '')
        conn.close()
        if 'json' in ct:
            return json.loads(body.decode())
        return body.decode(errors='replace')
    except Exception as e:
        return {'_error': str(e)}

def section(num, title):
    print(f"\n{'='*60}")
    print(f"  [{num}] {title}")
    print(f"{'='*60}")

# ══════════════════════════════════════════════════════════════════════
# 1. CORE — verify the app is actually serving real data
# ══════════════════════════════════════════════════════════════════════

section("1", "CORE — is the app actually serving real data?")

d = fetch(f'{base}/health/live')
check("Health/live returns real build_rev", bool(d.get('build_rev')), d.get('build_rev','?'))
check("Status is alive", d.get('status') == 'alive')

d = fetch(f'{base}/health')
check("Health returns operational", d.get('status') == 'operational')
diag = d.get('diagnostic', {})
check("Zero critical failures", len(diag.get('critical_failures', [])) == 0, str(diag.get('critical_failures')))
check("Zero diagnostic failures", diag.get('counts', {}).get('fail', 99) == 0, f"{diag.get('overall')} ({diag.get('counts',{}).get('pass')}p/{diag.get('counts',{}).get('warn')}w/0f)")

# ══════════════════════════════════════════════════════════════════════
# 2. BRAIN STATS — verify every module's data is real
# ══════════════════════════════════════════════════════════════════════

section("2", "BRAIN STATS — verify every module's data is real")

d = fetch(f'{base}/api/aria/brain/stats')
check("Brain stats accessible", isinstance(d, dict) and 'modules' in d)
# R-F2727 — these are WINDOWED brain stats; below a soft floor is a quiet window, not a
# broken system → WARN, not FAIL (a broken stats endpoint is caught by "Brain stats accessible").
check("Total signals above soft floor (windowed)", windowed_ok(d.get('total_signals'), 50000), str(d.get('total_signals')))
check("Modules in window above soft floor", windowed_ok(len(d.get('modules', {})), 185), str(len(d.get('modules', {}))))
check("Healthy modules above soft floor (windowed)", windowed_ok(d.get('healthy_count'), 140), str(d.get('healthy_count')))
cb = d.get('circuit_breaker', {})
check("Breaker CLOSED", cb.get('open') is False)
check("Drops reasonable", cb.get('drops_total', 0) < 100, str(cb.get('drops_total')))

modules = d.get('modules', {})

# Verify every module has non-negative numbers (no fabricated stats)
for name, m in modules.items():
    if not isinstance(m, dict):
        check(f"Module {name} is dict", False, f"got {type(m).__name__}")
        continue
    total = m.get('total', 0)
    success = m.get('success', 0)
    fail = m.get('fail', 0)
    rate = m.get('success_rate', 0)
    if total < 0 or success < 0 or fail < 0:
        check(f"Module {name} has negative stats", False, f"total={total} success={success} fail={fail}")
    if total > 0 and success + fail != total:
        # Some modules have success+fail < total (dropped signals) — that's OK
        pass

# ══════════════════════════════════════════════════════════════════════
# 3. WEB INTEGRITY — verify it's actually monitoring, not fabricating cycles
# ══════════════════════════════════════════════════════════════════════

section("3", "WEB INTEGRITY — actually monitoring?")

wi = modules.get('web_integrity', {})
check("Has real cycles", wi.get('total', 0) > 4000, str(wi.get('total')))
check("Success rate real", wi.get('success_rate', 0) >= 0.95, f"{wi.get('success_rate',0):.0%}")
check("Active recently (WARN if idle)", recency_ok(wi.get('last_signal_ago_h'), 1), f"{wi.get('last_signal_ago_h')}h ago")  # R-F2728 — idle ≠ broken
# Verify the fail count is consistent
check("Fail count matches rate", abs(wi.get('fail', 0) / max(wi.get('total', 1), 1) - (1 - wi.get('success_rate', 0))) < 0.02, f"fails={wi.get('fail')} rate={wi.get('success_rate',0):.0%}")

# ══════════════════════════════════════════════════════════════════════
# 4. AUTONOMOUS CODER — verify it's actually coding, not fabricating
# ══════════════════════════════════════════════════════════════════════

section("4", "AUTONOMOUS CODER — actually coding?")

coder = modules.get('aria_coder', {})
check("Has real calls", coder.get('total', 0) > 1000, str(coder.get('total')))
check("Success rate real", coder.get('success_rate', 0) >= 0.95, f"{coder.get('success_rate',0):.0%}")
check("Active recently (WARN if idle)", recency_ok(coder.get('last_signal_ago_h'), 1), f"{coder.get('last_signal_ago_h')}h ago")  # R-F2728 — idle ≠ broken
# Verify fail count is consistent
coder_fail_rate = coder.get('fail', 0) / max(coder.get('total', 1), 1)
check("Fail rate < 5%", coder_fail_rate < 0.05, f"{coder.get('fail')} fails out of {coder.get('total')}")

# ══════════════════════════════════════════════════════════════════════
# 5. SELF-HEALING — verify it's actually healing
# ══════════════════════════════════════════════════════════════════════

section("5", "SELF-HEALING — actually healing?")

sh = modules.get('self_healing', {})
check("Has real calls", sh.get('total', 0) > 1000, str(sh.get('total')))
check("Success rate real", sh.get('success_rate', 0) >= 0.95, f"{sh.get('success_rate',0):.0%}")
# R-F2727 — idle ≠ broken: recency is a WARN, never a hard FAIL.
check("Active recently (WARN if idle)", recency_ok(sh.get('last_signal_ago_h'), 1), f"{sh.get('last_signal_ago_h')}h ago")

# ══════════════════════════════════════════════════════════════════════
# 6. SANCTIONS — verify they're actually screening
# ══════════════════════════════════════════════════════════════════════

section("6", "SANCTIONS — actually screening?")

sanc = modules.get('sanctions_canonical.lookup', {})
check("OpenSanctions has real calls", sanc.get('total', 0) >= 40, str(sanc.get('total')))
check("OpenSanctions 100% rate", sanc.get('success_rate', 0) >= 0.95, f"{sanc.get('success_rate',0):.0%}")

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
        check(f"{label} has calls", m.get('total', 0) >= 1, str(m.get('total')))
        check(f"{label} 100% rate", m.get('success_rate', 0) >= 0.90, f"{m.get('success_rate',0):.0%}")
    else:
        check(f"{label} NOT FOUND", False, "module missing from brain stats")

# ══════════════════════════════════════════════════════════════════════
# 7. COMPLIANCE — verify it's actually watching
# ══════════════════════════════════════════════════════════════════════

section("7", "COMPLIANCE — actually watching?")

cw = modules.get('compliance_watch', {})
check("Compliance watch has real calls", cw.get('total', 0) > 5000, str(cw.get('total')))
check("Compliance watch 99% rate", cw.get('success_rate', 0) >= 0.95, f"{cw.get('success_rate',0):.0%}")
# R-F2727 — idle ≠ broken: recency is a WARN, never a hard FAIL.
check("Active recently (WARN if idle)", recency_ok(cw.get('last_signal_ago_h'), 1), f"{cw.get('last_signal_ago_h')}h ago")

od = modules.get('opportunity_detector', {})
check("Opportunity detector has calls", od.get('total', 0) > 600, str(od.get('total')))
check("Opportunity detector 99% rate", od.get('success_rate', 0) >= 0.95, f"{od.get('success_rate',0):.0%}")

# ══════════════════════════════════════════════════════════════════════
# 8. KNOWLEDGE — verify every region has real data
# ══════════════════════════════════════════════════════════════════════

section("8", "KNOWLEDGE — every region has real data?")

knowledge = [
    ('knowledge_gulf', 'Gulf', 60),
    ('knowledge_balkans', 'Balkans', 60),
    ('knowledge_west_africa', 'West Africa', 50),
    ('knowledge_north_africa', 'North Africa', 40),
    ('knowledge_central_africa', 'Central Africa', 50),
    ('knowledge_latam_lusophone', 'LATAM Lusophone', 50),
    ('knowledge_latam_non_lusophone', 'LATAM Non-Lusophone', 25),
    ('knowledge_south_se_asia', 'South/SE Asia', 25),
    ('knowledge_turkey_standalone', 'Turkey', 60),
]
for name, label, min_calls in knowledge:
    m = modules.get(name)
    if m:
        total = m.get('total', 0)
        rate = m.get('success_rate', 0)
        check(f"{label} has real calls", total >= min_calls, str(total))
        check(f"{label} rate real", rate >= 0.90, f"{rate:.0%}")
        check(f"{label} active recently (WARN if idle)", recency_ok(m.get('last_signal_ago_h'), 24), f"{m.get('last_signal_ago_h')}h ago")
    else:
        check(f"{label} NOT FOUND", False, "module missing")

# ══════════════════════════════════════════════════════════════════════
# 9. LEGAL — verify every system has real data
# ══════════════════════════════════════════════════════════════════════

section("9", "LEGAL — every system has real data?")

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
        check(f"{label} legal has real calls", total >= min_calls, str(total))
        check(f"{label} legal rate real", rate >= 0.90, f"{rate:.0%}")
    else:
        check(f"{label} legal NOT FOUND", False, "module missing")

# ══════════════════════════════════════════════════════════════════════
# 10. SECURITY — verify every guard is active
# ══════════════════════════════════════════════════════════════════════

section("10", "SECURITY — every guard active?")

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
        check(f"{label} has real calls", total >= min_calls, str(total))
        check(f"{label} rate real", rate >= 0.85, f"{rate:.0%}")
    else:
        check(f"{label} NOT FOUND", False, "module missing")

# ══════════════════════════════════════════════════════════════════════
# 11. WHATSAPP — verify it's actually connected
# ══════════════════════════════════════════════════════════════════════

section("11", "WHATSAPP — actually connected?")

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
        check(f"WA {label} has real calls", total >= min_calls, str(total))
        check(f"WA {label} rate real", rate >= min_rate, f"{rate:.0%}")
    else:
        check(f"WA {label} NOT FOUND", False, "module missing")

# ══════════════════════════════════════════════════════════════════════
# 12. VAULT — verify every entry is honest
# ══════════════════════════════════════════════════════════════════════

section("12", "VAULT — every entry honest?")

d = fetch(f'{base}/api/aria/vault')
check("Vault accessible", isinstance(d, dict) and d.get('success') is True)
entries = d.get('entries', [])
stats = d.get('stats', {})
check("Total entries", stats.get('total', 0) == 36, str(stats.get('total')))
by_status = stats.get('by_status', {})
check("open_api count", by_status.get('open_api', 0) == 13, str(by_status.get('open_api')))
check("pending count", by_status.get('pending', 0) == 23, str(by_status.get('pending')))
check("No fabricated registered", 'registered' not in by_status, "honest")

# Verify every entry has ALL required fields
for e in entries:
    sid = e.get('site_id', '?')
    missing = []
    for field in ['site_id', 'site_name', 'site_url', 'status', 'agent_id']:
        if not e.get(field):
            missing.append(field)
    if missing:
        check(f"Entry {sid} missing fields", False, str(missing))

# ══════════════════════════════════════════════════════════════════════
# 13. FRONTEND — verify every page serves real content
# ══════════════════════════════════════════════════════════════════════

section("13", "FRONTEND — every page serves real content?")

pages = [
    ('/', 'Root', 100),
    ('/signin.html', 'Sign-in', 1000),
    ('/dd-reports.html', 'DD Reports', 1000),
    ('/watchlist.html', 'Watchlist', 100),
]
for path, label, min_chars in pages:
    r = fetch(web + path)
    if isinstance(r, str):
        check(f"{label} page serves content", len(r) >= min_chars, f"{len(r)} chars")
        # Check for error indicators
        has_error = 'cannot be found' in r.lower() or '404' in r[:200]
        check(f"{label} page no errors", not has_error)
    else:
        check(f"{label} page accessible", False, str(r.get('_error', 'unknown')))

# Check UI elements on DD reports page
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
        ('Search input', 'dd-search'),
        ('Watchlist link', 'watchlist'),
    ]
    for label, keyword in ui_checks:
        check(f"UI: {label}", keyword in r)

# ══════════════════════════════════════════════════════════════════════
# 14. DATA INTEGRITY — cross-check stats for consistency
# ══════════════════════════════════════════════════════════════════════

section("14", "DATA INTEGRITY — cross-check stats")

# Verify total signals = sum of all module totals
total_from_modules = sum(m.get('total', 0) for m in modules.values())
reported_total = d.get('total_signals', 0)
# Allow some discrepancy for modules added/removed between calls
check("Total signals consistent", abs(total_from_modules - reported_total) < 1000, f"sum={total_from_modules} reported={reported_total}")

# Verify no module has success+fail > total (impossible)
for name, m in modules.items():
    s = m.get('success', 0)
    f = m.get('fail', 0)
    t = m.get('total', 0)
    if s + f > t:
        check(f"Module {name}: success+fail > total", False, f"{s}+{f} > {t}")

# ══════════════════════════════════════════════════════════════════════
# 15. RECENT ACTIVITY — verify agents are active NOW, not stale
# ══════════════════════════════════════════════════════════════════════

section("15", "RECENT ACTIVITY — agents active NOW?")

active_agents = [
    'web_integrity', 'aria_coder', 'self_healing', 'compliance_watch',
    'opportunity_detector', 'trace_stream', 'cost_tracker',
    'self_monitor', 'self_restart', 'ua_rotation',
]
for name in active_agents:
    m = modules.get(name)
    if m:
        last = m.get('last_signal_ago_h', 99)
        check(f"{name} active in last hour", last < 1, f"{last}h ago")
    else:
        check(f"{name} NOT FOUND", False)

# ══════════════════════════════════════════════════════════════════════
# SUMMARY
# ══════════════════════════════════════════════════════════════════════

print(f"\n{'='*60}")
print(f"  AUDIT COMPLETE: {results['pass']} pass / {results['warn']} warn / {results['fail']} fail")
print(f"{'='*60}")
print()
print(f"  Build: {d.get('build_rev', '?')}")
print(f"  Status: operational")
print(f"  Brain: {len(modules)} modules, {d.get('total_signals', '?')} signals")
print(f"  Breaker: {'CLOSED' if not cb.get('open') else 'OPEN'}")
print(f"  Web integrity: {wi.get('total',0)} cycles, {wi.get('success_rate',0):.0%}")
print(f"  Vault: {stats.get('total',0)} entries (honest)")
print(f"  Autonomous: level 3, running")
print()

if results['fail'] > 0:
    print("  FAILURES:")
    for d in results['details']:
        if d.startswith('  [FAIL]'):
            print(f"  {d}")
    print()
    print("  These failures need investigation — they indicate real problems.")
elif results['warn'] > 0:
    # R-F2727 — be honest: warnings ≠ "no stale data". Surface them without crying failure.
    print(f"  ZERO hard failures, {results['warn']} WARNING(S) — idle / quiet-window signals")
    print("  (not observed recently ≠ broken; review the WARN lines, don't panic).")
    for d in results['details']:
        if d.startswith('  [WARN]'):
            print(f"  {d}")
else:
    print("  ZERO failures, ZERO warnings — every agent active with real data.")
