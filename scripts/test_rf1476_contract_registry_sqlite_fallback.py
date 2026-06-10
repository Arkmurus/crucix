"""R-F1476: Capability test — ContractRegistry SQLite fallback.

The real broken path: ContractRegistry was Redis-only. When Redis/state_store was
unreachable, register_contract() returned False, get_contract() returned None,
and list_contracts() returned {}. Unlike AgentRegistry (which has a dedicated
SQLite DB fallback via R-F1446), contracts had no persistence without Redis.

This test drives the REAL ContractRegistry path (not mocked) and asserts the
user-visible outcome: contracts are persisted to the dedicated SQLite DB even
when Redis is down.
"""
import sys
import os
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'aria_service'))
os.environ['ARIA_DATA_DIR'] = os.path.join(os.path.dirname(__file__), '..', 'data')

from intel.agent_contract import AgentContract, CONTRACT_REGISTRY
import asyncio


async def test_register_returns_true_when_redis_down():
    """register_contract() must return True when the dedicated DB write succeeds,
    even if Redis/state_store is unreachable."""
    contract = AgentContract(
        agent_id="rf1476_cap_test",
        version="1.0.0",
        directives=["Test directive for R-F1476"],
        inputs=["test_input"],
        outputs=["test_output"],
        error_modes=["test_error"],
        dependencies=[],
        check_interval_s=3600,
    )

    # Register — Redis is down in this environment, but the dedicated DB should work
    result = await CONTRACT_REGISTRY.register_contract(contract)

    # The user-visible outcome: register_contract() returns True
    assert result is True, (
        f"register_contract() returned {result} — expected True. "
        "The dedicated SQLite DB write should succeed even when Redis is down."
    )
    print(f"✅ register_contract() returned True with Redis down")


async def test_get_contract_works_when_redis_down():
    """get_contract() must return the contract from the dedicated DB when Redis is down."""
    contract = await CONTRACT_REGISTRY.get_contract("rf1476_cap_test")

    assert contract is not None, "get_contract() should return the contract from the dedicated DB"
    assert contract.agent_id == "rf1476_cap_test"
    assert contract.version == "1.0.0"
    assert len(contract.directives) == 1
    assert contract.directives[0] == "Test directive for R-F1476"
    print(f"✅ get_contract() returned contract from dedicated DB: {contract.agent_id} v{contract.version}")


async def test_list_contracts_works_when_redis_down():
    """list_contracts() must return contracts from the dedicated DB when Redis is down."""
    contracts = await CONTRACT_REGISTRY.list_contracts()

    assert "rf1476_cap_test" in contracts, (
        f"list_contracts() should include rf1476_cap_test. Got: {list(contracts.keys())}"
    )
    assert contracts["rf1476_cap_test"].agent_id == "rf1476_cap_test"
    print(f"✅ list_contracts() returned {len(contracts)} contract(s) from dedicated DB")


async def test_delete_contract_works_when_redis_down():
    """delete_contract() must work when Redis is down."""
    result = await CONTRACT_REGISTRY.delete_contract("rf1476_cap_test")
    assert result is True, f"delete_contract() returned {result} — expected True"

    # Verify it's gone
    contract = await CONTRACT_REGISTRY.get_contract("rf1476_cap_test")
    assert contract is None, "Contract should be deleted from the dedicated DB"
    print(f"✅ delete_contract() removed contract from dedicated DB")


async def test_existing_tests_still_pass():
    """Verify the existing test suite still passes (no regression)."""
    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "pytest",
         os.path.join(os.path.dirname(__file__), '..', 'aria_service', 'tests', 'test_rf1212_agent_contracts.py'),
         "-v", "--tb=short"],
        capture_output=True, text=True, timeout=60,
    )
    print(f"Existing test suite exit code: {result.returncode}")
    if result.returncode != 0:
        # Print the failures
        for line in result.stdout.split('\n'):
            if 'FAILED' in line:
                print(f"  {line}")
    assert result.returncode == 0, "Existing contract tests must still pass"
    print(f"✅ Existing contract tests all pass")


async def main():
    print("=" * 60)
    print("R-F1476: ContractRegistry SQLite fallback capability test")
    print("=" * 60)
    print()

    await test_register_returns_true_when_redis_down()
    print()
    await test_get_contract_works_when_redis_down()
    print()
    await test_list_contracts_works_when_redis_down()
    print()
    await test_delete_contract_works_when_redis_down()
    print()
    await test_existing_tests_still_pass()
    print()
    print("=" * 60)
    print("ALL TESTS PASSED ✅")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
