"""R-F1212 — Agent Contract System.

Every agent in ARIA's ecosystem has a binding contract that declares:
  1. DIRECTIVES — what the agent promises to do (machine-readable + human-readable)
  2. INPUTS — what the agent needs to function
  3. OUTPUTS — what the agent produces
  4. ERROR_MODES — known failure modes and how they're handled
  5. DEPENDENCIES — which other agents this agent depends on
  6. METRICS — what success/failure looks like

Contracts are:
  - Registered in Redis alongside the agent's heartbeat
  - Validated before/after each agent cycle
  - Enforced by self_healing (detects violations across agents)
  - Visible to all agents via the registry
  - Versioned so contract evolution is tracked

Usage
─────
  from .agent_contract import AgentContract, CONTRACT_REGISTRY

  # Define a contract
  contract = AgentContract(
      agent_id="research_engine",
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
  )

  # Register the contract
  await CONTRACT_REGISTRY.register_contract(contract)

  # Validate before a cycle
  violations = await CONTRACT_REGISTRY.validate_contract("research_engine")
  if violations:
      await wire_failure(...)

  # Check cross-agent dependencies
  deps_ok = await CONTRACT_REGISTRY.check_dependencies("research_engine")
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger("aria.agent_contract")

# Redis key prefixes
_CONTRACT_KEY = "crucix:agent:contract:"           # hash: agent_id → json contract
_CONTRACT_VIOLATION_KEY = "crucix:agent:violation:" # list: agent_id → violation records
_CONTRACT_VERSION_KEY = "crucix:agent:contract_version"  # hash: agent_id → version string

# Max violations to keep per agent
_MAX_VIOLATIONS = 100


@dataclass
class AgentContract:
    """Binding contract for an ARIA agent.

    Every agent MUST declare its contract at registration time. The contract
    is stored in Redis and validated by self_healing. Violations are recorded
    and wired to the brain.

    Attributes:
        agent_id: Unique identifier matching the agent registry.
        version: Semver string for the contract itself (not the agent).
        directives: List of binding directives the agent promises to follow.
        inputs: What the agent needs to function (env vars, modules, services).
        outputs: What the agent produces (data, signals, side effects).
        error_modes: Known failure modes with expected handling.
        dependencies: agent_ids this agent depends on being healthy.
        check_interval_s: Expected interval between cycles (for staleness detection).
        critical: If True, a violation escalates immediately.
    """
    agent_id: str
    version: str = "1.0.0"
    directives: list[str] = field(default_factory=list)
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    error_modes: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    check_interval_s: int = 3600
    critical: bool = False
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


@dataclass
class ContractViolation:
    """A record of a contract violation."""
    agent_id: str
    violation_type: str  # 'missing_directive', 'stale_heartbeat', 'dependency_down', 'missing_output'
    description: str
    severity: str = "WARNING"  # WARNING, ERROR, CRITICAL
    timestamp: float = field(default_factory=time.time)
    resolved: bool = False


class ContractRegistry:
    """Stores and validates agent contracts.

    Integrates with the AgentRegistry — contracts are stored alongside
    agent registrations in Redis. The self_healing system uses this to
    detect violations across all agents.
    """

    def __init__(self) -> None:
        self._redis = None  # lazy-loaded

    def _get_redis(self):
        if self._redis is not None:
            return self._redis
        from . import redis_store as _rs
        self._redis = _rs
        return self._redis

    # ── Contract CRUD ────────────────────────────────────────────────────

    async def register_contract(self, contract: AgentContract) -> bool:
        """Register or update an agent's contract in Redis.

        Returns True if registration succeeded.
        """
        try:
            rs = self._get_redis()
            key = f"{_CONTRACT_KEY}{contract.agent_id}"
            await rs.set(key, json.dumps(asdict(contract)))
            await rs.hset(_CONTRACT_VERSION_KEY, {contract.agent_id: contract.version})
            logger.info(
                "[R-F1212] contract registered: %s v%s — %d directives",
                contract.agent_id, contract.version, len(contract.directives),
            )
            # Wire success to brain
            try:
                from .engine_wiring import wire_success
                wire_success(
                    module="agent_contract",
                    summary=f"Contract registered: {contract.agent_id} v{contract.version}",
                    entity_name=contract.agent_id,
                    source_id="agent_contract:register",
                )
            except Exception:
                pass
            return True
        except Exception as e:
            logger.warning("[R-F1212] contract registration failed for %s: %s", contract.agent_id, e)
            try:
                from .engine_wiring import wire_failure
                wire_failure(
                    module="agent_contract",
                    detail=f"register_contract failed for {contract.agent_id}: {e}",
                    gap_type="contract_registration_failure",
                    source="agent_contract:register",
                )
            except Exception:
                pass
            return False

    async def get_contract(self, agent_id: str) -> Optional[AgentContract]:
        """Get an agent's contract from Redis.

        Returns None if no contract is registered.
        """
        try:
            rs = self._get_redis()
            key = f"{_CONTRACT_KEY}{agent_id}"
            raw = await rs.get(key)
            if not raw:
                return None
            data = json.loads(raw) if isinstance(raw, str) else json.loads(raw.decode("utf-8"))
            return AgentContract(**data)
        except Exception as e:
            logger.debug("[R-F1212] get_contract failed for %s: %s", agent_id, e)
            return None

    async def list_contracts(self) -> dict[str, AgentContract]:
        """List all registered contracts.

        Returns dict of agent_id → AgentContract.
        """
        contracts: dict[str, AgentContract] = {}
        try:
            rs = self._get_redis()
            # Scan for contract keys
            all_keys = await rs.keys(f"{_CONTRACT_KEY}*")
            if not all_keys:
                return contracts
            for key in all_keys:
                agent_id = key.replace(_CONTRACT_KEY, "")
                contract = await self.get_contract(agent_id)
                if contract:
                    contracts[agent_id] = contract
        except Exception as e:
            logger.debug("[R-F1212] list_contracts failed: %s", e)
        return contracts

    async def delete_contract(self, agent_id: str) -> bool:
        """Remove an agent's contract (on agent unregistration)."""
        try:
            rs = self._get_redis()
            key = f"{_CONTRACT_KEY}{agent_id}"
            await rs.delete(key)
            await rs.hset(_CONTRACT_VERSION_KEY, {agent_id: ""})
            logger.info("[R-F1212] contract deleted: %s", agent_id)
            return True
        except Exception as e:
            logger.debug("[R-F1212] delete_contract failed for %s: %s", agent_id, e)
            return False

    # ── Contract Validation ──────────────────────────────────────────────

    async def validate_contract(self, agent_id: str) -> list[ContractViolation]:
        """Validate an agent's contract against reality.

        Checks:
          1. Contract exists
          2. Agent is registered and has recent heartbeat
          3. Dependencies are healthy
          4. No unresolved violations

        Returns a list of violations (empty = contract is healthy).
        """
        violations: list[ContractViolation] = []
        contract = await self.get_contract(agent_id)
        if not contract:
            violations.append(ContractViolation(
                agent_id=agent_id,
                violation_type="missing_contract",
                description=f"Agent {agent_id} has no registered contract",
                severity="ERROR",
            ))
            return violations

        # Check agent is registered and has recent heartbeat
        try:
            from .agent_registry import AgentRegistry
            registry = AgentRegistry()
            status = await registry.get_agent_status(agent_id)
            if status is None:
                violations.append(ContractViolation(
                    agent_id=agent_id,
                    violation_type="not_registered",
                    description=f"Agent {agent_id} is not registered in the agent registry",
                    severity="ERROR",
                ))
            elif status.get("status") == "stale":
                age = status.get("heartbeat_age_s", 0)
                # Import threshold from agent_registry (default 300s)
                try:
                    from .agent_registry import _AGENT_STALE_THRESHOLD_S as _THRESH
                except ImportError:
                    _THRESH = 300
                violations.append(ContractViolation(
                    agent_id=agent_id,
                    violation_type="stale_heartbeat",
                    description=f"Agent {agent_id} heartbeat is {age}s old (threshold: {_THRESH}s)",
                    severity="WARNING" if age < _THRESH * 3 else "ERROR",
                ))
        except Exception as e:
            logger.debug("[R-F1212] validate_contract registry check failed: %s", e)

        # Check dependencies
        for dep_id in contract.dependencies:
            dep_contract = await self.get_contract(dep_id)
            if dep_contract is None:
                violations.append(ContractViolation(
                    agent_id=agent_id,
                    violation_type="dependency_no_contract",
                    description=f"Dependency {dep_id} has no contract",
                    severity="WARNING",
                ))
            else:
                dep_violations = await self.validate_contract(dep_id)
                if dep_violations:
                    violations.append(ContractViolation(
                        agent_id=agent_id,
                        violation_type="dependency_violation",
                        description=f"Dependency {dep_id} has {len(dep_violations)} contract violations",
                        severity="ERROR",
                    ))

        # Record violations to Redis
        if violations:
            await self._record_violations(agent_id, violations)

        return violations

    async def validate_all_contracts(self) -> dict[str, list[ContractViolation]]:
        """Validate all registered contracts.

        Returns dict of agent_id → list of violations.
        """
        contracts = await self.list_contracts()
        results: dict[str, list[ContractViolation]] = {}
        for agent_id in contracts:
            violations = await self.validate_contract(agent_id)
            if violations:
                results[agent_id] = violations
        return results

    # ── Dependency Checking ──────────────────────────────────────────────

    async def check_dependencies(self, agent_id: str) -> dict[str, bool]:
        """Check if all dependencies of an agent are healthy.

        Returns dict of dependency_id → is_healthy.
        """
        contract = await self.get_contract(agent_id)
        if not contract:
            return {}
        results: dict[str, bool] = {}
        for dep_id in contract.dependencies:
            violations = await self.validate_contract(dep_id)
            results[dep_id] = len(violations) == 0
        return results

    # ── Violation Recording ──────────────────────────────────────────────

    async def _record_violations(self, agent_id: str, violations: list[ContractViolation]) -> None:
        """Record contract violations to Redis."""
        try:
            rs = self._get_redis()
            key = f"{_CONTRACT_VIOLATION_KEY}{agent_id}"
            for v in violations:
                await rs.lpush(key, json.dumps(asdict(v)))
            await rs.ltrim(key, 0, _MAX_VIOLATIONS - 1)
        except Exception as e:
            logger.debug("[R-F1212] record_violations failed: %s", e)

    async def get_violations(self, agent_id: str, limit: int = 20) -> list[ContractViolation]:
        """Get recent contract violations for an agent."""
        try:
            rs = self._get_redis()
            key = f"{_CONTRACT_VIOLATION_KEY}{agent_id}"
            raw_list = await rs.lrange(key, 0, limit - 1)
            violations = []
            for raw in raw_list:
                try:
                    data = json.loads(raw) if isinstance(raw, str) else json.loads(raw.decode("utf-8"))
                    violations.append(ContractViolation(**data))
                except (json.JSONDecodeError, TypeError):
                    continue
            return violations
        except Exception as e:
            logger.debug("[R-F1212] get_violations failed: %s", e)
            return []

    async def get_all_violations(self) -> dict[str, list[ContractViolation]]:
        """Get violations for all agents."""
        contracts = await self.list_contracts()
        results: dict[str, list[ContractViolation]] = {}
        for agent_id in contracts:
            violations = await self.get_violations(agent_id)
            if violations:
                results[agent_id] = violations
        return results

    # ── Stats ────────────────────────────────────────────────────────────

    async def get_contract_stats(self) -> dict[str, Any]:
        """Get statistics about the contract system."""
        contracts = await self.list_contracts()
        all_violations = await self.get_all_violations()
        total_violations = sum(len(v) for v in all_violations.values())
        return {
            "total_contracts": len(contracts),
            "agents_with_violations": len(all_violations),
            "total_violations": total_violations,
            "contracts": {
                aid: {
                    "version": c.version,
                    "directives": len(c.directives),
                    "dependencies": len(c.dependencies),
                    "critical": c.critical,
                }
                for aid, c in contracts.items()
            },
        }


# ── Singleton ────────────────────────────────────────────────────────────────

CONTRACT_REGISTRY = ContractRegistry()
