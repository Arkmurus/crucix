"""Check which endpoints the brain command center tries to fetch."""
import urllib.request, ssl, re
ctx = ssl.create_default_context()
r = urllib.request.urlopen('https://imaria.io/aria-brain', context=ctx, timeout=15)
html = r.read().decode()

# Find all API paths the page fetches
paths = set()
for m in re.finditer(r"['\"]/([^'\"]+(?:api/|health|diagnostic|predictor|layer|autonomous)[^'\"]*)['\"]", html):
    paths.add('/' + m.group(1))

print("Endpoints the brain UI tries to fetch:")
for p in sorted(paths):
    print(f"  {p}")

print()
print("Testing each endpoint:")
base = 'https://aria-intel.fly.dev'
for p in sorted(paths):
    try:
        r2 = urllib.request.urlopen(base + p, context=ctx, timeout=10)
        print(f"  OK {p}: {r2.status}")
    except urllib.error.HTTPError as e:
        print(f"  FAIL {p}: HTTP {e.code}")
    except Exception as e:
        print(f"  FAIL {p}: {e}")
