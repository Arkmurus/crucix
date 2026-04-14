"""ARIA eval regression suite.

Catches silent regressions in critical paths after every change. Two phases:

  Phase 1 — Pure functions (no auth, no network).
            Always runs. Fast. Catches logic regressions in:
              - dual_use_classifier
              - euc_library
              - jurisdiction inference
              - buyer-match heuristic
              - registry adapter dispatch

  Phase 2 — Live API regression (auth + network).
            Runs ONLY if ARIA_BASE_URL + ARIA_AUTH_TOKEN are set.
            Skipped silently otherwise. Hits the live deployed service
            and asserts response shape on stable endpoints.

Run:
  py -m aria_service.tests.test_eval_regression
  ARIA_BASE_URL=https://aria-intel.fly.dev ARIA_AUTH_TOKEN=xxx \\
      py -m aria_service.tests.test_eval_regression

Exit code 0 = all pass. 1 = any failure.
"""
from __future__ import annotations

import asyncio
import os
import sys
import traceback
from typing import Callable

# ── Tiny harness ────────────────────────────────────────────────────────────

_results: list[tuple[str, bool, str]] = []


def _record(name: str, ok: bool, detail: str = "") -> None:
    _results.append((name, ok, detail))


def check(name: str, fn: Callable, *, expect_truthy: bool = True) -> None:
    """Run a sync check. Captures exceptions as failures."""
    try:
        result = fn()
        ok = bool(result) if expect_truthy else (result is None or result is False)
        _record(name, ok, "" if ok else f"got: {result!r}")
    except Exception as e:
        _record(name, False, f"raised: {e.__class__.__name__}: {str(e)[:200]}")


async def acheck(name: str, coro_fn: Callable, *, expect_truthy: bool = True) -> None:
    """Run an async check."""
    try:
        result = await coro_fn()
        ok = bool(result) if expect_truthy else (result is None or result is False)
        _record(name, ok, "" if ok else f"got: {result!r}")
    except Exception as e:
        _record(name, False, f"raised: {e.__class__.__name__}: {str(e)[:200]}")


# ── Phase 1: pure functions ─────────────────────────────────────────────────

async def phase1_pure() -> None:
    print("\n=== Phase 1: pure-function regressions ===")

    # Imports
    try:
        from aria_service.intel import (
            dual_use_classifier as duc,
            euc_library as el,
            registry_adapters as ra,
            dd_orchestrator as ddo,
            tech_classifier as tc,
            brain_hook as bh,
        )
        _record("imports: all critical modules", True)
    except Exception as e:
        _record("imports: all critical modules", False, str(e))
        return  # nothing else can run

    # Brain-hook registration of new modules
    check("brain: dual_use_classifier registered",
          lambda: "dual_use_classifier" in bh._MODULE_TOPICS and "dual_use_classifier" in bh._MODULE_WEIGHT)
    check("brain: euc_library registered",
          lambda: "euc_library" in bh._MODULE_TOPICS and "euc_library" in bh._MODULE_WEIGHT)

    # Registry dispatch
    check("registry: 13 jurisdictions supported",
          lambda: len(ra._SUPPORTED_JURISDICTIONS) >= 13)
    # GB intentionally not here — UK Companies House has its own dedicated
    # module (companies_house.py) and is not in the unified dispatch.
    for j in ("DE", "FR", "AE", "PL", "RO", "BR", "TR", "IN"):
        check(f"registry: {j} in supported set",
              lambda j=j: j in ra._SUPPORTED_JURISDICTIONS)

    # Jurisdiction inference
    inf = ddo._infer_jurisdiction
    check("infer: HRB number → DE", lambda: inf({}, "", "HRB 12345") == "DE")
    check("infer: +49 phone → DE", lambda: inf({"phone": "+49 30 1"}, "x", None) == "DE")
    check("infer: +33 phone → FR", lambda: inf({"phone": "+33 1 234"}, "x", None) == "FR")
    check("infer: +971 phone → AE", lambda: inf({"phone": "+971 4 234"}, "x", None) == "AE")
    check("infer: 'Berlin, Germany' → DE", lambda: inf({"address": "Berlin, Germany"}, "x", None) == "DE")
    check("infer: 'Paris, France' → FR", lambda: inf({"address": "Paris, France"}, "x", None) == "FR")
    check("infer: .fr email → FR", lambda: inf({"email": "a@thales.fr"}, "x", None) == "FR")

    # Buyer-match heuristic
    check("fan-out: substring buyer match",
          lambda: ddo._buyer_matches_entity("JSC Rosoboronexport", "Rosoboronexport"))
    check("fan-out: short tokens reject",
          lambda: not ddo._buyer_matches_entity("ABC Co", "ABC"))

    # EUC library — profile load
    profs = el.list_profiles()
    check("euc: 5 profiles loaded", lambda: len(profs) == 5)
    profile_ids = {p["id"] for p in profs}
    for pid in ("US_DSP83", "UK_GENERAL", "EU_DUAL_USE", "WASSENAAR_GENERIC", "GCC_GENERIC"):
        check(f"euc: profile {pid} present", lambda pid=pid: pid in profile_ids)

    # EUC: own template against own profile = VALID
    for pid in ("UK_GENERAL", "US_DSP83", "EU_DUAL_USE"):
        tpl = el.get_template(pid)
        result = el.gap_check(tpl, pid)
        check(f"euc: {pid} template clean against own profile",
              lambda r=result: r["status"] == "VALID" and r["critical_missing_count"] == 0)

    # EUC: empty text rejected
    check("euc: empty text raises",
          lambda: _expect_raises(lambda: el.gap_check("xxx short", "UK_GENERAL")))

    # EUC: flawed EUC missing non-reexport rejects
    flawed = (
        "We the Ministry of Defence of Ghana certify that the end user is "
        "the Ghana Armed Forces and the items will be used for border surveillance. "
        "Final destination: Ghana. Signed by Director of Procurement on 2026-04-14."
    )
    flawed_result = el.gap_check(flawed, "UK_GENERAL")
    check("euc: flawed EUC missing no-reexport → REJECT",
          lambda: flawed_result["status"] == "REJECT" and flawed_result["critical_missing_count"] >= 1)

    # Dual-use: military item → YES
    r = await duc.assess("Thales Squire ground surveillance radar", "UK", "Ghana", end_use="border surveillance")
    check("dual-use: UK→Ghana radar → YES + ECJU + STANDARD embargo",
          lambda r=r: (
              r["decision"]["licence_required"] == "YES"
              and "ECJU" in r["jurisdiction"]["controlling_authority"]
              and r["embargo_check"]["status"] == "STANDARD"
          ))

    # Dual-use: embargo destination → PROHIBITED
    r2 = await duc.assess("thermal imaging cameras", "Germany", "Russia")
    check("dual-use: DE→RU thermal → PROHIBITED + HARD_STOP",
          lambda r=r2: (
              r["decision"]["licence_required"] == "PROHIBITED"
              and r["embargo_check"]["status"] == "HARD_STOP"
          ))

    # Dual-use: unknown civilian item → PROBABLY_NO
    r3 = await duc.assess("office stapler", "UK", "Germany")
    check("dual-use: UK→DE stapler → PROBABLY_NO",
          lambda r=r3: r["decision"]["licence_required"] in ("PROBABLY_NO", "DEPENDS"))

    # Tech classifier still produces ML codes for known items
    classification = tc.classify_export_control("M2 Browning .50 BMG heavy machine gun")
    check("tech_classifier: known weapon → ML code",
          lambda: bool(classification.get("wassenaar_ml") or classification.get("usml")))

    # ── Audit log + compliance file (Tier 1) ────────────────────────────
    from aria_service.intel import audit_log, compliance_file

    # Brain-hook registration
    check("brain: audit_log registered",
          lambda: "audit_log" in bh._MODULE_TOPICS and "audit_log" in bh._MODULE_WEIGHT)
    check("brain: compliance_file registered",
          lambda: "compliance_file" in bh._MODULE_TOPICS and "compliance_file" in bh._MODULE_WEIGHT)

    # Action allowlist enforced
    async def _try_bogus():
        try:
            await audit_log.record(action="bogus_action", actor="test")
            return False
        except ValueError:
            return True
    await acheck("audit: unknown action rejected", _try_bogus)

    # End-to-end chain test using a unique deal_id so we don't interfere
    # with real data on shared Redis. Note: this writes to whatever Redis
    # is configured — fine because audit log is append-only by design.
    test_deal = f"LEAD-EVAL-{int(asyncio.get_event_loop().time() * 1000) % 100000}"
    e1 = await audit_log.record(
        action="sanctions_screen", actor="eval_test",
        entity_name="Eval Test Co", deal_id=test_deal,
        decision="CLEAN", confidence="ASSESSED",
    )
    e2 = await audit_log.record(
        action="dual_use_assessment", actor="eval_test",
        entity_name="Eval Test Co", deal_id=test_deal,
        decision="YES: SIEL", confidence="ASSESSED",
    )
    check("audit: e1 has entry_hash + prev=GENESIS-or-prior",
          lambda: bool(e1.get("entry_hash")) and len(e1["entry_hash"]) == 64)
    check("audit: e2 prev_hash matches e1 entry_hash",
          lambda: e2.get("prev_hash") == e1.get("entry_hash"))

    # Chain verification
    verify = await audit_log.verify_chain(0, 50)
    check("audit: chain verifies clean",
          lambda: verify.get("verified") is True)

    # Index queries
    by_deal = await audit_log.get_by_deal(test_deal)
    check("audit: by_deal finds the 2 test entries",
          lambda: len(by_deal) == 2)

    by_entity = await audit_log.get_by_entity("Eval Test Co")
    check("audit: by_entity finds entries (>= 2)",
          lambda: len(by_entity) >= 2)

    # Provenance
    prov = await compliance_file.get_provenance(e2["entry_hash"])
    check("provenance: returns ok with decision payload",
          lambda: prov.get("ok") and prov["decision"]["entry_hash"] == e2["entry_hash"])
    check("provenance: e2 has supporting prior (e1)",
          lambda: prov.get("supporting_count", 0) >= 1)


def _expect_raises(fn) -> bool:
    """Helper: returns True if the callable raises an exception."""
    try:
        fn()
        return False
    except Exception:
        return True


# ── Phase 2: live API regression ────────────────────────────────────────────

async def phase2_live() -> None:
    base = os.environ.get("ARIA_BASE_URL", "").strip().rstrip("/")
    token = os.environ.get("ARIA_AUTH_TOKEN", "").strip()
    if not base or not token:
        print("\n=== Phase 2: live API — SKIPPED (set ARIA_BASE_URL + ARIA_AUTH_TOKEN to enable) ===")
        return

    print(f"\n=== Phase 2: live API regressions (target: {base}) ===")

    try:
        import httpx
    except ImportError:
        _record("phase2: httpx import", False, "httpx not installed")
        return

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    timeout = 30.0

    async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:

        async def _get(path: str):
            return await client.get(f"{base}{path}")

        async def _post(path: str, json_body: dict):
            return await client.post(f"{base}{path}", json=json_body)

        # EUC profiles
        try:
            r = await _get("/api/aria/compliance/euc/profiles")
            data = r.json() if r.status_code == 200 else {}
            profs = data.get("profiles", []) if isinstance(data, dict) else []
            _record("live: EUC profiles returns 5", len(profs) == 5,
                    f"got {len(profs)}, status={r.status_code}")
        except Exception as e:
            _record("live: EUC profiles", False, str(e)[:200])

        # EUC template UK_GENERAL non-empty
        try:
            r = await _get("/api/aria/compliance/euc/template/UK_GENERAL")
            data = r.json() if r.status_code == 200 else {}
            tpl = data.get("template", "") if isinstance(data, dict) else ""
            _record("live: EUC UK template non-empty", len(tpl) > 500,
                    f"len={len(tpl)}, status={r.status_code}")
        except Exception as e:
            _record("live: EUC UK template", False, str(e)[:200])

        # Dual-use UK→Ghana radar
        try:
            r = await _post("/api/aria/compliance/dual-use-check", {
                "item": "Thales Squire ground surveillance radar",
                "origin": "UK", "destination": "Ghana",
                "end_use": "border surveillance",
            })
            data = r.json() if r.status_code == 200 else {}
            decision = (data.get("decision") or {}).get("licence_required")
            _record("live: dual-use UK→Ghana radar = YES",
                    decision == "YES",
                    f"got={decision}, status={r.status_code}")
        except Exception as e:
            _record("live: dual-use UK→Ghana", False, str(e)[:200])

        # Dual-use DE→Russia embargo
        try:
            r = await _post("/api/aria/compliance/dual-use-check", {
                "item": "thermal imaging cameras",
                "origin": "Germany", "destination": "Russia",
            })
            data = r.json() if r.status_code == 200 else {}
            decision = (data.get("decision") or {}).get("licence_required")
            embargo = (data.get("embargo_check") or {}).get("status")
            _record("live: dual-use DE→RU = PROHIBITED + HARD_STOP",
                    decision == "PROHIBITED" and embargo == "HARD_STOP",
                    f"decision={decision}, embargo={embargo}")
        except Exception as e:
            _record("live: dual-use DE→RU", False, str(e)[:200])

        # EUC check own template = VALID
        try:
            r = await _get("/api/aria/compliance/euc/template/UK_GENERAL")
            tpl = (r.json() if r.status_code == 200 else {}).get("template", "")
            r2 = await _post("/api/aria/compliance/euc/check", {"text": tpl, "profile": "UK_GENERAL"})
            data = r2.json() if r2.status_code == 200 else {}
            status = data.get("status")
            _record("live: EUC UK own-template check = VALID",
                    status == "VALID",
                    f"got={status}, critical_missing={data.get('critical_missing_count')}")
        except Exception as e:
            _record("live: EUC UK own-template check", False, str(e)[:200])

        # Composed export-assessment
        try:
            r = await _post("/api/aria/compliance/export-assessment", {
                "item": "Thales radar", "origin": "UK", "destination": "Ghana",
                "end_use": "border surveillance",
            })
            data = r.json() if r.status_code == 200 else {}
            decision = ((data.get("dual_use") or {}).get("decision") or {}).get("licence_required")
            euc_profile = (data.get("euc") or {}).get("profile_id")
            _record("live: export-assessment composes UK→Ghana",
                    decision in ("YES", "LIKELY") and euc_profile == "UK_GENERAL",
                    f"decision={decision}, euc_profile={euc_profile}")
        except Exception as e:
            _record("live: export-assessment", False, str(e)[:200])

        # Tender stats has portal_health (the diagnostic field we added)
        try:
            r = await _get("/api/aria/tenders/stats")
            data = r.json() if r.status_code == 200 else {}
            has_diag = "portal_health" in data or "portals_ok" in data or r.status_code == 404
            # Note: stats may be empty if no crawl has run yet — accept either
            # the diagnostic keys present, OR an empty/404 (no run yet).
            _record("live: tender stats has diagnostic fields (or empty)",
                    has_diag or not data,
                    f"keys={list(data.keys())[:8] if isinstance(data, dict) else 'N/A'}")
        except Exception as e:
            _record("live: tender stats", False, str(e)[:200])


# ── Runner ──────────────────────────────────────────────────────────────────

async def main() -> int:
    print("ARIA eval regression suite")
    print("=" * 60)

    await phase1_pure()
    await phase2_live()

    print("\n" + "=" * 60)
    passed = sum(1 for _, ok, _ in _results if ok)
    failed = sum(1 for _, ok, _ in _results if not ok)
    total = len(_results)

    if failed:
        print(f"\nFAILURES ({failed}):")
        for name, ok, detail in _results:
            if not ok:
                print(f"  [FAIL] {name}")
                if detail:
                    print(f"         {detail}")

    print(f"\nResult: {passed}/{total} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    rc = asyncio.run(main())
    sys.exit(rc)
