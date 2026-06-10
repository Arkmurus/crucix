"""Probe contract registry — check Redis-only vs SQLite fallback gap."""
import sys
import os
import json
import asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'aria_service'))
os.environ['ARIA_DATA_DIR'] = os.path.join(os.path.dirname(__file__), '..', 'data')


async def main():
    from intel.agent_contract import AgentContract, CONTRACT_REGISTRY

    print("=== CONTRACT REGISTRY PROBE ===")

    # Register
    contract = AgentContract(
        agent_id='probe_test_contract',
        version='1.0.0',
        directives=['Test directive'],
    )
    ok = await CONTRACT_REGISTRY.register_contract(contract)
    print(f"Register contract: {ok}")

    # Get
    got = await CONTRACT_REGISTRY.get_contract('probe_test_contract')
    print(f"Get contract: {got}")

    # List
    contracts = await CONTRACT_REGISTRY.list_contracts()
    print(f"List contracts: {len(contracts)}")

    # Validate
    violations = await CONTRACT_REGISTRY.validate_contract('probe_test_contract')
    print(f"Validate: {len(violations)} violations")
    for v in violations:
        print(f"  {v.violation_type}: {v.description}")

    # Cleanup
    await CONTRACT_REGISTRY.delete_contract('probe_test_contract')
    print("Cleaned up")

    print("\n=== FINDING ===")
    print("Contract registry has NO SQLite fallback (unlike AgentRegistry)")
    print("When Redis is down: register() returns False, get() returns None")
    print("list_contracts() also fails because scan_keys() is Redis-only")
    print("This is a DEGRADED state — contracts are not persisted without Redis")


if __name__ == '__main__':
    asyncio.run(main())
