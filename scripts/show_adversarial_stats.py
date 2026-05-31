"""Fetch and display adversarial stats from live server."""
import json
import urllib.request

resp = urllib.request.urlopen(
    "https://aria-intel.fly.dev/api/aria/adversarial/stats", timeout=15
)
data = json.loads(resp.read())

print("=== 4-Week Trend ===")
trend = data.get("four_week_trend", [])
if trend:
    for run in trend:
        dt = run.get("run_at", "?")[:19]
        p = run.get("passed", 0)
        t = run.get("total_attacks", 0)
        bs = run.get("base_score", 0) * 100
        os_val = run.get("overall_score", 0)
        print(f"  {dt}  {p}/{t} passed ({bs:.0f}%)  overall={os_val:.3f}")
else:
    print("  (no trend data — only 1 run persisted)")

print()
print("=== Last Run ===")
lr = data.get("last_run", {})
if lr:
    print(f"  Run at:      {lr.get('run_at', '?')}")
    print(f"  Result:      {lr.get('passed', 0)}/{lr.get('total_attacks', 0)} passed")
    print(f"  Base score:  {lr.get('base_score', 0)*100:.1f}%")
    print(f"  Overall:     {lr.get('overall_score', 0):.3f}")
    print(f"  Critical:    {lr.get('critical_failures', 0)}")
    print()
    print("  By category:")
    for cat, s in lr.get("by_category", {}).items():
        print(f"    {cat}:  {s['passed']}/{s['total']} ({s['score']*100:.0f}%)")
else:
    print("  (no runs yet)")

print()
print(f"Pending amendments: {data.get('pending_amendments', 0)}")
print(f"Regression count:   {data.get('regression_count', 0)}")
