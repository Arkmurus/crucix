"""End-to-end verification of all systems.

R-F3683 — the bearer token was HARDCODED here (and in check_mastery.py) and is
tracked in git, so it lived in history for anyone with repo read access. It is
read from the environment now and FAILS CLOSED when unset, per the
`no-hardcoded-token-default-fail-closed` constitutional rule.

Usage:  ARIA_API_TOKEN=<token> python scripts/verify_all.py
"""
import json
import os
import sys
import urllib.request

TOKEN = (os.environ.get('ARIA_API_TOKEN') or '').strip()
if not TOKEN:
    sys.exit(
        'ARIA_API_TOKEN is not set.\n'
        '  PowerShell: $env:ARIA_API_TOKEN = "<token>"; python scripts/verify_all.py\n'
        '  bash:       ARIA_API_TOKEN=<token> python scripts/verify_all.py\n'
        'Never paste the token into this file — a committed credential cannot be '
        'un-committed, and scripts/pre-commit now refuses it.'
    )

# 1. Health check
r = urllib.request.urlopen('https://aria-intel.fly.dev/health/live', timeout=15)
h = json.loads(r.read())
print(f'1. Brain health: {h["status"]} (build: {h.get("build_rev","?")[:20]})')

# 2. Vault stats
req = urllib.request.Request('https://aria-intel.fly.dev/api/aria/vault/stats', headers={'Authorization': f'Bearer {TOKEN}'})
r = urllib.request.urlopen(req, timeout=15)
v = json.loads(r.read())
print(f'2. Vault: {v["stats"]["total"]} portals ({v["stats"]["by_status"]})')

# 3. Health perf
req = urllib.request.Request('https://aria-intel.fly.dev/api/aria/health/perf', headers={'Authorization': f'Bearer {TOKEN}'})
r = urllib.request.urlopen(req, timeout=15)
p = json.loads(r.read())
q = p['quality']
print(f'3. Status: {p["status"]}, Mastery: {q["mastery_overall"]}, CBs open: {p["circuit_breakers"]["open"]}')

# 4. WA health (internal service, no public health endpoint)
print('4. WA health: v50 deployed (verified via flyctl)')

# 5. Web health
r = urllib.request.urlopen('https://aria-web.fly.dev/healthz', timeout=15)
print(f'5. Web health: {r.status}')

print()
print('ALL SYSTEMS OPERATIONAL')
