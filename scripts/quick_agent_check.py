"""Quick agent health check."""
import urllib.request, json, ssl, time
ctx = ssl.create_default_context()
base = 'https://aria-intel.fly.dev'

for i in range(3):
    try:
        d = json.loads(urllib.request.urlopen(base + '/api/aria/brain/stats', context=ctx, timeout=15).read())
        modules = d.get('modules', {})
        agents = [
            'web_integrity', 'aria_coder', 'self_healing', 'self_improve',
            'autonomous_engine', 'compliance_watch', 'opportunity_detector',
            'sanctions_canonical.lookup', 'wa_notifier', 'security',
            'self_monitor', 'self_restart', 'self_infra_detector',
            'self_diagnostic', 'ua_rotation', 'url_safety',
            'web_search', 'news_monitor', 'intel_ledger', 'mistake_ledger',
            'grounded_reasoner', 'investigation_thread',
            'company_investigator', 'capability_card',
            'tool_claim_guard', 'topic_completion',
        ]
        for name in agents:
            m = modules.get(name)
            if m:
                total = m.get('total', 0)
                rate = m.get('success_rate', 0)
                print(f'  {name}: {total} calls, {rate:.0%} rate')
            else:
                print(f'  {name}: NOT FOUND')
        cb = d.get('circuit_breaker', {})
        print(f'  Breaker open: {cb.get("open")}, drops: {cb.get("drops_total")}')
        print(f'  Total signals: {d.get("total_signals")}')
        print(f'  Healthy modules: {d.get("healthy_count")}')
        break
    except Exception as e:
        print(f'Attempt {i+1}: {e}')
        time.sleep(5)
