"""Check current mastery state.

R-F3683 — see scripts/verify_all.py: this token was hardcoded and tracked in
git. Read from the environment, fail closed when unset.

Usage:  ARIA_API_TOKEN=<token> python scripts/check_mastery.py
"""
import json
import os
import sys
import urllib.request

TOKEN = (os.environ.get('ARIA_API_TOKEN') or '').strip()
if not TOKEN:
    sys.exit(
        'ARIA_API_TOKEN is not set.\n'
        '  PowerShell: $env:ARIA_API_TOKEN = "<token>"; python scripts/check_mastery.py\n'
        '  bash:       ARIA_API_TOKEN=<token> python scripts/check_mastery.py'
    )

req = urllib.request.Request(
    'https://aria-intel.fly.dev/api/aria/health/perf',
    headers={'Authorization': f'Bearer {TOKEN}'}
)
r = urllib.request.urlopen(req, timeout=15)
data = json.loads(r.read())
q = data['quality']
print(f'Mastery overall: {q["mastery_overall"]}')
print(f'Core mastery: {q["core_mastery"]}')
print(f'Weak topics: {q["core_weak_topics"]}')
print(f'Degraded: {data["degraded_reasons"]}')
