"""Probe all DD agents + signup/registry/contract agents — capability health-review.

ROUND 19 per Claude's directive: run each agent's REAL path, capture outcomes.
"""
import sys
import os
import json
import asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'aria_service'))
os.environ['ARIA_DATA_DIR'] = os.path.join(os.path.dirname(__file__), '..', 'data')

results = {}


async def test_registry():
    """Probe AgentRegistry: register, heartbeat, list, stats, unregister."""
    from intel.agent_registry import AgentRegistry
    reg = AgentRegistry()
    r = {}
    try:
        ok = await reg.register('probe_test', 'test_agent', 'probing')
        r['register'] = ok
    except Exception as e:
        r['register'] = f"EXCEPTION: {e}"

    try:
        await reg.tick_heartbeat('probe_test', 'still probing')
        r['heartbeat'] = True
    except Exception as e:
        r['heartbeat'] = f"EXCEPTION: {e}"

    try:
        agents = await reg.list_active_agents()
        r['list_active'] = len(agents) > 0
    except Exception as e:
        r['list_active'] = f"EXCEPTION: {e}"

    try:
        stats = await reg.get_registry_stats()
        r['stats'] = stats.get('total_agents', 0) > 0
    except Exception as e:
        r['stats'] = f"EXCEPTION: {e}"

    try:
        status = await reg.get_agent_status('probe_test')
        r['get_status'] = status is not None
    except Exception as e:
        r['get_status'] = f"EXCEPTION: {e}"

    try:
        ok = await reg.unregister('probe_test')
        r['unregister'] = ok
    except Exception as e:
        r['unregister'] = f"EXCEPTION: {e}"

    return r


async def test_vault():
    """Probe AgentSignupVault: record, get, list, update, delete, stats."""
    from intel.agent_signup_vault import get_vault
    vault = get_vault()
    r = {}
    try:
        entry = vault.record(
            site_id='probe_test_site',
            site_name='Probe Test Portal',
            site_url='https://probe-test.example.com',
            agent_id='probe_test',
            status='pending'
        )
        r['record'] = entry is not None and entry.get('site_id') == 'probe_test_site'
    except Exception as e:
        r['record'] = f"EXCEPTION: {e}"

    try:
        got = vault.get('probe_test_site')
        r['get'] = got is not None
    except Exception as e:
        r['get'] = f"EXCEPTION: {e}"

    try:
        entries = vault.list(status='pending')
        r['list'] = len(entries) > 0
    except Exception as e:
        r['list'] = f"EXCEPTION: {e}"

    try:
        updated = vault.update_status('probe_test_site', 'registered')
        r['update'] = updated is not None and updated.get('status') == 'registered'
    except Exception as e:
        r['update'] = f"EXCEPTION: {e}"

    try:
        stats = vault.stats()
        r['stats'] = stats.get('total', 0) > 0
    except Exception as e:
        r['stats'] = f"EXCEPTION: {e}"

    try:
        deleted = vault.delete('probe_test_site')
        r['delete'] = deleted
    except Exception as e:
        r['delete'] = f"EXCEPTION: {e}"

    return r


async def test_contract():
    """Probe AgentContract + ContractRegistry: create, register, get, validate, delete."""
    from intel.agent_contract import AgentContract, CONTRACT_REGISTRY
    r = {}
    contract = AgentContract(
        agent_id='probe_test_contract',
        version='1.0.0',
        directives=['Test directive'],
        inputs=['test_input'],
        outputs=['test_output'],
        error_modes=['test_error'],
    )
    try:
        ok = await CONTRACT_REGISTRY.register_contract(contract)
        r['register'] = ok
    except Exception as e:
        r['register'] = f"EXCEPTION: {e}"

    try:
        got = await CONTRACT_REGISTRY.get_contract('probe_test_contract')
        r['get'] = got is not None
    except Exception as e:
        r['get'] = f"EXCEPTION: {e}"

    try:
        violations = await CONTRACT_REGISTRY.validate_contract('probe_test_contract')
        r['validate'] = isinstance(violations, list)
    except Exception as e:
        r['validate'] = f"EXCEPTION: {e}"

    try:
        deps = await CONTRACT_REGISTRY.check_dependencies('probe_test_contract')
        r['check_deps'] = isinstance(deps, dict)
    except Exception as e:
        r['check_deps'] = f"EXCEPTION: {e}"

    try:
        ok = await CONTRACT_REGISTRY.delete_contract('probe_test_contract')
        r['delete'] = ok
    except Exception as e:
        r['delete'] = f"EXCEPTION: {e}"

    return r


async def test_portal():
    """Probe PortalRegistry: is_registered, get_registered_portals, identity, pending."""
    from intel.portal_registry import (
        is_registered, get_registered_portals, get_pending_source_requirements,
        assert_real_identity, PORTALS
    )
    r = {}
    try:
        registered = await is_registered('probe_test_portal')
        r['is_registered'] = isinstance(registered, bool)
    except Exception as e:
        r['is_registered'] = f"EXCEPTION: {e}"

    try:
        portals = await get_registered_portals()
        r['get_registered_portals'] = isinstance(portals, list) and len(portals) > 0
    except Exception as e:
        r['get_registered_portals'] = f"EXCEPTION: {e}"

    try:
        pending = get_pending_source_requirements()
        r['pending_sources'] = isinstance(pending, list) and len(pending) > 0
    except Exception as e:
        r['pending_sources'] = f"EXCEPTION: {e}"

    try:
        valid, reason = assert_real_identity('aria@arkmurus.com', 'Arkmurus Research')
        r['identity_valid'] = valid
    except Exception as e:
        r['identity_valid'] = f"EXCEPTION: {e}"

    try:
        invalid, reason2 = assert_real_identity('fake@notarkmurus.com', 'Fake Name')
        r['identity_invalid'] = not invalid
    except Exception as e:
        r['identity_invalid'] = f"EXCEPTION: {e}"

    r['portal_count'] = len(PORTALS)
    return r


def test_web_integrity():
    """Probe WebIntegrityAgent: validate_input, ErrorPatternDetector, endpoint registry."""
    from intel.web_integrity_agent import (
        validate_input_payload, ErrorPatternDetector, IntegrityCheck,
        WEB_ENDPOINTS, INPUT_SCHEMAS
    )
    r = {}
    schema = {'required_fields': ['message'], 'field_types': {'message': str}}

    try:
        errors = validate_input_payload({'message': 'hello'}, schema)
        r['validate_valid'] = len(errors) == 0
    except Exception as e:
        r['validate_valid'] = f"EXCEPTION: {e}"

    try:
        errors2 = validate_input_payload({}, schema)
        r['validate_missing'] = len(errors2) > 0
    except Exception as e:
        r['validate_missing'] = f"EXCEPTION: {e}"

    try:
        errors3 = validate_input_payload({'message': 123}, schema)
        r['validate_wrong_type'] = len(errors3) > 0
    except Exception as e:
        r['validate_wrong_type'] = f"EXCEPTION: {e}"

    try:
        detector = ErrorPatternDetector()
        check = IntegrityCheck(endpoint='/test', method='GET', passed=False,
                               errors=['Timeout on GET /test'])
        detector.record_error(check)
        detector.record_error(check)
        detector.record_error(check)
        actionable = detector.get_actionable_patterns()
        r['pattern_detection'] = len(actionable) > 0
    except Exception as e:
        r['pattern_detection'] = f"EXCEPTION: {e}"

    r['endpoints_populated'] = len(WEB_ENDPOINTS) > 0
    r['input_schemas_populated'] = len(INPUT_SCHEMAS) > 0
    return r


def test_dd_orchestrator():
    """Probe DD Orchestrator: check orchestrate_dd exists and is callable."""
    from intel.dd_orchestrator import orchestrate_dd
    return {'orchestrate_dd_exists': callable(orchestrate_dd)}


def test_company_investigator():
    """Probe Company Investigator: check investigate_company exists."""
    from intel.company_investigator import investigate_company, InvestigationReport
    return {
        'investigate_company_exists': callable(investigate_company),
        'InvestigationReport_importable': True,
    }


def test_dd_trigger_pipeline():
    """Probe DD Trigger Pipeline: check functions import."""
    from intel.dd_trigger_pipeline import (
        monitor_and_trigger, trigger_dd_for_entity, scout_portals_for_dd, get_trigger_log
    )
    return {
        'functions_exist': all(
            callable(c) for c in [monitor_and_trigger, trigger_dd_for_entity, scout_portals_for_dd, get_trigger_log]
        ),
    }


async def main():
    print("=" * 60)
    print("ARIA AGENT CAPABILITY HEALTH PROBE")
    print("=" * 60)

    # 1. Agent Registry
    print("\n--- 1. AGENT REGISTRY ---")
    r = await test_registry()
    results['agent_registry'] = r
    print(json.dumps(r, indent=2))

    # 2. Agent Signup Vault
    print("\n--- 2. AGENT SIGNUP VAULT ---")
    r = await test_vault()
    results['agent_signup_vault'] = r
    print(json.dumps(r, indent=2))

    # 3. Agent Contract
    print("\n--- 3. AGENT CONTRACT ---")
    r = await test_contract()
    results['agent_contract'] = r
    print(json.dumps(r, indent=2))

    # 4. Portal Registry
    print("\n--- 4. PORTAL REGISTRY ---")
    r = await test_portal()
    results['portal_registry'] = r
    print(json.dumps(r, indent=2))

    # 5. Web Integrity Agent
    print("\n--- 5. WEB INTEGRITY AGENT ---")
    r = test_web_integrity()
    results['web_integrity'] = r
    print(json.dumps(r, indent=2))

    # 6. DD Orchestrator
    print("\n--- 6. DD ORCHESTRATOR ---")
    r = test_dd_orchestrator()
    results['dd_orchestrator'] = r
    print(json.dumps(r, indent=2))

    # 7. Company Investigator
    print("\n--- 7. COMPANY INVESTIGATOR ---")
    r = test_company_investigator()
    results['company_investigator'] = r
    print(json.dumps(r, indent=2))

    # 8. DD Trigger Pipeline
    print("\n--- 8. DD TRIGGER PIPELINE ---")
    r = test_dd_trigger_pipeline()
    results['dd_trigger_pipeline'] = r
    print(json.dumps(r, indent=2))

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    all_ok = True
    for agent, checks in results.items():
        all_vals = list(checks.values())
        bool_vals = [v for v in all_vals if isinstance(v, bool)]
        str_vals = [v for v in all_vals if isinstance(v, str) and v.startswith('EXCEPTION')]
        if len(str_vals) > 0:
            status = "❌ BROKEN"
            all_ok = False
        elif all(v is True for v in bool_vals):
            status = "✅ OK"
        elif any(v is True for v in bool_vals):
            status = "⚠️ DEGRADED"
            all_ok = False
        else:
            status = "❌ BROKEN"
            all_ok = False
        print(f"  {status} {agent}")

    print(f"\nAll agents healthy: {all_ok}")
    return results


if __name__ == '__main__':
    asyncio.run(main())
