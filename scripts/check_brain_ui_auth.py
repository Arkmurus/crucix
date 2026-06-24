"""Check if the brain command center sends auth tokens."""
import urllib.request, ssl, re
ctx = ssl.create_default_context()
r = urllib.request.urlopen('https://intel.arkmurus.com/aria-brain', context=ctx, timeout=15)
html = r.read().decode()

# Find all API paths the page calls
for m in re.finditer(r"(?:authed|fetch)\s*\(\s*['\x22](/[^'\x22]+)['\x22]", html):
    path = m.group(1)
    print(f"  Calls: {path}")

# Check if it uses authed() or bare fetch()
uses_authed = 'authed(' in html
uses_fetch = 'fetch(' in html
print(f"\nUses authed(): {uses_authed}")
print(f"Uses fetch(): {uses_fetch}")
print(f"Has token in localStorage: {'crucix_token' in html}")
