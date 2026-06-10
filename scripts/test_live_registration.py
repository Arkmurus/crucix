"""Test if the live server can actually register portals."""
import sys, os, json, asyncio
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'aria_service'))
os.environ['ARIA_DATA_DIR'] = os.path.join(os.path.dirname(__file__), '..', 'data')

async def main():
    from intel.portal_registry import (
        register_for_portal, PORTALS, is_registered,
        assert_real_identity, _ARIA_EMAIL, _ARIA_NAME,
    )
    
    print(f"Configured name: {_ARIA_NAME}")
    print(f"Configured email: {_ARIA_EMAIL}")
    
    valid, reason = assert_real_identity(_ARIA_EMAIL, _ARIA_NAME)
    print(f"Identity assertion: valid={valid}, reason={reason}")
    
    if not valid:
        print()
        print("=== ROOT CAUSE ===")
        print("The identity assertion requires the name to contain 'arkmurus'.")
        print(f"But ARIA_PORTAL_NAME is '{_ARIA_NAME}' which doesn't.")
        print()
        print("Fix: Set ARIA_PORTAL_NAME to include 'Arkmurus', e.g.:")
        print("  ARIA_PORTAL_NAME='ARIA Research (Arkmurus Group)'")
        print("  ARIA_PORTAL_EMAIL='aria@arkmurus.com'")
        print()
        print("Or on Fly:")
        print("  flyctl secrets set ARIA_PORTAL_NAME='ARIA Research (Arkmurus Group)' -a aria-intel")
        return
    
    # Test a simple portal
    portal = next(p for p in PORTALS if p.id == 'govtribe')
    print(f"\nTesting: {portal.name}")
    print(f"  CAPTCHA: {portal.requires_captcha}")
    print(f"  Email verify: {portal.requires_email_verify}")
    
    registered = await is_registered(portal.id)
    print(f"  Already registered: {registered}")
    
    if not registered and not portal.requires_captcha:
        print("  Attempting registration...")
        result = await register_for_portal(portal.id)
        print(f"  Result: {json.dumps(result, indent=2)[:600]}")

if __name__ == '__main__':
    asyncio.run(main())
