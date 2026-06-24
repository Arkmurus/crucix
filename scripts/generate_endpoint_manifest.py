"""Generate endpoint manifest from the Python backend routes."""
import re, json

with open('aria_service/routes/aria.py', encoding='utf-8') as f:
    content = f.read()

routes = set()
for m in re.finditer(r'@router\.(get|post|put|delete)\(["\x27]([^"\x27?{]+)', content):
    method = m.group(1).upper()
    path = m.group(2).rstrip('/')
    routes.add(f'GET /api/aria{path}' if method == 'GET' else f'{method} /api/aria{path}')

# Also add non-prefixed routes
for m in re.finditer(r'@router\.(get|post|put|delete)\(["\x27]([^"\x27?{]+)', content):
    method = m.group(1).upper()
    path = m.group(2).rstrip('/')
    if not path.startswith('/api/'):
        routes.add(f'{method} /api/aria{path}')

manifest = sorted(routes)
print(f'Total endpoints: {len(manifest)}')
for r in manifest[:20]:
    print(r)
if len(manifest) > 20:
    print(f'... and {len(manifest)-20} more')

# Write manifest
with open('public/endpoint-manifest.json', 'w') as f:
    json.dump(manifest, f, indent=2)
print(f'\nWritten to public/endpoint-manifest.json')
