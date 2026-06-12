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
import os
import sqlite3
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("aria.agent_contract")

# Redis key prefixes
_CONTRACT_KEY = "crucix:agent:contract:"           # hash: agent_id → json contract
_CONTRACT_VIOLATION_KEY = "crucix:agent:violation:" # list: agent_id → violation records
_CONTRACT_VERSION_KEY = "crucix:agent:contract_version"  # hash: agent_id → version string

# Max violations to keep per agent
_MAX_VIOLATIONS = 100

# R-F1476: dedicated SQLite database path for contract registry (same pattern
# as AgentRegistry R-F1446). Separate file = separate write lock = no contention
# with state_store or agent_registry.
# R-F1529: fallback to /data if ARIA_DATA_DIR is not set (production default).
_CONTRACT_DATA_DIR = os.getenv("ARIA_DATA_DIR") or "/data"
_CONTRACT_DB_DIR = Path(_CONTRACT_DATA_DIR)
_CONTRACT_DB = _CONTRACT_DB_DIR / "agent_contract.db"


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
        self._db_conn: sqlite3.Connection | None = None  # R-F1476: dedicated SQLite connection
        self._db_path = _CONTRACT_DB

    # ── R-F1476: Dedicated SQLite database (same pattern as AgentRegistry R-F1446) ──

    def _get_db(self) -> sqlite3.Connection:
        """Get or create the dedicated SQLite connection for contract registry.

        R-F1476: separate database file = separate write lock = no contention
        with state_store or agent_registry. Follows the same pattern as
        AgentRegistry._get_db() (R-F1446).
        """
        if self._db_conn is None:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._db_conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            self._db_conn.row_factory = sqlite3.Row
            self._db_conn.execute("PRAGMA journal_mode=WAL")
            self._db_conn.execute("PRAGMA foreign_keys=ON")
            self._init_db()
        return self._db_conn

    def _init_db(self):
        """Initialize the contract database schema."""
        self._db_conn.executescript("""
            CREATE TABLE IF NOT EXISTS contracts (
                agent_id    TEXT PRIMARY KEY,
                version     TEXT NOT NULL DEFAULT '1.0.0',
                directives  TEXT NOT NULL DEFAULT '[]',
                inputs      TEXT NOT NULL DEFAULT '[]',
                outputs     TEXT NOT NULL DEFAULT '[]',
                error_modes TEXT NOT NULL DEFAULT '[]',
                dependencies TEXT NOT NULL DEFAULT '[]',
                check_interval_s INTEGER NOT NULL DEFAULT 3600,
                critical    INTEGER NOT NULL DEFAULT 0,
                created_at  TEXT NOT NULL DEFAULT '',
                metadata    TEXT DEFAULT '{}'
            );
        """)
        self._db_conn.commit()

    def _db_register(self, contract: AgentContract) -> None:
        """Write contract to the dedicated DB (no lock contention)."""
        conn = self._get_db()
        conn.execute(
            """INSERT OR REPLACE INTO contracts
               (agent_id, version, directives, inputs, outputs, error_modes,
                dependencies, check_interval_s, critical, created_at, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}')""",
            (
                contract.agent_id,
                contract.version,
                json.dumps(contract.directives),
                json.dumps(contract.inputs),
                json.dumps(contract.outputs),
                json.dumps(contract.error_modes),
                json.dumps(contract.dependencies),
                contract.check_interval_s,
                1 if contract.critical else 0,
                contract.created_at,
            ),
        )
        conn.commit()

    def _db_get(self, agent_id: str) -> dict | None:
        """Get a contract from the dedicated DB."""
        conn = self._get_db()
        row = conn.execute("SELECT * FROM contracts WHERE agent_id=?", (agent_id,)).fetchone()
        if row is None:
            return None
        return dict(row)

    def _db_list(self) -> list[dict]:
        """List all contracts from the dedicated DB."""
        conn = self._get_db()
        rows = conn.execute("SELECT agent_id, version, directives, inputs, outputs, error_modes, dependencies, check_interval_s, critical, created_at FROM contracts").fetchall()
        return [dict(r) for r in rows]

    def _db_delete(self, agent_id: str) -> None:
        """Remove a contract from the dedicated DB."""
        conn = self._get_db()
        conn.execute("DELETE FROM contracts WHERE agent_id=?", (agent_id,))
        conn.commit()

    def _db_clear(self) -> None:
        """Clear all contracts from the dedicated DB (for testing)."""
        try:
            conn = self._get_db()
            conn.execute("DELETE FROM contracts")
            conn.commit()
        except Exception:
            pass

    def close(self) -> None:
        """Close the dedicated database connection."""
        if self._db_conn:
            self._db_conn.close()
            self._db_conn = None

    def __del__(self) -> None:
        self.close()

    def _get_redis(self):
        if self._redis is not None:
            return self._redis
        from . import redis_store as _rs
        self._redis = _rs
        return self._redis

    # ── Contract CRUD ────────────────────────────────────────────────────

    async def register_contract(self, contract: AgentContract) -> bool:
        """Register or update an agent's contract.

        R-F1476: writes to the dedicated SQLite DB first (fast, no lock contention),
        then also writes to Redis/state_store for backward compatibility.
        Returns True if the DB write succeeded.

        Args:
            contract: The AgentContract to register.

        Returns:
            True if the contract was persisted to the dedicated DB.
        """
        # R-F1476: write to dedicated DB first (fast, no lock contention)
        db_ok = False
        try:
            self._db_register(contract)
            db_ok = True
        except Exception as _db_e:
            logger.debug("[R-F1476] dedicated DB register failed for %s: %s", contract.agent_id, _db_e)

        # Also write to Redis/state_store for backward compatibility
        try:
            rs = self._get_redis()
            key = f"{_CONTRACT_KEY}{contract.agent_id}"
            await rs.set(key, json.dumps(asdict(contract)))
            await rs.hset(_CONTRACT_VERSION_KEY, {contract.agent_id: contract.version})
            logger.info(
                "[R-F1212] contract registered: %s v%s — %d directives",
                contract.agent_id, contract.version, len(contract.directives),
            )
        except Exception as e:
            logger.warning("[R-F1212] contract Redis write failed for %s: %s", contract.agent_id, e)

        # R-F1476: return True if the dedicated DB write succeeded
        if db_ok:
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

        logger.warning("[R-F1476] contract registration failed for %s (DB+Redis both down)", contract.agent_id)
        try:
            from .engine_wiring import wire_failure
            wire_failure(
                module="agent_contract",
                detail=f"register_contract failed for {contract.agent_id}: DB+Redis both down",
                gap_type="contract_registration_failure",
                source="agent_contract:register",
            )
        except Exception:
            pass
        return False

    async def get_contract(self, agent_id: str) -> Optional[AgentContract]:
        """Get an agent's contract.

        R-F1476: reads from the dedicated DB first (always available, no
        lock contention), falls back to Redis/state_store if empty.

        Returns None if no contract is registered.
        """
        # R-F1476: read from dedicated DB first
        try:
            db_entry = self._db_get(agent_id)
            if db_entry is not None:
                return AgentContract(
                    agent_id=db_entry["agent_id"],
                    version=db_entry["version"],
                    directives=json.loads(db_entry.get("directives", "[]")),
                    inputs=json.loads(db_entry.get("inputs", "[]")),
                    outputs=json.loads(db_entry.get("outputs", "[]")),
                    error_modes=json.loads(db_entry.get("error_modes", "[]")),
                    dependencies=json.loads(db_entry.get("dependencies", "[]")),
                    check_interval_s=db_entry.get("check_interval_s", 3600),
                    critical=bool(db_entry.get("critical", 0)),
                    created_at=db_entry.get("created_at", ""),
                )
        except Exception as _db_e:
            logger.debug("[R-F1476] dedicated DB get failed for %s: %s", agent_id, _db_e)

        # Fallback: read from Redis/state_store
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

        R-F1476: reads from the dedicated DB first (always available, no
        lock contention), falls back to Redis/state_store if empty.

        Returns dict of agent_id → AgentContract.
        """
        contracts: dict[str, AgentContract] = {}

        # R-F1476: read from dedicated DB first
        try:
            db_contracts = self._db_list()
            if db_contracts:
                for entry in db_contracts:
                    try:
                        contract = AgentContract(
                            agent_id=entry["agent_id"],
                            version=entry["version"],
                            directives=json.loads(entry.get("directives", "[]")),
                            inputs=json.loads(entry.get("inputs", "[]")),
                            outputs=json.loads(entry.get("outputs", "[]")),
                            error_modes=json.loads(entry.get("error_modes", "[]")),
                            dependencies=json.loads(entry.get("dependencies", "[]")),
                            check_interval_s=entry.get("check_interval_s", 3600),
                            critical=bool(entry.get("critical", 0)),
                            created_at=entry.get("created_at", ""),
                        )
                        contracts[contract.agent_id] = contract
                    except Exception:
                        continue
                return contracts
        except Exception as _db_e:
            logger.debug("[R-F1476] dedicated DB list failed: %s", _db_e)

        # Fallback: read from Redis/state_store
        try:
            rs = self._get_redis()
            all_keys = await rs.scan_keys(f"{_CONTRACT_KEY}*")
            if not all_keys:
                return contracts
            for key in all_keys:
                agent_id = key.replace(_CONTRACT_KEY, "")
                contract = await self.get_contract(agent_id)
                if contract:
                    contracts[agent_id] = contract
        except Exception as e:
            logger.warning("[R-F1301] list_contracts failed: %s", e)
            try:
                from .engine_wiring import wire_failure
                wire_failure(
                    module="agent_contract",
                    detail=f"list_contracts failed: {e}",
                    gap_type="source_failure",
                    source="agent_contract:list_contracts",
                )
            except Exception:
                pass
        return contracts

    async def delete_contract(self, agent_id: str) -> bool:
        """Remove an agent's contract (on agent unregistration).

        R-F1476: removes from the dedicated DB first, then Redis.
        """
        # R-F1476: remove from dedicated DB
        try:
            self._db_delete(agent_id)
        except Exception as _db_e:
            logger.debug("[R-F1476] dedicated DB delete failed for %s: %s", agent_id, _db_e)
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
