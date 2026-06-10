"""R-F1212 — Capability tests for the Agent Contract system.

Tests:
1. AgentContract dataclass creation and serialization
2. ContractRegistry register/get/list/delete
3. Contract validation (missing contract, stale heartbeat)
4. Dependency checking
5. Violation recording and retrieval
6. Contract stats
7. Dual-branch brain wiring in main.py loops
8. Contract registration via AgentRegistry.register()
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from aria_service.intel.agent_contract import (
    AgentContract,
    ContractViolation,
    ContractRegistry,
    CONTRACT_REGISTRY,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _read(path: str) -> str:
    """Read a file relative to repo root."""
    return (_REPO_ROOT / path).read_text(encoding="utf-8")


# ── Mock Redis for ContractRegistry tests ──────────────────────────────────


class _MockRedis:
    """Async stand-in that mimics the redis_store interface."""

    def __init__(self):
        self._data: dict[str, str | bytes] = {}
        self._hash: dict[str, dict[str, str]] = {}
        self._lists: dict[str, list[str]] = {}
        self._ttl: dict[str, float] = {}

    async def get(self, key):
        raw = self._data.get(key)
        if raw is None:
            return None
        expiry = self._ttl.get(key)
        if expiry and time.time() > expiry:
            self._data.pop(key, None)
            self._ttl.pop(key, None)
            return None
        return raw if isinstance(raw, bytes) else raw.encode("utf-8") if isinstance(raw, str) else raw

    async def set(self, key, value):
        self._data[key] = value
        return True

    async def delete(self, key):
        self._data.pop(key, None)
        self._ttl.pop(key, None)
        self._lists.pop(key, None)
        return True

    async def expire(self, key, seconds):
        self._ttl[key] = time.time() + seconds
        return True

    async def hset(self, key, mapping: dict):
        if key not in self._hash:
            self._hash[key] = {}
        self._hash[key].update(mapping)
        return True

    async def hgetall(self, key):
        return self._hash.get(key, {})

    async def lpush(self, key, value):
        if key not in self._lists:
            self._lists[key] = []
        self._lists[key].insert(0, value)
        return True

    async def lrange(self, key, start, stop):
        lst = self._lists.get(key, [])
        return lst[start:stop + 1] if stop >= 0 else lst[start:]

    async def ltrim(self, key, start, stop):
        lst = self._lists.get(key, [])
        self._lists[key] = lst[start:stop + 1] if stop >= 0 else lst[start:]
        return True

    async def keys(self, pattern: str):
        prefix = pattern.rstrip("*")
        return [k for k in self._data if k.startswith(prefix)]


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_redis():
    return _MockRedis()


@pytest.fixture
def registry(mock_redis):
    reg = ContractRegistry()
    reg._redis = mock_redis
    reg._db_clear()  # R-F1476: clear dedicated DB for test isolation
    return reg


@pytest.fixture
def sample_contract():
    return AgentContract(
        agent_id="test_agent",
        version="1.0.0",
        directives=[
            "Extract facts from RSS feeds every 30min",
            "Validate hypotheses against existing knowledge",
            "Wire both success and failure to the brain",
        ],
        inputs=["RSS feed URLs", "LLM provider"],
        outputs=["New facts", "Validated hypotheses"],
        error_modes=["feed_unreachable", "llm_unavailable", "parse_failure"],
        dependencies=[],
        check_interval_s=1800,
        critical=False,
    )


# ── Tests: AgentContract dataclass ──────────────────────────────────────────


class TestAgentContract:
    """AgentContract dataclass creation and behavior."""

    def test_create_minimal(self):
        """A minimal contract can be created with just agent_id."""
        c = AgentContract(agent_id="minimal")
        assert c.agent_id == "minimal"
        assert c.version == "1.0.0"
        assert c.directives == []
        assert c.dependencies == []
        assert c.critical is False

    def test_create_full(self, sample_contract):
        """A full contract has all fields populated."""
        assert sample_contract.agent_id == "test_agent"
        assert len(sample_contract.directives) == 3
        assert len(sample_contract.inputs) == 2
        assert len(sample_contract.outputs) == 2
        assert len(sample_contract.error_modes) == 3

    def test_serialization_roundtrip(self, sample_contract):
        """AgentContract can be serialized to dict and back."""
        from dataclasses import asdict
        data = asdict(sample_contract)
        restored = AgentContract(**data)
        assert restored.agent_id == sample_contract.agent_id
        assert restored.directives == sample_contract.directives
        assert restored.version == sample_contract.version


# ── Tests: ContractRegistry ────────────────────────────────────────────────


class TestContractRegistry:
    """ContractRegistry CRUD operations."""

    @pytest.mark.asyncio
    async def test_register_and_get(self, registry, sample_contract):
        """Register a contract and retrieve it."""
        ok = await registry.register_contract(sample_contract)
        assert ok, "Contract registration should succeed"

        retrieved = await registry.get_contract("test_agent")
        assert retrieved is not None
        assert retrieved.agent_id == "test_agent"
        assert len(retrieved.directives) == 3

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, registry):
        """Getting a nonexistent contract returns None."""
        retrieved = await registry.get_contract("nonexistent")
        assert retrieved is None

    @pytest.mark.asyncio
    async def test_list_contracts(self, registry):
        """List all registered contracts."""
        c1 = AgentContract(agent_id="agent_a")
        c2 = AgentContract(agent_id="agent_b")
        await registry.register_contract(c1)
        await registry.register_contract(c2)

        contracts = await registry.list_contracts()
        assert "agent_a" in contracts
        assert "agent_b" in contracts
        assert len(contracts) == 2

    @pytest.mark.asyncio
    async def test_delete_contract(self, registry, sample_contract):
        """Delete a contract."""
        await registry.register_contract(sample_contract)
        ok = await registry.delete_contract("test_agent")
        assert ok

        retrieved = await registry.get_contract("test_agent")
        assert retrieved is None

    @pytest.mark.asyncio
    async def test_register_twice_updates(self, registry, sample_contract):
        """Registering the same agent twice updates the contract."""
        await registry.register_contract(sample_contract)
        updated = AgentContract(
            agent_id="test_agent",
            version="2.0.0",
            directives=["New directive"],
        )
        await registry.register_contract(updated)

        retrieved = await registry.get_contract("test_agent")
        assert retrieved.version == "2.0.0"
        assert retrieved.directives == ["New directive"]


# ── Tests: Contract Validation ─────────────────────────────────────────────


class TestContractValidation:
    """Contract validation logic."""

    @pytest.mark.asyncio
    async def test_validate_missing_contract(self, registry):
        """Validating a nonexistent contract returns a violation."""
        violations = await registry.validate_contract("ghost")
        assert len(violations) == 1
        assert violations[0].violation_type == "missing_contract"
        assert violations[0].severity == "ERROR"

    @pytest.mark.asyncio
    async def test_validate_healthy_contract(self, registry, sample_contract):
        """A healthy contract with no dependencies returns no violations."""
        await registry.register_contract(sample_contract)
        violations = await registry.validate_contract("test_agent")
        # May have registry check violations (agent not actually running)
        # but should NOT have missing_contract or dependency violations
        types = [v.violation_type for v in violations]
        assert "missing_contract" not in types
        assert "dependency_violation" not in types

    @pytest.mark.asyncio
    async def test_validate_with_dependency(self, registry):
        """A contract with a missing dependency returns a violation."""
        parent = AgentContract(
            agent_id="parent",
            dependencies=["child"],
        )
        await registry.register_contract(parent)
        violations = await registry.validate_contract("parent")
        types = [v.violation_type for v in violations]
        assert "dependency_no_contract" in types


# ── Tests: Violation Recording ─────────────────────────────────────────────


class TestViolations:
    """Contract violation recording and retrieval."""

    @pytest.mark.asyncio
    async def test_record_and_get_violations(self, registry):
        """Record violations and retrieve them."""
        v = ContractViolation(
            agent_id="test_agent",
            violation_type="missing_directive",
            description="Agent has no directives",
            severity="ERROR",
        )
        await registry._record_violations("test_agent", [v])

        violations = await registry.get_violations("test_agent")
        assert len(violations) == 1
        assert violations[0].violation_type == "missing_directive"
        assert violations[0].severity == "ERROR"

    @pytest.mark.asyncio
    async def test_get_violations_empty(self, registry):
        """Getting violations for a clean agent returns empty list."""
        violations = await registry.get_violations("clean_agent")
        assert violations == []

    @pytest.mark.asyncio
    async def test_get_all_violations(self, registry):
        """Get violations across all agents."""
        # Register contracts first (get_all_violations iterates contracts)
        c1 = AgentContract(agent_id="a")
        c2 = AgentContract(agent_id="b")
        await registry.register_contract(c1)
        await registry.register_contract(c2)

        v1 = ContractViolation(agent_id="a", violation_type="type_a", description="desc")
        v2 = ContractViolation(agent_id="b", violation_type="type_b", description="desc")
        await registry._record_violations("a", [v1])
        await registry._record_violations("b", [v2])

        all_v = await registry.get_all_violations()
        assert "a" in all_v
        assert "b" in all_v


# ── Tests: Contract Stats ──────────────────────────────────────────────────


class TestContractStats:
    """Contract statistics."""

    @pytest.mark.asyncio
    async def test_get_stats_empty(self, registry):
        """Stats with no contracts returns zeros."""
        stats = await registry.get_contract_stats()
        assert stats["total_contracts"] == 0
        assert stats["total_violations"] == 0

    @pytest.mark.asyncio
    async def test_get_stats_with_contracts(self, registry):
        """Stats reflect registered contracts."""
        c = AgentContract(agent_id="test", directives=["d1", "d2"], critical=True)
        await registry.register_contract(c)
        stats = await registry.get_contract_stats()
        assert stats["total_contracts"] == 1
        assert stats["contracts"]["test"]["directives"] == 2
        assert stats["contracts"]["test"]["critical"] is True


# ── Tests: Dual-branch brain wiring in main.py ─────────────────────────────


class TestDualBranchWiring:
    """Every agent loop must wire both success and failure to the brain."""

    def _check_loop(self, source: str, agent_id: str) -> list[str]:
        """Check that an agent loop has both _wire_agent_success and _wire_agent_failure."""
        missing = []
        # Search ALL occurrences of _wire_agent_success and check if any has this agent_id
        has_success = False
        has_failure = False
        search_from = 0
        while True:
            idx = source.find("_wire_agent_success", search_from)
            if idx < 0:
                break
            chunk = source[idx:idx + 200]
            if f'"{agent_id}"' in chunk:
                has_success = True
                break
            search_from = idx + 1
        search_from = 0
        while True:
            idx = source.find("_wire_agent_failure", search_from)
            if idx < 0:
                break
            chunk = source[idx:idx + 200]
            if f'"{agent_id}"' in chunk:
                has_failure = True
                break
            search_from = idx + 1
        if not has_success:
            missing.append(f"success wiring for {agent_id}")
        if not has_failure:
            missing.append(f"failure wiring for {agent_id}")
        return missing

    def test_all_loops_have_dual_branch_wiring(self):
        """Every background loop must wire both success and failure."""
        source = _read("aria_service/main.py")
        loop_agents = [
            "research_engine",
            "self_improve",
            "student_quiz",
            "student_reading",
            "library_consolidation",
            "proactive_watch",
            "weekly_report",
            "watchlist_rescreen",
            "tender_monitor",
        ]
        all_missing = []
        for agent_id in loop_agents:
            missing = self._check_loop(source, agent_id)
            all_missing.extend(missing)

        assert not all_missing, (
            f"Missing dual-branch wiring: {', '.join(all_missing)}"
        )

    def test_wire_helpers_exist(self):
        """The _wire_agent_success and _wire_agent_failure helpers must exist."""
        source = _read("aria_service/main.py")
        assert "async def _wire_agent_success" in source, (
            "_wire_agent_success helper must be defined"
        )
        assert "async def _wire_agent_failure" in source, (
            "_wire_agent_failure helper must be defined"
        )
        assert "wire_success" in source, (
            "_wire_agent_success must call wire_success"
        )
        assert "wire_failure" in source, (
            "_wire_agent_failure must call wire_failure"
        )


# ── Tests: AgentContract module exists ──────────────────────────────────────


class TestContractModule:
    """The agent_contract module must exist and be importable."""

    def test_module_importable(self):
        """agent_contract module can be imported."""
        from aria_service.intel import agent_contract
        assert agent_contract.AgentContract is not None
        assert agent_contract.ContractRegistry is not None
        assert agent_contract.CONTRACT_REGISTRY is not None

    def test_contract_registry_in_agent_registry(self):
        """AgentRegistry.register accepts a contract parameter."""
        source = _read("aria_service/intel/agent_registry.py")
        assert "contract" in source, (
            "AgentRegistry.register must accept a contract parameter"
        )
        assert "CONTRACT_REGISTRY" in source, (
            "AgentRegistry must import CONTRACT_REGISTRY"
        )
        assert "register_contract" in source, (
            "AgentRegistry must call register_contract"
        )
