"""Diagnose every failure point in the portal registration pipeline."""
import sys, os, json, asyncio
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'aria_service'))
os.environ['ARIA_DATA_DIR'] = os.path.join(os.path.dirname(__file__), '..', 'data')

async def main():
    from intel.portal_registry import (
        register_for_portal, PORTALS, is_registered,
        assert_real_identity, _ARIA_EMAIL, _ARIA_NAME,
    )
    
    # First, fix the identity issue by checking env
    import os as _os
    env_name = _os.environ.get('ARIA_PORTAL_NAME', '')
    env_email = _os.environ.get('ARIA_PORTAL_EMAIL', '')
    print(f"ENV ARIA_PORTAL_NAME: {env_name or '(not set)'}")
    print(f"ENV ARIA_PORTAL_EMAIL: {env_email or '(not set)'}")
    print(f"CODE _ARIA_NAME: {_ARIA_NAME}")
    print(f"CODE _ARIA_EMAIL: {_ARIA_EMAIL}")
    
    valid, reason = assert_real_identity(_ARIA_EMAIL, _ARIA_NAME)
    print(f"Identity assertion: valid={valid}")
    if not valid:
        print(f"  REASON: {reason}")
        print(f"  FIX: The name must contain 'arkmurus'. Set ARIA_PORTAL_NAME env var.")
        print()
    
    # Test each portal and categorize the failure
    results = {
        'identity_fail': [],
        'captcha': [],
        'already_registered': [],
        'email_verify_needed': [],
        'form_fill_needed': [],
        'api_key_needed': [],
        'other_fail': [],
        'success': [],
    }
    
    for portal in PORTALS:
        if portal.registration_type == 'none':
            continue
        
        if not valid:
            results['identity_fail'].append(portal.id)
            continue
        
        if portal.requires_captcha:
            results['captcha'].append(portal.id)
            continue
        
        registered = await is_registered(portal.id)
        if registered:
            results['already_registered'].append(portal.id)
            continue
        
        try:
            result = await register_for_portal(portal.id)
            if result.get('success'):
                results['success'].append(portal.id)
            elif result.get('requires_operator'):
                results['captcha'].append(portal.id)
            elif result.get('requires_email_verify'):
                results['email_verify_needed'].append(portal.id)
            elif result.get('requires_form_fill'):
                results['form_fill_needed'].append(portal.id)
            elif portal.registration_type == 'api_key':
                results['api_key_needed'].append(portal.id)
            else:
                results['other_fail'].append(portal.id)
                print(f"  {portal.id}: {result.get('error','unknown')[:100]}")
        except Exception as e:
            results['other_fail'].append(portal.id)
            print(f"  {portal.id}: EXCEPTION: {e}")
    
    print()
    print("=== REGISTRATION DIAGNOSIS ===")
    for category, portals in results.items():
        if portals:
            print(f"  {category}: {len(portals)}")
            for p in portals[:5]:
                print(f"    - {p}")
            if len(portals) > 5:
                print(f"    ... and {len(portals)-5} more")
    
    print()
    print("=== WHAT NEEDS TO HAPPEN ===")
    print(f"  1. Fix identity: Set ARIA_PORTAL_NAME env var to include 'Arkmurus'")
    print(f"  2. CAPTCHA portals ({len(results['captcha'])}): Need operator to manually register")
    print(f"  3. Email verify ({len(results['email_verify_needed'])}): Need IMAP email reader configured")
    print(f"  4. Form fill ({len(results['form_fill_needed'])}): Playwright form fill - needs debugging")
    print(f"  5. API key ({len(results['api_key_needed'])}): Need operator to obtain API keys")
    print(f"  6. Other fails ({len(results['other_fail'])}): Need investigation")

if __name__ == '__main__':
    asyncio.run(main())
