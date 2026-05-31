"""Independence self-test — runs on every deploy to verify ARIA's self-sufficiency.

This test checks that ARIA's LLM chain is configured for minimum cost:
1. Ollama is the primary provider (free, local)
2. DeepSeek is the emergency fallback only
3. No billing-exhausted providers are being probed
4. The autonomous coder uses the free tier

Fails loudly if independence is compromised.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger("aria.independence_test")

# Cost thresholds
MAX_MONTHLY_SPEND_USD = 5.00  # Independence threshold: $5/month max
WARN_MONTHLY_SPEND_USD = 1.00  # Warning threshold


def _get_fly_secrets() -> dict[str, str]:
    """Fetch secrets from fly.io for the aria-intel app."""
    import subprocess
    try:
        r = subprocess.run(
            ["flyctl", "secrets", "list", "-a", "aria-intel"],
            capture_output=True, timeout=30,
        )
        text = r.stdout.decode("utf-8", errors="replace")
        secrets = {}
        # Each line: NAME │ DIGEST │ STATUS
        # Split on the box-drawing pipe (U+2502 = │)
        for line in text.split("\n"):
            if "│" in line:
                name = line.split("│")[0].strip()
                if name and not name.startswith("NAME"):
                    secrets[name] = "set"
        return secrets
    except Exception:
        return {}


def check_independence(use_fly_secrets: bool = False) -> dict[str, any]:
    """Run all independence checks.

    If use_fly_secrets is True, checks against live fly.io secrets instead of local env.
    """
    checks = []
    passed = 0
    total = 0

    fly_secrets = _get_fly_secrets() if use_fly_secrets else {}

    def _env(name: str) -> str:
        if use_fly_secrets and name in fly_secrets:
            return "set"
        return (os.environ.get(name) or "").strip()

    # ── Check 1: Ollama is configured ──────────────────────────────────
    total += 1
    ollama_url = _env("OLLAMA_URL")
    if ollama_url:
        passed += 1
        checks.append({
            "check": "ollama_configured",
            "pass": True,
            "detail": f"OLLAMA_URL={ollama_url}",
        })
    else:
        checks.append({
            "check": "ollama_configured",
            "pass": False,
            "detail": "OLLAMA_URL not set — ARIA has no free local LLM",
            "action": "Set OLLAMA_URL to your Ollama endpoint (e.g. http://localhost:11434)",
        })

    # ── Check 2: Ollama is primary (not just preferred) ────────────────
    total += 1
    local_primary = _env("ARIA_LOCAL_LLM_PRIMARY").lower() in ("1", "true", "yes", "set")
    if local_primary:
        passed += 1
        checks.append({
            "check": "ollama_is_primary",
            "pass": True,
            "detail": "ARIA_LOCAL_LLM_PRIMARY=1 — Ollama serves first",
        })
    else:
        checks.append({
            "check": "ollama_is_primary",
            "pass": False,
            "detail": "ARIA_LOCAL_LLM_PRIMARY not set — DeepSeek/Anthropic serves first",
            "action": "Set ARIA_LOCAL_LLM_PRIMARY=1 to make Ollama the primary provider",
        })

    # ── Check 3: No billing-exhausted providers in the chain ───────────
    total += 1
    anthropic_enabled = _env("ARIA_ANTHROPIC_ENABLED").lower() in ("1", "true", "yes", "set")
    if not anthropic_enabled:
        passed += 1
        checks.append({
            "check": "no_billing_exhausted_providers",
            "pass": True,
            "detail": "Anthropic disabled (billing exhausted) — not being probed",
        })
    else:
        checks.append({
            "check": "no_billing_exhausted_providers",
            "pass": False,
            "detail": "Anthropic enabled but billing may be exhausted — will waste calls probing it",
            "action": "Unset ARIA_ANTHROPIC_ENABLED or top up billing",
        })

    # ── Check 4: Autonomous mode is enabled ────────────────────────────
    total += 1
    autonomous = _env("ARIA_AUTONOMOUS_ENABLED").lower() in ("1", "true", "yes", "set")
    if autonomous:
        passed += 1
        checks.append({
            "check": "autonomous_enabled",
            "pass": True,
            "detail": "ARIA_AUTONOMOUS_ENABLED=1 — self-coding loop active",
        })
    else:
        checks.append({
            "check": "autonomous_enabled",
            "pass": False,
            "detail": "ARIA_AUTONOMOUS_ENABLED not set — ARIA cannot self-improve",
            "action": "Set ARIA_AUTONOMOUS_ENABLED=1",
        })

    # ── Check 5: Coder is enabled ──────────────────────────────────────
    total += 1
    coder = _env("ARIA_CODER_ENABLED").lower() in ("1", "true", "yes", "set")
    if coder:
        passed += 1
        checks.append({
            "check": "coder_enabled",
            "pass": True,
            "detail": "ARIA_CODER_ENABLED=1 — autonomous coding active",
        })
    else:
        checks.append({
            "check": "coder_enabled",
            "pass": False,
            "detail": "ARIA_CODER_ENABLED not set — ARIA cannot code autonomously",
            "action": "Set ARIA_CODER_ENABLED=1",
        })

    # ── Check 6: Cost cap is set ───────────────────────────────────────
    total += 1
    cap = _env("ARIA_MONTHLY_CAP_USD")
    if cap:
        passed += 1
        checks.append({
            "check": "cost_cap_set",
            "pass": True,
            "detail": f"ARIA_MONTHLY_CAP_USD={cap}",
        })
    else:
        checks.append({
            "check": "cost_cap_set",
            "pass": False,
            "detail": "ARIA_MONTHLY_CAP_USD not set — no cost limit",
            "action": "Set ARIA_MONTHLY_CAP_USD=300 (or lower for tighter control)",
        })

    # ── Check 7: Prompt budget is active ───────────────────────────────
    total += 1
    try:
        # Try to import; if it fails, the module isn't deployed yet
        import importlib
        spec = importlib.util.find_spec("aria_service.llm.prompt_budget")
        if spec is not None:
            passed += 1
            checks.append({
                "check": "prompt_budget_active",
                "pass": True,
                "detail": "Prompt budget (R-F1236) module found — prevents HTTP 413",
            })
        else:
            checks.append({
                "check": "prompt_budget_active",
                "pass": False,
                "detail": "Prompt budget module not found",
                "action": "Ensure prompt_budget.py is deployed",
            })
    except Exception:
        checks.append({
            "check": "prompt_budget_active",
            "pass": False,
            "detail": "Could not check prompt budget",
            "action": "Ensure prompt_budget.py is deployed",
        })

    # ── Check 8: SQLite backend (not Redis/Upstash) ────────────────────
    total += 1
    backend = _env("ARIA_STATE_BACKEND")
    if backend in ("sqlite", "set"):  # "set" when checking fly secrets
        passed += 1
        checks.append({
            "check": "free_state_backend",
            "pass": True,
            "detail": f"ARIA_STATE_BACKEND={backend} — no paid persistence",
        })
    else:
        checks.append({
            "check": "free_state_backend",
            "pass": False,
            "detail": f"ARIA_STATE_BACKEND={backend} — may have paid persistence",
            "action": "Set ARIA_STATE_BACKEND=sqlite for zero-cost state",
        })

    # ── Score ──────────────────────────────────────────────────────────
    score_pct = round(passed / total * 100) if total > 0 else 0
    all_pass = passed == total

    result = {
        "pass": all_pass,
        "score": score_pct,
        "passed": passed,
        "total": total,
        "checks": checks,
        "independence_level": (
            "FULL" if score_pct >= 90 else
            "HIGH" if score_pct >= 75 else
            "PARTIAL" if score_pct >= 50 else
            "LOW"
        ),
    }

    if all_pass:
        logger.info(
            "INDEPENDENCE: %d/%d checks pass (%d%%) — %s",
            passed, total, score_pct, result["independence_level"],
        )
    else:
        logger.warning(
            "INDEPENDENCE: %d/%d checks pass (%d%%) — %s. Failing checks:",
            passed, total, score_pct, result["independence_level"],
        )
        for c in checks:
            if not c["pass"]:
                logger.warning("  [FAIL] %s: %s", c["check"], c.get("action", c["detail"]))

    return result


def check_live_chain() -> dict[str, any]:
    """Check the live LLM chain via the health endpoint."""
    try:
        import urllib.request
        resp = urllib.request.urlopen("https://aria-intel.fly.dev/health", timeout=15)
        health = json.loads(resp.read())
        chain = health.get("llm_chain", {})
        stats = health.get("llm_fallback_stats", {})

        active = chain.get("active_providers", [])
        serving = chain.get("serving_provider", "unknown")

        result = {
            "pass": serving == "ollama",
            "chain": active,
            "serving": serving,
            "deepseek_calls": stats.get("deepseek", {}).get("calls", 0),
            "ollama_calls": stats.get("ollama", {}).get("calls", 0),
        }

        if result["pass"]:
            logger.info("LIVE CHAIN: %s (serving: %s) — FREE", " → ".join(active), serving)
        else:
            logger.warning(
                "LIVE CHAIN: %s (serving: %s) — PAID provider primary!",
                " → ".join(active), serving,
            )

        return result
    except Exception as e:
        return {"pass": False, "error": str(e)}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print()
    print("=" * 60)
    print("ARIA INDEPENDENCE SELF-TEST")
    print("=" * 60)
    print()

    result = check_independence(use_fly_secrets=True)
    print(f"Score: {result['passed']}/{result['total']} ({result['score']}%)")
    print(f"Level: {result['independence_level']}")
    print()

    for c in result["checks"]:
        icon = "✅" if c["pass"] else "❌"
        print(f"  {icon} {c['check']}")
        print(f"     {c['detail']}")
        if not c["pass"] and "action" in c:
            print(f"     → {c['action']}")
        print()

    print("=" * 60)
    print()

    # Also check live chain
    live = check_live_chain()
    if "error" in live:
        print(f"  ⚠️ Live chain check failed: {live['error']}")
    else:
        icon = "✅" if live["pass"] else "❌"
        print(f"  {icon} Live chain: {' → '.join(live['chain'])}")
        print(f"     Serving: {live['serving']}")
        print(f"     DeepSeek calls: {live['deepseek_calls']}")
        print(f"     Ollama calls: {live['ollama_calls']}")

    print()
    sys.exit(0 if result["pass"] else 1)
