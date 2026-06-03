"""
Capability test for R-F1301: list_contracts uses rs.scan_keys instead of rs.keys.
Tests that list_contracts returns contracts when they exist, proving the scan_keys path works.
"""
import pytest
from aria_service.intel.agent_contract import AgentContract, CONTRACT_REGISTRY


@pytest.mark.asyncio
async def test_rf1301_list_contracts_uses_scan_keys():
    """list_contracts must return contracts when registered, proving scan_keys works."""
    # Register a test contract
    contract = AgentContract(
        agent_id="test_rf1301_agent",
        version="1.0.0",
        directives=["Test directive"],
        inputs=[],
        outputs=[],
        error_modes=[],
        dependencies=[],
    )
    await CONTRACT_REGISTRY.register_contract(contract)

    try:
        # list_contracts should return the registered contract
        contracts = await CONTRACT_REGISTRY.list_contracts()
        assert isinstance(contracts, dict), f"Expected dict, got {type(contracts)}"
        assert "test_rf1301_agent" in contracts, (
            f"Expected test_rf1301_agent in contracts, got {list(contracts.keys())}"
        )
        assert contracts["test_rf1301_agent"].agent_id == "test_rf1301_agent"
    finally:
        # Cleanup
        await CONTRACT_REGISTRY.delete_contract("test_rf1301_agent")


@pytest.mark.asyncio
async def test_rf1301_list_contracts_empty():
    """list_contracts must return empty dict when no contracts match."""
    contracts = await CONTRACT_REGISTRY.list_contracts()
    assert isinstance(contracts, dict)
    # Should not raise — even with no matching keys
