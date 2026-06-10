"""Probe vault API routes."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'aria_service'))
os.environ['ARIA_DATA_DIR'] = os.path.join(os.path.dirname(__file__), '..', 'data')

from fastapi.testclient import TestClient
from aria_service.main import app

client = TestClient(app)

# Check what's in the vault
r = client.get('/api/aria/vault')
data = r.json()
print(f"Entries: {len(data.get('entries', []))}")
for e in data.get('entries', []):
    print(f"  {e['site_id']}: {e['site_name']} ({e['status']})")

# Try recording
r2 = client.post('/api/aria/vault', json={
    'site_id': 'api_test_site',
    'site_name': 'API Test Site',
    'site_url': 'https://api-test.gov',
    'agent_id': 'test_agent',
    'status': 'pending',
})
print(f"POST status: {r2.status_code}")
body = r2.json()
print(f"POST body: {body}")

# The test asserts data['success'] is True
# If the site already exists, it returns success=False with an error
# Let's check if it already exists
if not body.get('success'):
    print(f"  Reason: {body.get('error', 'unknown')}")
