# =============================================================================
# ARIA v3.1 — Cost Circuit Breaker
# aria_service/autonomous/cost_monitor.py
#
# NOTE (R-F1207): The ARIACostMonitor class in this module is SUPERSEDED
# by the MeteredProvider wrapper (aria_service/llm/metered.py) which is
# applied to the LLM provider at boot in main.py. Cost tracking, per-task
# attribution, and circuit-breaking are handled by MeteredProvider +
# cost_tracker.py. This module is kept for reference but NOT started
# in production. Do not wire into main.py.
#
# PROBLEM THIS SOLVES (external review finding):
#   "$1/day cap" is development-grade. In production with 34 scheduled
#   tasks + deep research + full channel ingestion, costs can spike
#   to $20-30/day on a heavy intelligence sweep day.
#
#   Current state: one global cost cap. No per-task attribution.
#   No circuit breaker before the cap is hit.
#   No visibility into which tasks are most expensive.
#   No user-facing cost report.
#
# WHAT THIS BUILDS:
#   1. Per-request cost attribution (task + model + token count)
#   2. Per-task daily budget allocation
#   3. Circuit breaker at 80% of daily cap (warning) and 100% (hard stop)
#   4. Cost-per-task leaderboard for weekly report
#   5. Automatic task suspension when budget exhausted
#   6. Admin endpoint to adjust caps without restart
#
# PRODUCTION CAPS (adjust via env vars):
#   ARIA_DAILY_CAP_USD=10          — total daily cap
#   ARIA_TASK_CAP_USD=2            — per-task daily cap
#   ARIA_WARNING_THRESHOLD=0.80    — alert at 80% of cap
#   ARIA_CIRCUIT_THRESHOLD=1.00    — hard stop at 100%
#
# ANTHROPIC PRICING (claude-sonnet-4-6 as of 2026):
#   Input:  $3.00 per million tokens
#   Output: $15.00 per million tokens
#   (Update PRICING dict when Anthropic updates pricing)
# =============================================================================

import json
import time
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, date, timedelta
from typing import Optional
from functools import wraps
from ..intel.wire import fail_wire  # R-F1789 §21 brain-wiring

logger = logging.getLogger("ARIA.CostMonitor")

# R-F1320: wire module health to the brain
try:
    from aria_service.intel.engine_wiring import wire_success as _ws1320
    _ws1320(
        module="autonomous.cost_monitor",
        summary="Cost Monitor active",
        source_id="autonomous:cost_monitor:R-F1320",
    )
except Exception:
    pass


# =============================================================================
# PRICING TABLE
# Update this when Anthropic changes pricing
# =============================================================================

PRICING = {
    "claude-opus-4-6": {
        "input_per_mtok":  15.00,
        "output_per_mtok": 75.00,
    },
    "claude-sonnet-4-6": {
        "input_per_mtok":   3.00,
        "output_per_mtok": 15.00,
    },
    "claude-haiku-4-5-20251001": {
        "input_per_mtok":  0.80,
        "output_per_mtok":  4.00,
    },
    "deepseek-chat": {           # Fallback model
        "input_per_mtok":  0.14,
        "output_per_mtok": 0.28,
    },
    "_default": {
        "input_per_mtok":  3.00,   # Assume Sonnet if unknown
        "output_per_mtok": 15.00,
    },
}


@fail_wire(module="cost_monitor", gap_type="agent_cycle_failure")
def compute_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Compute USD cost for a single API call."""
    pricing = PRICING.get(model, PRICING["_default"])
    input_cost  = (input_tokens  / 1_000_000) * pricing["input_per_mtok"]
    output_cost = (output_tokens / 1_000_000) * pricing["output_per_mtok"]
    return round(input_cost + output_cost, 6)


# =============================================================================
# REQUEST RECORD
# =============================================================================

@dataclass
class CostRecord:
    """A single API call with full cost attribution."""
    request_id: str
    task_id: str                  # Which scheduled task triggered this
    task_name: str                # Human-readable task name
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    latency_ms: float = 0.0
    success: bool = True
    error: str = ""
    purpose: str = ""             # Brief description of what this call was for

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @fail_wire(module="cost_monitor", gap_type="agent_cycle_failure")
    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "task_id": self.task_id,
            "task_name": self.task_name,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": self.cost_usd,
            "timestamp": self.timestamp,
            "latency_ms": self.latency_ms,
            "success": self.success,
            "purpose": self.purpose,
        }


# =============================================================================
# CIRCUIT BREAKER STATE
# =============================================================================

class CircuitState:
    CLOSED  = "CLOSED"   # Normal operation
    WARNING = "WARNING"  # Approaching cap — alert sent
    OPEN    = "OPEN"     # Cap hit — requests blocked


@dataclass
class DailyBudgetState:
    """Budget state for a single day."""
    date: str
    total_cap_usd: float
    task_cap_usd: float
    total_spent_usd: float = 0.0
    task_spent: dict = field(default_factory=dict)  # task_id → spent
    circuit_state: str = CircuitState.CLOSED
    warning_sent: bool = False
    anomaly_sent: dict = field(default_factory=dict)  # task_id → bool
    suspended_tasks: list = field(default_factory=list)

    @property
    def remaining_usd(self) -> float:
        return max(0.0, self.total_cap_usd - self.total_spent_usd)

    @property
    def utilisation(self) -> float:
        return self.total_spent_usd / self.total_cap_usd if self.total_cap_usd > 0 else 0.0

    @fail_wire(module="cost_monitor", gap_type="agent_cycle_failure")
    def task_remaining(self, task_id: str) -> float:
        spent = self.task_spent.get(task_id, 0.0)
        return max(0.0, self.task_cap_usd - spent)

    @fail_wire(module="cost_monitor", gap_type="agent_cycle_failure")
    def task_utilisation(self, task_id: str) -> float:
        spent = self.task_spent.get(task_id, 0.0)
        return spent / self.task_cap_usd if self.task_cap_usd > 0 else 0.0


# =============================================================================
# COST MONITOR
# =============================================================================

class ARIACostMonitor:
    """
    Production-grade cost monitoring and circuit breaker for ARIA.

    Every API call goes through this monitor:
    1. Pre-call check: is this task still within budget?
    2. Post-call record: log cost with full attribution
    3. Aggregate update: update daily totals
    4. Circuit breaker: suspend task or halt system if caps hit

    Usage:
        monitor = ARIACostMonitor(redis_client=redis, notify_fn=notify)

        # Before making an API call:
        allowed, reason = monitor.pre_call_check(task_id="TASK-01")
        if not allowed:
            logger.warning(f"Call blocked: {reason}")
            return

        # After API call:
        record = monitor.record_call(
            task_id="TASK-01",
            task_name="Daily Briefing",
            model="claude-sonnet-4-6",
            input_tokens=1200,
            output_tokens=800,
            purpose="Generate 05:45 team briefing",
        )
        print(f"Cost: ${record.cost_usd:.4f}")
    """

    REDIS_KEY_PREFIX = "aria:cost"

    def __init__(
        self,
        redis_client=None,
        notify_fn=None,
        daily_cap_usd: float = 10.0,
        task_cap_usd: float = 2.0,
        warning_threshold: float = 0.80,
    ):
        self.redis = redis_client
        self.notify = notify_fn
        self.daily_cap = daily_cap_usd
        self.task_cap = task_cap_usd
        self.warning_threshold = warning_threshold
        self._today_state: Optional[DailyBudgetState] = None
        self._today_key: str = ""

    @fail_wire(module="cost_monitor", gap_type="agent_cycle_failure")
    def pre_call_check(self, task_id: str) -> tuple[bool, str]:
        """
        Check if a task is allowed to make an API call.
        Call this BEFORE every API request.

        Returns:
            (allowed: bool, reason: str)
        """
        state = self._get_today_state()

        # Global circuit open — all calls blocked
        if state.circuit_state == CircuitState.OPEN:
            return False, (
                f"Global circuit OPEN — daily cap ${self.daily_cap:.2f} exceeded. "
                f"Spent: ${state.total_spent_usd:.4f}"
            )

        # Task suspended
        if task_id in state.suspended_tasks:
            return False, (
                f"Task {task_id} suspended — per-task cap ${self.task_cap:.2f} exceeded."
            )

        # Task approaching its cap
        if state.task_utilisation(task_id) >= 0.90:
            logger.warning(
                f"Task {task_id} at {state.task_utilisation(task_id):.0%} "
                f"of per-task cap"
            )

        return True, "OK"

    @fail_wire(module="cost_monitor", gap_type="agent_cycle_failure")
    def record_call(
        self,
        task_id: str,
        task_name: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        purpose: str = "",
        latency_ms: float = 0.0,
        success: bool = True,
        error: str = "",
    ) -> CostRecord:
        """
        Record an API call with full cost attribution.
        Call this AFTER every API request completes.
        """
        import hashlib
        request_id = hashlib.md5(
            f"{task_id}{time.time()}".encode()
        , usedforsecurity=False).hexdigest()[:12]

        cost = compute_cost(model, input_tokens, output_tokens)

        record = CostRecord(
            request_id=request_id,
            task_id=task_id,
            task_name=task_name,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            latency_ms=latency_ms,
            success=success,
            error=error,
            purpose=purpose,
        )

        # Update state
        state = self._get_today_state()
        state.total_spent_usd = round(state.total_spent_usd + cost, 6)
        state.task_spent[task_id] = round(
            state.task_spent.get(task_id, 0.0) + cost, 6
        )

        # Check circuit breaker thresholds
        self._evaluate_circuit(state, task_id)

        # Persist
        self._save_today_state(state)
        self._store_record(record)

        logger.debug(
            f"Cost [{task_id}]: ${cost:.4f} | "
            f"Daily: ${state.total_spent_usd:.4f}/${self.daily_cap:.2f} "
            f"({state.utilisation:.0%})"
        )
        return record

    @fail_wire(module="cost_monitor", gap_type="agent_cycle_failure")
    def get_daily_summary(self, target_date: str = "") -> dict:
        """Get cost summary for today or a specific date."""
        state = self._get_today_state()
        return {
            "date": state.date,
            "total_spent_usd": round(state.total_spent_usd, 4),
            "daily_cap_usd": state.total_cap_usd,
            "remaining_usd": round(state.remaining_usd, 4),
            "utilisation_pct": round(state.utilisation * 100, 1),
            "circuit_state": state.circuit_state,
            "task_breakdown": {
                task_id: {
                    "spent": round(spent, 4),
                    "cap": self.task_cap,
                    "utilisation_pct": round(
                        (spent / self.task_cap * 100) if self.task_cap > 0 else 0, 1
                    ),
                }
                for task_id, spent in state.task_spent.items()
            },
            "suspended_tasks": state.suspended_tasks,
        }

    @fail_wire(module="cost_monitor", gap_type="agent_cycle_failure")
    def get_cost_leaderboard(self, days: int = 7) -> list[dict]:
        """
        Return per-task cost leaderboard for the past N days.
        Used in weekly team report.
        """
        if not self.redis:
            return []

        task_totals: dict[str, float] = {}
        task_names: dict[str, str] = {}

        for i in range(days):
            d = (date.today() - timedelta(days=i)).isoformat()
            key = f"{self.REDIS_KEY_PREFIX}:records:{d}"
            try:
                data = self.redis.get(key)
                if data:
                    records = json.loads(data)
                    for r in records:
                        tid = r.get("task_id", "unknown")
                        task_totals[tid] = round(
                            task_totals.get(tid, 0) + r.get("cost_usd", 0), 4
                        )
                        if r.get("task_name"):
                            task_names[tid] = r["task_name"]
            except Exception:
                continue

        return sorted([
            {
                "task_id": tid,
                "task_name": task_names.get(tid, tid),
                "total_cost_usd": total,
                "daily_average_usd": round(total / days, 4),
            }
            for tid, total in task_totals.items()
        ], key=lambda x: x["total_cost_usd"], reverse=True)

    @fail_wire(module="cost_monitor", gap_type="agent_cycle_failure")
    def reset_task(self, task_id: str) -> None:
        """Re-enable a suspended task (admin action)."""
        state = self._get_today_state()
        if task_id in state.suspended_tasks:
            state.suspended_tasks.remove(task_id)
            state.task_spent[task_id] = 0.0  # Reset task budget for today
            self._save_today_state(state)
            logger.info(f"Task {task_id} re-enabled by admin")

    @fail_wire(module="cost_monitor", gap_type="agent_cycle_failure")
    def set_daily_cap(self, cap_usd: float) -> None:
        """Adjust daily cap at runtime."""
        self.daily_cap = cap_usd
        state = self._get_today_state()
        state.total_cap_usd = cap_usd
        self._save_today_state(state)
        logger.info(f"Daily cap updated to ${cap_usd:.2f}")

    # ── INTERNAL ─────────────────────────────────────────────────────────────

    def _evaluate_circuit(self, state: DailyBudgetState, task_id: str) -> None:
        """Check thresholds and trigger circuit breaker if needed."""

        # Per-task cap exceeded — suspend this task
        if state.task_utilisation(task_id) >= 1.0:
            if task_id not in state.suspended_tasks:
                state.suspended_tasks.append(task_id)
                logger.warning(f"Task {task_id} suspended — per-task cap reached")
                if self.notify:
                    self.notify(
                        f"⚠️ ARIA Cost Alert\n"
                        f"Task {task_id} suspended — per-task budget ${self.task_cap:.2f}/day exhausted.\n"
                        f"Task will resume tomorrow at 00:00 UTC.\n"
                        f"Admin can reset with: reset_task('{task_id}')"
                    )

        # Global warning threshold
        if (state.utilisation >= self.warning_threshold
                and not state.warning_sent):
            state.warning_sent = True
            state.circuit_state = CircuitState.WARNING
            if self.notify:
                self.notify(
                    f"ARIA Cost Warning\n"
                    f"Daily spend at {state.utilisation:.0%} of cap "
                    f"(${state.total_spent_usd:.2f}/${self.daily_cap:.2f}).\n"
                    f"Top spenders: "
                    + ", ".join(
                        f"{tid}: ${spent:.2f}"
                        for tid, spent in sorted(
                            state.task_spent.items(),
                            key=lambda x: x[1], reverse=True
                        )[:3]
                    )
                )
            logger.warning(
                f"Daily cost at {state.utilisation:.0%} — WARNING threshold reached"
            )

        # R-F1080 — cost anomaly detection: if any single task exceeds
        # 50% of the daily cap, emit a brain signal.
        for tid, spent in state.task_spent.items():
            if spent > self.daily_cap * 0.5 and not state.anomaly_sent.get(tid):
                state.anomaly_sent[tid] = True
                logger.warning(
                    "Cost anomaly: task %s spent $%.2f (%.0f%% of daily cap)",
                    tid, spent, spent / self.daily_cap * 100,
                )
                try:
                    from ..intel.engine_wiring import wire_failure as _wf
                    _wf(
                        module="cost_monitor",
                        detail=f"Cost anomaly: task {tid} spent ${spent:.2f} ({spent/self.daily_cap*100:.0f}% of daily cap)",
                        gap_type="cost_anomaly",
                        source="cost_monitor",
                    )
                except Exception:
                    pass

        # Global cap exceeded — open circuit
        if state.utilisation >= 1.0:
            state.circuit_state = CircuitState.OPEN
            if self.notify:
                self.notify(
                    f"🚨 ARIA Cost Circuit OPEN\n"
                    f"Daily cap ${self.daily_cap:.2f} exhausted "
                    f"(${state.total_spent_usd:.2f} spent).\n"
                    f"All autonomous API calls halted until 00:00 UTC.\n"
                    f"Manual queries via WhatsApp/chat still permitted."
                )
            logger.error("Daily cost cap exhausted — circuit OPEN")
            # R-F1059 — wire circuit open to brain
            try:
                from ..intel.engine_wiring import wire_failure as _wf
                _wf(
                    module="cost_monitor",
                    detail=f"Daily cost cap ${self.daily_cap:.2f} exhausted (${state.total_spent_usd:.2f} spent)",
                    gap_type="cost_cap_hit",
                    source="cost_monitor",
                )
            except Exception:
                pass

    def _get_today_state(self) -> DailyBudgetState:
        today_str = date.today().isoformat()
        if self._today_key != today_str or self._today_state is None:
            self._today_key = today_str
            self._today_state = self._load_today_state(today_str)
        return self._today_state

    def _load_today_state(self, date_str: str) -> DailyBudgetState:
        if self.redis:
            try:
                key = f"{self.REDIS_KEY_PREFIX}:state:{date_str}"
                data = self.redis.get(key)
                if data:
                    d = json.loads(data)
                    return DailyBudgetState(
                        date=d["date"],
                        total_cap_usd=d.get("total_cap_usd", self.daily_cap),
                        task_cap_usd=d.get("task_cap_usd", self.task_cap),
                        total_spent_usd=d.get("total_spent_usd", 0.0),
                        task_spent=d.get("task_spent", {}),
                        circuit_state=d.get("circuit_state", CircuitState.CLOSED),
                        warning_sent=d.get("warning_sent", False),
                        suspended_tasks=d.get("suspended_tasks", []),
                    )
            except Exception as e:
                logger.error(f"Failed to load cost state: {e}")
        return DailyBudgetState(
            date=date_str,
            total_cap_usd=self.daily_cap,
            task_cap_usd=self.task_cap,
        )

    def _save_today_state(self, state: DailyBudgetState) -> None:
        if not self.redis:
            return
        try:
            key = f"{self.REDIS_KEY_PREFIX}:state:{state.date}"
            self.redis.set(key, json.dumps({
                "date": state.date,
                "total_cap_usd": state.total_cap_usd,
                "task_cap_usd": state.task_cap_usd,
                "total_spent_usd": state.total_spent_usd,
                "task_spent": state.task_spent,
                "circuit_state": state.circuit_state,
                "warning_sent": state.warning_sent,
                "suspended_tasks": state.suspended_tasks,
            }), ex=7 * 86400)
        except Exception as e:
            logger.error(f"Failed to save cost state: {e}")

    def _store_record(self, record: CostRecord) -> None:
        if not self.redis:
            return
        try:
            key = f"{self.REDIS_KEY_PREFIX}:records:{date.today().isoformat()}"
            existing = self.redis.get(key)
            records = json.loads(existing) if existing else []
            records.append(record.to_dict())
            self.redis.set(key, json.dumps(records), ex=30 * 86400)
        except Exception as e:
            logger.error(f"Failed to store cost record: {e}")


# =============================================================================
# DECORATOR FOR ARIA ENGINE
# =============================================================================

@fail_wire(module="cost_monitor", gap_type="agent_cycle_failure", control_flow_exempt=("RuntimeError",))
def track_cost(task_id: str, task_name: str, purpose: str = ""):
    """
    Decorator to automatically track costs for any function that
    makes an Anthropic API call.

    Usage:
        @track_cost(task_id="BRIEFING-01", task_name="Daily Briefing")
        async def generate_briefing(self, ...):
            response = self.client.messages.create(...)
            return response
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Get monitor from self if it's a method
            monitor = None
            if args and hasattr(args[0], 'cost_monitor'):
                monitor = args[0].cost_monitor

            # Pre-call check
            if monitor:
                allowed, reason = monitor.pre_call_check(task_id)
                if not allowed:
                    raise RuntimeError(f"Cost circuit breaker: {reason}")

            start = time.time()
            try:
                result = await func(*args, **kwargs)

                # Extract token usage if available
                if monitor and hasattr(result, 'usage'):
                    monitor.record_call(
                        task_id=task_id,
                        task_name=task_name,
                        model=getattr(result, 'model', 'unknown'),
                        input_tokens=result.usage.input_tokens,
                        output_tokens=result.usage.output_tokens,
                        purpose=purpose,
                        latency_ms=(time.time() - start) * 1000,
                        success=True,
                    )
                return result

            except Exception as e:
                if monitor:
                    monitor.record_call(
                        task_id=task_id,
                        task_name=task_name,
                        model="unknown",
                        input_tokens=0,
                        output_tokens=0,
                        purpose=purpose,
                        latency_ms=(time.time() - start) * 1000,
                        success=False,
                        error=str(e)[:200],
                    )
                raise

        return wrapper
    return decorator


# =============================================================================
# STANDALONE DEMO
# =============================================================================

if __name__ == "__main__":
    print("ARIA Cost Monitor — Demo")
    print("=" * 50)

    monitor = ARIACostMonitor(
        daily_cap_usd=10.0,
        task_cap_usd=2.0,
        warning_threshold=0.80,
    )

    # Simulate a day of API calls
    tasks = [
        ("BRIEFING-01",  "Daily Briefing",    "claude-sonnet-4-6", 1200, 800),
        ("ANGOLA-SWEEP", "Angola Intel Sweep", "claude-sonnet-4-6", 2500, 1200),
        ("DD-BTG",       "BTG DD Screen",      "claude-opus-4-6",   3000, 2000),
        ("TENDER-MON",   "Tender Monitor",     "claude-sonnet-4-6",  800,  400),
        ("SANCTIONS",    "Sanctions Screen",   "claude-sonnet-4-6",  600,  200),
    ]

    for task_id, task_name, model, inp, out in tasks:
        allowed, reason = monitor.pre_call_check(task_id)
        if not allowed:
            print(f"  BLOCKED: {task_id} — {reason}")
            continue
        record = monitor.record_call(
            task_id=task_id, task_name=task_name, model=model,
            input_tokens=inp, output_tokens=out,
            purpose=f"Test call for {task_name}",
        )
        print(f"  {task_id:<15} ${record.cost_usd:.4f}")

    summary = monitor.get_daily_summary()
    print(f"\nDaily total: ${summary['total_spent_usd']:.4f}")
    print(f"Remaining:   ${summary['remaining_usd']:.4f}")
    print(f"Utilisation: {summary['utilisation_pct']:.1f}%")
    print(f"Circuit:     {summary['circuit_state']}")
    print(f"\nPricing reference:")
    for model, pricing in PRICING.items():
        if not model.startswith('_'):
            print(f"  {model}: "
                  f"${pricing['input_per_mtok']:.2f}/MTok in, "
                  f"${pricing['output_per_mtok']:.2f}/MTok out")
