"""Check why identity assertion fails."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'aria_service'))
os.environ['ARIA_DATA_DIR'] = os.path.join(os.path.dirname(__file__), '..', 'data')

from intel.portal_registry import (
    assert_real_identity, _ARIA_NAME, _ARIA_EMAIL,
    _ARIA_IDENTITY_NAME, _ARIA_IDENTITY_EMAIL,
)
print(f'ARIA_NAME: {_ARIA_NAME}')
print(f'ARIA_EMAIL: {_ARIA_EMAIL}')
print(f'ARIA_IDENTITY_NAME: {_ARIA_IDENTITY_NAME}')
print(f'ARIA_IDENTITY_EMAIL: {_ARIA_IDENTITY_EMAIL}')
print()

# Test what WOULD work
valid, reason = assert_real_identity('aria@arkmurus.com', 'Arkmurus Research')
print(f'Test 1 (Arkmurus Research): valid={valid}, reason={reason}')

valid, reason = assert_real_identity('aria@arkmurus.com', 'ARIA Research')
print(f'Test 2 (ARIA Research): valid={valid}, reason={reason}')

valid, reason = assert_real_identity('aria@arkmurus.com', 'Arkmurus Group Ltd')
print(f'Test 3 (Arkmurus Group Ltd): valid={valid}, reason={reason}')
