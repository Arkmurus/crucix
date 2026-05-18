"""Self-diagnostic — ARIA checks her own wiring and surfaces broken pieces.

Why this exists
───────────────
Session feedback 2026-04-18: "we cannot keep finding missing bits all
the time — ARIA should be able to self-diagnose, and the dashboard
should fire malfunction alerts."

Every past incident in this repo where something shipped but didn't
actually run had the same shape: a module was created, wasn't wired
into the intent router / the autonomous engine / the Node proxy /
brain_hook, and nobody knew until a user reported the output was
missing. The fix is not more code review — it's a module that walks
its own sibling modules and checks each one is actually reachable.

What this module checks
───────────────────────
For every Fire-on-ARIA + anti-fabrication + DD module, it verifies:

  1. IMPORT      — does `import aria_service.intel.X` succeed?
  2. ENTRYPOINT  — does the documented public entry function exist?
  3. REGISTERED  — if the module is brain-feeding, is it in
                   brain_hook._MODULE_TOPICS?
  4. ROUTED      — if the module has an endpoint, is the Node proxy
                   present in server.mjs (verified via HTTP probe
                   returning 401 rather than 404)?
  5. SCHEDULED   — if the module has an autonomous task, is the task
                   in tasks.yaml with `enabled: true`?
  6. CREDENTIALED — if the module needs an API key env var, is it
                    actually set?
  7. RESPONSIVE  — does the module's smoke-test function return a
                   sensible result?

Each check returns PASS / WARN / FAIL plus an actionable note.

Output consumed by
──────────────────
  - GET /api/aria/diagnostic              (summary, public)
  - GET /api/aria/diagnostic/details      (full report, auth)
  - Dashboard "System Health" tile
  - SELF-DIAGNOSTIC-15MIN autonomous task — fires pending_actions on RED

Public API
──────────
  async run_diagnostic() -> dict            # full report
  async run_diagnostic_summary() -> dict    # overall score only
  async alert_on_red(report: dict) -> None  # push CRITICAL pending_action
"""
from __future__ import annotations

import asyncio
import importlib
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("aria.self_diagnostic")


# ── Module catalogue — what we check ───────────────────────────────────────
# Each entry: (module_path, entry_fn, flags)
# flags: brain_registered, autonomous_task_id, endpoint_path, env_var,
#        smoke_fn (optional — async callable returning truthy on success)

_MODULES: list[dict] = [
    # Fire-on-ARIA core
    {
        "name": "scratchpad",
        "module": "aria_service.intel.scratchpad",
        "entry": "build_prefix",
        "brain_registered": True,
        "endpoint": "/api/aria/scratchpad/fake_trace_id",
        "endpoint_unauth_ok_codes": (401, 404),  # 404 is OK here (trace missing)
        "critical": False,
    },
    {
        "name": "comprehension",
        "module": "aria_service.intel.comprehension",
        "entry": "analyse",
        "brain_registered": True,
        "critical": True,
    },
    {
        "name": "consistency_suite",
        "module": "aria_service.intel.consistency_suite",
        "entry": "load_tests",
        "brain_registered": True,
        "autonomous_task_id": "CONSISTENCY-WEEKLY",
        "endpoint": "/api/aria/consistency/scores",
        "critical": False,
    },
    {
        "name": "capability_card",
        "module": "aria_service.intel.capability_card",
        "entry": "build_card",
        "brain_registered": True,
        "autonomous_task_id": "CAPABILITY-CARD-NIGHTLY",
        "endpoint": "/api/aria/capability-card",
        "critical": False,
    },
    {
        "name": "calibration_auto_tune",
        "module": "aria_service.intel.calibration_auto_tune",
        "entry": "run_auto_tune",
        "brain_registered": True,
        "autonomous_task_id": "CALIBRATION-AUTO-TUNE-WEEKLY",
        "endpoint": "/api/aria/calibration/auto-tune",
        "critical": False,
    },
    # Anti-fabrication stack
    {
        "name": "ground_truth_guard",
        "module": "aria_service.intel.ground_truth_guard",
        "entry": "verify",
        "brain_registered": True,
        "critical": True,
    },
    {
        "name": "tool_claim_guard",
        "module": "aria_service.intel.tool_claim_guard",
        "entry": "guard",
        "brain_registered": True,
        "critical": True,
    },
    {
        "name": "commitment_guard",
        "module": "aria_service.intel.commitment_guard",
        "entry": "guard_commitments",
        "brain_registered": False,  # not in brain_hook; OK
        "critical": True,
    },
    # Search + research engine
    {
        "name": "query_decomposer",
        "module": "aria_service.intel.query_decomposer",
        "entry": "classify",
        "brain_registered": True,
        "endpoint": "/api/aria/query/decompose",
        "endpoint_unauth_ok_codes": (401, 405),  # POST-only, GET may 405
        "critical": True,
    },
    {
        "name": "known_publisher_router",
        "module": "aria_service.intel.known_publisher_router",
        "entry": "detect_publisher",
        "brain_registered": True,
        "endpoint": "/api/aria/publisher/fetch",
        "endpoint_unauth_ok_codes": (401, 405),
        "critical": True,
    },
    {
        "name": "source_uptime_monitor",
        "module": "aria_service.intel.source_uptime_monitor",
        "entry": "run_daily_ping",
        "brain_registered": True,
        "autonomous_task_id": "SOURCE-UPTIME-DAILY",
        "endpoint": "/api/aria/sources/uptime",
        "critical": False,
    },
    {
        "name": "defence_source_seed",
        "module": "aria_service.intel.defence_source_seed",
        "entry": "seed_web_atlas",
        "brain_registered": True,
        "endpoint": "/api/aria/sources/seed/catalogue",
        "critical": False,
    },
    # RLAIF + critique — gated behind env vars
    {
        "name": "rlaif",
        "module": "aria_service.intel.rlaif",
        "entry": "evaluate",
        "brain_registered": True,
        "endpoint": "/api/aria/rlaif/stats",
        "env_var": "ARIA_RLAIF_ENABLED",
        "critical": False,
    },
    {
        "name": "critique_collector",
        "module": "aria_service.intel.critique_collector",
        "entry": "collect",
        "brain_registered": True,
        "endpoint": "/api/aria/critique/stats",
        "env_var": "ARIA_CRITIQUE_COLLECTOR_ENABLED",
        "critical": False,
    },
    # DD primary sources
    {
        "name": "sec_edgar",
        "module": "aria_service.intel.sources.sec_edgar",
        "entry": "lookup",
        "brain_registered": True,
        "critical": False,
        "smoke_is_available": True,
    },
    {
        "name": "ofac_sdn",
        "module": "aria_service.intel.sources.ofac_sdn",
        "entry": "lookup",
        "brain_registered": True,
        "critical": True,
        "smoke_is_available": True,
    },
    {
        "name": "fcdo_sanctions",
        "module": "aria_service.intel.sources.fcdo_sanctions",
        "entry": "lookup",
        "brain_registered": True,
        "critical": True,
        "smoke_is_available": True,
    },
    {
        "name": "un_sc_sanctions",
        "module": "aria_service.intel.sources.un_sc_sanctions",
        "entry": "lookup",
        "brain_registered": True,
        "critical": True,
        "smoke_is_available": True,
    },
    {
        "name": "worldbank_debarred",
        "module": "aria_service.intel.sources.worldbank_debarred",
        "entry": "lookup",
        "brain_registered": True,
        "env_var": "WORLDBANK_SUBSCRIPTION_KEY",
        "critical": False,
        "smoke_is_available": True,
    },
    {
        "name": "worldbank_documents",
        "module": "aria_service.intel.sources.worldbank_documents",
        "entry": "lookup",
        "brain_registered": True,
        "critical": False,
        "smoke_is_available": True,
    },
    {
        "name": "acled",
        "module": "aria_service.intel.sources.acled",
        "entry": "lookup",
        "brain_registered": True,
        "env_var": "ACLED_EMAIL",
        "critical": False,
        "smoke_is_available": True,
    },
    # Foundation layer
    {
        "name": "vendor_registry",
        "module": "aria_service.intel.vendor_registry",
        "entry": "load_vendors",
        "brain_registered": False,
        "endpoint": "/api/aria/vendors",
        "critical": False,
    },
    {
        "name": "pending_actions",
        "module": "aria_service.intel.pending_actions",
        "entry": "record",
        "brain_registered": True,
        "endpoint": "/api/aria/pending-actions",
        "critical": True,
    },
    {
        "name": "brain_hook",
        "module": "aria_service.intel.brain_hook",
        "entry": "absorb",
        "brain_registered": False,  # brain_hook IS the registry
        "critical": True,
    },
    # Autonomous engine + tasks
    {
        "name": "autonomous_engine",
        "module": "aria_service.autonomous.engine",
        "entry": "get_engine_status",
        "brain_registered": False,
        "critical": True,
    },
    # Guards' feeding modules (ones that matter for data ingest)
    {
        "name": "researcher.extract_url_deep",
        "module": "aria_service.intel.researcher",
        "entry": "extract_url_deep",
        "brain_registered": False,
        "critical": True,
    },
    {
        "name": "deep_researcher.crawl_website",
        "module": "aria_service.intel.deep_researcher",
        "entry": "crawl_website",
        "brain_registered": False,
        "critical": True,
    },
    # Professional crawl enhancements (2026-04-18 evening)
    {
        "name": "crawl_enhancements",
        "module": "aria_service.intel.crawl_enhancements",
        "entry": "fetch_with_fallbacks",
        "brain_registered": True,
        "critical": False,
    },
    # Playwright scraper stack (2026-04-18 evening, stealth-free)
    {
        "name": "scraper_playwright_engine",
        "module": "aria_service.intel.scraper.playwright_engine",
        "entry": "fetch",
        "brain_registered": True,
        "critical": False,
        # is_available launches a browser — expensive, but this is a
        # 15-min cron cadence so acceptable.
        "smoke_is_available": True,
    },
    {
        "name": "scraper_orchestrator",
        "module": "aria_service.intel.scraper.orchestrator",
        "entry": "fetch",
        "brain_registered": True,
        "critical": False,
    },
    {
        "name": "scraper_procurement",
        "module": "aria_service.intel.scraper.procurement_adapters",
        "entry": "fetch_portal",
        "brain_registered": True,
        "critical": False,
    },
    {
        "name": "scraper_generic",
        "module": "aria_service.intel.scraper.generic_adapter",
        "entry": "fetch",
        "brain_registered": True,
        "critical": False,
    },
    # Airtable integration — added 2026-04-21. Probes module import,
    # health_check entry point, AIRTABLE_PAT env, and the live health
    # endpoint (which in turn reads one row from both Task Register +
    # Pipeline). Before this, Airtable drops were logger.debug only,
    # so ops had no visibility into why rows were missing.
    {
        "name": "airtable_sync",
        "module": "aria_service.integrations.airtable_sync",
        "entry": "health_check",
        "env_var": "AIRTABLE_PAT",
        "endpoint": "/api/aria/airtable/health",
        "endpoint_unauth_ok_codes": (401,),
        "critical": False,
    },
    # ── R-F136 (2026-05-10) — register today's load-bearing modules ──
    # so ARIA self-diagnoses the substrate her DD + search + counter-intel
    # work depends on. Each entry exposes a smoke-callable so the
    # SELF-DIAGNOSTIC-15MIN task surfaces breakage before operators do.
    {
        "name": "web_search",
        "module": "aria_service.intel.web_search",
        "entry": "search",
        "brain_registered": True,
        "critical": True,
    },
    {
        "name": "intel_ledger",
        "module": "aria_service.intel.intel_ledger",
        "entry": "get_recent",  # R-F134 helper
        "brain_registered": True,
        "critical": True,
    },
    {
        "name": "coverage_heatmap",
        "module": "aria_service.intel.coverage_heatmap",
        "entry": "build_heatmap",
        "endpoint": "/api/aria/learning/coverage",
        "endpoint_unauth_ok_codes": (200, 401),
        "critical": False,
    },
    {
        "name": "learning_progress",
        "module": "aria_service.intel.learning_progress",
        "entry": "record_refresh",
        "critical": False,
    },
    {
        "name": "counter_intelligence",
        "module": "aria_service.intel.counter_intelligence",
        "entry": "scan_entity",
        "endpoint": "/api/aria/security/counter-intel/scan",
        "endpoint_unauth_ok_codes": (200, 401, 422),
        "critical": False,
    },
    {
        "name": "sanctions_divergence",
        "module": "aria_service.intel.sanctions_divergence",
        "entry": "analyze_divergence",
        "endpoint": "/api/aria/sanctions/divergence",
        "endpoint_unauth_ok_codes": (200, 401, 422),
        "critical": False,
    },
    {
        "name": "forensic_benford",
        "module": "aria_service.intel.forensic_benford",
        "entry": "benford_test",
        "endpoint": "/api/aria/forensic/benford",
        "endpoint_unauth_ok_codes": (200, 401, 405, 422),
        "critical": False,
    },
    {
        "name": "tbml_detection",
        "module": "aria_service.intel.tbml_detection",
        "entry": "analyze_transaction",
        "endpoint": "/api/aria/tbml/classify",
        "endpoint_unauth_ok_codes": (200, 401, 405, 422),
        "critical": False,
    },
    {
        "name": "dd_orchestrator",
        "module": "aria_service.intel.dd_orchestrator",
        "entry": "orchestrate_dd",
        "endpoint": "/api/aria/dd/reports",
        "endpoint_unauth_ok_codes": (200, 401),
        "brain_registered": True,
        "critical": True,
    },
]


# ── Individual checks ──────────────────────────────────────────────────────

def _check_import(mod_path: str) -> tuple[str, str, Any]:
    """Returns (status, note, module_ref_or_none)."""
    try:
        mod = importlib.import_module(mod_path)
        return ("PASS", "imported cleanly", mod)
    except Exception as e:
        return ("FAIL", f"ImportError: {type(e).__name__}: {str(e)[:200]}", None)


def _check_entry(mod: Any, entry: str) -> tuple[str, str]:
    if mod is None:
        return ("FAIL", "no module to check")
    if not hasattr(mod, entry):
        return ("FAIL", f"entry function `{entry}` not found on module")
    return ("PASS", f"entry `{entry}` callable")


def _check_brain_registered(name: str, module_path: str = "") -> tuple[str, str]:
    """Verify the module appears in brain_hook._MODULE_TOPICS.

    Tolerates common name variants:
      - the name passed directly
      - last segment after dot ("researcher.extract_url_deep" → "extract_url_deep")
      - dots → underscores
      - `sources_X` prefix for modules under aria_service/intel/sources/
      - first segment (module namespace)
    """
    try:
        from . import brain_hook
        topics = getattr(brain_hook, "_MODULE_TOPICS", {}) or {}
        variants = {
            name,
            name.split(".")[-1],
            name.replace(".", "_"),
            name.split(".")[0],
        }
        # If the module lives under aria_service.intel.sources.*, try
        # the `sources_X` convention used in brain_hook.
        if module_path and ".sources." in module_path:
            short = module_path.rsplit(".", 1)[-1]
            variants.add(f"sources_{short}")
        for v in variants:
            if v in topics:
                return ("PASS", f"registered as `{v}` → {topics[v]}")
        return (
            "WARN",
            f"not in brain_hook._MODULE_TOPICS — signals from this module "
            f"will file under 'general' only (tried {sorted(variants)})",
        )
    except Exception as e:
        return ("FAIL", f"brain_hook check failed: {e}")


def _check_autonomous_task(task_id: str) -> tuple[str, str]:
    """Verify a task id is in tasks.yaml and enabled."""
    try:
        import yaml
        yaml_path = (
            Path(__file__).parent.parent / "autonomous" / "tasks.yaml"
        )
        with open(yaml_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        for t in (data.get("tasks") or []):
            if t.get("id") == task_id:
                enabled = bool(t.get("enabled"))
                cron = t.get("cron", "?")
                if enabled:
                    return ("PASS", f"scheduled: cron=`{cron}`")
                return ("WARN", f"in tasks.yaml but enabled=false (cron=`{cron}`)")
        return ("FAIL", f"task id `{task_id}` NOT FOUND in tasks.yaml")
    except Exception as e:
        return ("WARN", f"tasks.yaml check failed: {e}")


def _self_diag_bearer_token() -> str | None:
    """R-F686 (2026-05-18) — pick up the same bearer token the rest of
    fly accepts so self-diagnostic probes can see PAST the auth wall.
    Without this, every probe returns 401 and the diagnostic can't tell
    a healthy route from a broken one. Prefer ARIA_INTERNAL_TOKEN (the
    machine-to-machine token used by other internal callers like the
    seenode listener); fall back to ARIA_API_TOKEN."""
    for var in ("ARIA_INTERNAL_TOKEN", "ARIA_API_TOKEN"):
        val = (os.getenv(var) or "").strip()
        if val:
            return val
    return None


async def _check_endpoint(
    path: str,
    unauth_ok_codes: tuple[int, ...] = (401,),
) -> tuple[str, str]:
    """Probe fly.io directly for the endpoint.

    R-F686 (2026-05-18) — sends the Bearer token when one is set in env
    so the probe can see actual endpoint behaviour, not just the auth
    wall. Live fly logs 2026-05-18 10:01:24 showed 16+ self-diagnostic
    probes hitting `aria-intel.fly.dev` and getting 401 because no token
    was being sent — silently masking any 5xx behind those endpoints.

    Accept rules:
      - With token: success means HTTP 200 OR one of the spec's
        unauth_ok_codes (covers POST-only 405s + body-required 422s +
        intentional 404s). HTTP 401 in this branch is a TOKEN PROBLEM,
        not "route exists" — bubbled up as FAIL.
      - Without token: keep the original behaviour — the listed
        unauth_ok_codes are "route exists, auth enforced". 401 is a
        valid PASS signal.
      - 404 is always FAIL unless the spec includes it (trace lookup).
      - 5xx is WARN.
    """
    fly_url = os.getenv("ARIA_FLY_URL") or "https://aria-intel.fly.dev"
    token = _self_diag_bearer_token()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        import httpx
        async with httpx.AsyncClient(timeout=6.0) as client:
            # Try GET; some endpoints are POST-only and return 405
            r = await client.get(
                f"{fly_url.rstrip('/')}{path}", headers=headers,
            )
            status = r.status_code

            # R-F686 — with a bearer, a 401 means token was rejected.
            # That's a configuration bug worth surfacing, not a PASS.
            if token and status == 401:
                return (
                    "FAIL",
                    "HTTP 401 with bearer token — token may be invalid / "
                    "rotated; check ARIA_INTERNAL_TOKEN secret",
                )

            # With a token, also accept HTTP 200 as PASS even if the
            # spec only listed unauth codes (the spec was authored
            # assuming no auth).
            effective_ok = set(unauth_ok_codes)
            if token:
                effective_ok.add(200)

            if status in effective_ok:
                return ("PASS", f"route exists (HTTP {status})")
            if status == 404:
                return ("FAIL", "HTTP 404 — route MISSING on fly.io")
            if status >= 500:
                return ("WARN", f"HTTP {status} — route exists but erroring")
            return ("PASS", f"HTTP {status}")
    except Exception as e:
        return ("WARN", f"probe failed (network?): {str(e)[:100]}")


def _check_env_var(var: str) -> tuple[str, str]:
    val = os.getenv(var, "").strip()
    if val and val not in ("0", "false", "no", "off"):
        return ("PASS", f"{var} is set")
    return ("WARN", f"{var} not set — module is in graceful-degrade mode")


async def _check_smoke(mod: Any) -> tuple[str, str]:
    """Source adapters expose an `is_available` coroutine.

    Timeout bumped 15→30s on 2026-04-21: ofac_sdn (treasury.gov sdn.xml GET
    up to 10s), fcdo_sanctions (Azure blob up to 8s), scraper_playwright_engine
    (browser cold-start up to 30s). Under fly.io network jitter + concurrent
    smoke checks (8-way semaphore), these were falsely flipping to FAIL. A
    genuine outage still fails within 30s so the diagnostic stays useful.
    """
    if mod is None or not hasattr(mod, "is_available"):
        return ("WARN", "no is_available() exposed")
    try:
        ok = await asyncio.wait_for(mod.is_available(), timeout=30.0)
        return (
            "PASS" if ok else "WARN",
            "upstream reachable" if ok else "upstream unreachable (may need credentials)",
        )
    except Exception as e:
        return ("FAIL", f"is_available raised: {type(e).__name__}: {str(e)[:100]}")


# ── Main diagnostic ─────────────────────────────────────────────────────────

async def _check_module(spec: dict) -> dict:
    """Run all checks for one module. Network-bound probes run
    in-coroutine so the outer gather can parallelise across modules."""
    entry_row: dict[str, Any] = {
        "name": spec["name"],
        "critical": bool(spec.get("critical", False)),
        "checks": [],
        "worst_status": "PASS",
    }

    # 1. Import (sync, fast)
    imp_status, imp_note, mod = _check_import(spec["module"])
    entry_row["checks"].append({"check": "import", "status": imp_status, "note": imp_note})

    # 2. Entry function
    ent_status, ent_note = _check_entry(mod, spec["entry"])
    entry_row["checks"].append({"check": "entry", "status": ent_status, "note": ent_note})

    # 3. Brain registered
    if spec.get("brain_registered"):
        br_status, br_note = _check_brain_registered(
            spec["name"], module_path=spec["module"],
        )
        entry_row["checks"].append({"check": "brain_registered", "status": br_status, "note": br_note})

    # 4. Autonomous task
    if spec.get("autonomous_task_id"):
        at_status, at_note = _check_autonomous_task(spec["autonomous_task_id"])
        entry_row["checks"].append({"check": "autonomous_task", "status": at_status, "note": at_note})

    # 5. Endpoint / route (network)
    if spec.get("endpoint"):
        ep_status, ep_note = await _check_endpoint(
            spec["endpoint"],
            unauth_ok_codes=spec.get("endpoint_unauth_ok_codes", (401,)),
        )
        entry_row["checks"].append({"check": "endpoint", "status": ep_status, "note": ep_note})

    # 6. Env var credential
    if spec.get("env_var"):
        ev_status, ev_note = _check_env_var(spec["env_var"])
        entry_row["checks"].append({"check": "env_var", "status": ev_status, "note": ev_note})

    # 7. Smoke test (source adapters — hits upstream)
    if spec.get("smoke_is_available") and mod is not None:
        sm_status, sm_note = await _check_smoke(mod)
        entry_row["checks"].append({"check": "smoke", "status": sm_status, "note": sm_note})

    rank = {"PASS": 0, "WARN": 1, "FAIL": 2}
    worst = max(
        (c["status"] for c in entry_row["checks"]),
        key=lambda s: rank.get(s, 0),
        default="PASS",
    )
    entry_row["worst_status"] = worst
    return entry_row


async def run_diagnostic() -> dict:
    """Run every check across every registered module — modules run in
    parallel so total time is bounded by the slowest single module, not
    the sum. Returns full report."""
    started = time.time()

    # Run module checks concurrently — bounded parallelism so we don't
    # hammer fly.io with simultaneous probes.
    sem = asyncio.Semaphore(8)

    async def _bounded(spec: dict) -> dict:
        async with sem:
            return await _check_module(spec)

    results = await asyncio.gather(
        *[_bounded(spec) for spec in _MODULES],
        return_exceptions=False,
    )

    # Summary rollup
    pass_count = sum(1 for r in results if r["worst_status"] == "PASS")
    warn_count = sum(1 for r in results if r["worst_status"] == "WARN")
    fail_count = sum(1 for r in results if r["worst_status"] == "FAIL")
    critical_fails = [
        r["name"] for r in results
        if r["worst_status"] == "FAIL" and r.get("critical")
    ]

    overall = "RED" if critical_fails else ("AMBER" if fail_count or warn_count else "GREEN")

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "duration_ms": int((time.time() - started) * 1000),
        "overall": overall,
        "counts": {"pass": pass_count, "warn": warn_count, "fail": fail_count},
        "modules_checked": len(results),
        "critical_failures": critical_fails,
        "modules": results,
    }

    # Persist for dashboard fast reads
    try:
        from . import redis_store as rs
        await rs.set_json("crucix:self_diagnostic:latest", report, ex=7 * 86400)
    except Exception:
        pass

    return report


async def run_diagnostic_summary() -> dict:
    """Compact payload for the dashboard tile + public /diagnostic endpoint.
    Safe to expose unauthenticated — only returns overall + counts,
    no per-module details."""
    report = await run_diagnostic()
    return {
        "ok": report["overall"] != "RED",
        "overall": report["overall"],
        "generated_at": report["generated_at"],
        "counts": report["counts"],
        "critical_failures": report["critical_failures"],
        "modules_checked": report["modules_checked"],
    }


async def alert_on_red(report: dict) -> None:
    """When the diagnostic goes RED, push a CRITICAL pending_action so
    the next daily briefing + WA nudge surfaces it."""
    if report.get("overall") != "RED":
        return
    critical = report.get("critical_failures") or []
    if not critical:
        return
    try:
        from . import pending_actions as _pa
        await _pa.record(
            promise=(
                f"Self-diagnostic RED — {len(critical)} critical module(s) "
                f"broken: {', '.join(critical[:5])}"
            ),
            reason="Module import / entry / route check failed",
            resolver_kind="operator_action",
            resolver_ref="review_diagnostic_report",
            severity="CRITICAL",
            source="self_diagnostic",
            operator_prompt=(
                "Run `curl -H 'Authorization: Bearer $ARIA_API_TOKEN' "
                "https://aria-intel.fly.dev/api/aria/diagnostic/details` "
                "to see per-check details."
            ),
            metadata={"critical_failures": critical[:10]},
        )
    except Exception as e:
        logger.debug("[self_diagnostic] alert failed: %s", e)


# ── Autonomous task entry point ────────────────────────────────────────────

async def run_diagnostic_tick() -> dict:
    """Called by the 15-minute autonomous task. Runs the full diagnostic
    and fires an alert if anything critical is red."""
    report = await run_diagnostic()
    await alert_on_red(report)
    # Feed brain once per tick — positive signal when all green, gap
    # signal when anything fails
    try:
        from . import brain_hook as _bh
        await _bh.absorb(
            module="self_diagnostic",
            summary=(
                f"Self-diagnostic: overall={report['overall']} "
                f"pass={report['counts']['pass']} "
                f"warn={report['counts']['warn']} "
                f"fail={report['counts']['fail']}"
            ),
            detail=", ".join(report.get("critical_failures", []))[:300],
            success=report["overall"] != "RED",
            gap_type="knowledge_gap" if report["overall"] == "RED" else None,
            gap_detail=(
                f"Critical modules broken: {report.get('critical_failures')}"
                if report["overall"] == "RED" else None
            ),
            confidence="CONFIRMED",
        )
    except Exception:
        pass
    return report
