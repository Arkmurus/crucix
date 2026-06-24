"""Check current mastery state."""
import json
import urllib.request

TOKEN = 'TPWspa3T5esw2YVh5Y7wemddnSSiLQAxZUz120u5uvk'

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
