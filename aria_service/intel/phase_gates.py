"""R-F2639 — the ONE canonical Phase A gate measurement.

Why this module exists
──────────────────────
Two independent gate aggregators were live at once and DISAGREED:

  * ``main.py``            → ``GET /phase/gates``          (unauthenticated)
  * ``routes/aria.py``     → ``GET /api/aria/phase/gates`` (Bearer-gated)

They read different sources for the same gate, so "is gate #N closed?" had two
answers depending on which URL you asked. Audited 2026-07-16:

  gate #3  main measured the R-F2622 durable streak anchor (honest);
           the fork used ``errors == 0 → closed`` — the EXACT vacuous pass
           R-F2622 killed, where an empty/evicted ledger reads as a clean week.
  gate #6  main passed on ``len(golden_set) >= 500`` (SIZE, not frozen);
           the fork read a key nothing in the tree ever wrote (uncloseable).
           Both wrong, in opposite directions → R-F2640 measures the pin.
  gate #7  main read design_partner_tracker (honest, operator-owned);
           the fork counted DISTINCT CHAT SESSIONS — so ARIA's own traffic
           closed a gate CLAUDE.md §1 defines as operator-owned/uncodeable.
  gate #5  the FORK was the stricter one (3 vars incl. ARIA_AUTONOMY_LEVEL);
           main checked only 2 and would have silently LOOSENED the gate.
  gate #4  BOTH forks read ``crucix:aria:dd:quarantined`` — a key with NO
           WRITER anywhere in the tree → ``[] → 0 → pass=True``, unconditional.
           R-F2643 re-points it at run_quarantine.closure_summary(). See there.

So this module is not "main won". Each gate takes the HONEST reading, whichever
fork had it — consolidating onto either one wholesale would have laundered a
fabrication through a refactor. ``main.py`` gate #3 already states the rule this
module generalises: **one measure, one verdict.**

A warning for whoever edits this next: R-F2639 originally carried gate #4
forward verbatim from main.py, because it *looked* measured — it read a real
key, handled exceptions, and had a confident comment calling that key "the REAL
source". It took a second pass to notice nothing writes it. **Inheriting a
measurement is not verifying it.** Grep for the writer.

Contract
────────
``compute_phase_gates()`` returns canonical records; the two endpoints RENDER
them into their existing response shapes (dict-keyed / list-of-status) so no
consumer contract breaks. Neither endpoint may measure anything itself.

``pass`` is tri-state and that is load-bearing:
    True  → measured, and it passed
    False → measured, and it failed
    None  → COULD NOT MEASURE (store failure). "Could not measure" is not
            "measured and failed" — asserting a verdict the data doesn't
            support is R-F560's sin (R-F2375/R-F2622 keep the distinction).
"""
from __future__ import annotations

import logging
import time
from typing import Any
from .engine_wiring import wired  # R-F3557 (§21a)

logger = logging.getLogger("aria.intel.phase_gates")

GATE_1_TARGET = 0.71
GATE_2_TARGET = 0.70
GATE_7_TARGET = 4


def _safe_err(e: Exception) -> str:
    """Render an exception for an UNAUTHENTICATED response body.

    /phase/gates is public. Raw str(e) from the store layer carries the DB path
    and SQL text (state_store: "SELECT <key> failed: …"), so emit the exception
    TYPE plus a bounded message — enough to diagnose, not a schema dump. This
    mirrors what /diagnostic already does with str(e)[:200].
    """
    return f"{type(e).__name__}: {str(e)[:200]}"


def _gate(
    gate_id: int,
    key: str,
    label: str,
    title: str,
    value: Any,
    passed: bool | None,
    evidence: str,
    **extras: Any,
) -> dict:
    """One canonical gate record. ``measurable`` is derived from ``pass`` so the
    two can never drift apart."""
    rec = {
        "id": gate_id,
        "key": key,
        "label": label,
        "title": title,
        "value": value,
        "pass": passed,
        "measurable": passed is not None,
        "evidence": evidence,
    }
    rec.update(extras)
    return rec


@wired(module="phase_gates", summary="phase gates measured", gap_type="engine_failure")
async def compute_phase_gates() -> dict:
    """Measure all 7 Phase A exit gates from LIVE probes.

    Every value reads from a live source — never from CLAUDE.md or any
    human-edited document. Editing markdown does NOT change these values.
    """
    # Lazy imports (matching the previous in-function pattern): keeps module
    # import cheap at boot and avoids intel-package import cycles. Modules are
    # bound, then attributes resolved at CALL time, so tests can monkeypatch
    # e.g. student.get_regional_heatmap and have it take effect here.
    from . import redis_store as rs
    from . import student
    from . import autonomy_scorer
    from . import error_streak
    from . import eval_runner
    from . import design_partner_tracker

    gates: dict[str, dict] = {}
    sources: dict[str, str] = {}

    # ── Gate #1: Composite >= 71% ──────────────────────────────────────────
    # R-F1557: the old fork called autonomy_scorer.composite_score() / .heatmap(),
    # neither of which exists → AttributeError → swallowed → "unknown".
    try:
        comp = await autonomy_scorer.compute_composite()
        cs = (comp or {}).get("composite_score")
        _low_conf = (comp or {}).get("low_confidence", True)

        # ── R-F4231 (C-211) — AN UNMEASURED AXIS IS NOT A VERDICT ────────────
        #
        # `compute_composite` renormalises over the signals that HAVE data
        # (`measured_sum / measured_weight`), and this gate compared that to
        # GATE_1_TARGET — a threshold defined over the FULL weight set. Those
        # are different quantities, so the comparison is undefined in BOTH
        # directions whenever an axis is missing.
        #
        # Measured live 2026-08-22: mastery 0.838, verification 0.594,
        # honesty_rate **None** (`no_data_neutral_prior`, 0 samples — the
        # honesty judge has 55 judgments in the platform's LIFETIME). The
        # renormalised score read 0.6916 and the gate reported a confident
        # `pass: false`. The true full-weight composite for those signals is
        # 0.5187 / 0.6437 / 0.7687 at honesty 0.0 / 0.5 / 1.0 — it STRADDLES
        # the 0.71 target, so `false` was as unfounded as `true` would have been.
        #
        # R-F2665's comment claims this gate closes only with "both honesty
        # signals present with real samples", but it enforces
        # `confidence >= MIN_CONFIDENCE (0.60)` where confidence is a fraction
        # of WEIGHT. Honesty carries 0.25, so mastery + verification alone give
        # 0.75 and the flag stays False. The guard was calibrated against the
        # mastery-ONLY case (0.30) and went INERT the moment verification began
        # reporting — a guard that stopped being able to fail rather than
        # failing (R-F3791/R-F3858). Verified against the pre-fix tree: a 0.75
        # composite with ZERO honesty samples returned `pass: True`.
        #
        # This is Phase A — the HONESTY foundation — and this is its exit gate.
        #
        # So `pass` is tri-state, the contract §1/R-F2639 already binds every
        # gate to: True/False = measured, None = COULD NOT MEASURE, rendered
        # `unknown` and never `open`. The score, the target and the missing axis
        # names are all still published, so this MEASURES MORE rather than
        # clamping (§1) — it only refuses to turn an absence into a verdict, and
        # `unknown` can never help Phase A exit.
        #
        # A payload with NO `signals` key is `unknown` too, deliberately: the
        # `{} -> nothing missing -> all measured` collapse is precisely how the
        # three fabricated Phase A gates in §1 were certified by an absence.
        _sig = (comp or {}).get("signals")
        if isinstance(_sig, dict):
            _unmeasured = sorted(k for k, v in _sig.items() if v is None)
        else:
            _unmeasured = ["signals_unavailable"]
        _comparable = not _unmeasured
        gates["gate_1_composite"] = _gate(
            1, "gate_1_composite", "Composite >= 71%", "Composite score ≥71%",
            round(cs, 3) if isinstance(cs, (int, float)) else cs,
            # R-F2665: pass requires BOTH the score AND real confidence. Pre-R-F2665
            # `pass` was pure threshold (cs >= 0.71) with NO confidence gate — so a
            # 0.71 built on mastery ALONE (verification 45% + honesty 25% absent →
            # 70% of the weight unmeasured and renormalised away) would FALSELY
            # certify gate #1 on thin evidence, with ARIA's honesty/grounding axis
            # (the moat) entirely unmeasured. Now a low-confidence score
            # (confidence < MIN_CONFIDENCE 0.60) cannot pass: gate #1 closes only
            # when the composite is >= 0.71 AND measured at real confidence (both
            # honesty signals present with real samples). This TIGHTENS the gate to
            # be honest; it does not clamp it (CLAUDE.md §1 — measure MORE, not less).
            # R-F4231 — None (unknown) when an axis is unmeasured, because the
            # renormalised score is not comparable to a full-weight target.
            (None if not _comparable
             else (cs is not None and cs >= GATE_1_TARGET and not _low_conf)),
            "autonomy_scorer.compute_composite()['composite_score']",
            target=GATE_1_TARGET,
            confidence=(comp or {}).get("confidence"),
            low_confidence=_low_conf,
            unmeasured_signals=_unmeasured,
        )
        sources["composite"] = "compute_composite()"
    except Exception as e:
        gates["gate_1_composite"] = _gate(
            1, "gate_1_composite", "Composite >= 71%", "Composite score ≥71%",
            None, None, "autonomy_scorer.compute_composite() — FAILED", error=_safe_err(e))
        sources["composite"] = f"error: {_safe_err(e)}"

    # ── Gate #2: Heatmap floor >= 70% ──────────────────────────────────────
    # NOT closeable by measuring less: see CLAUDE.md §1's anti-clamp list
    # (no dropping regions, no truncating breach cells, no seed knobs).
    try:
        hm_data = await student.get_regional_heatmap()
        hm = (hm_data or {}).get("heatmap", {}) or {}
        all_scores = [s for regions in hm.values() for s in regions.values()]
        floor = min(all_scores) if all_scores else None
        breach = (hm_data or {}).get("floor_breach_cells", []) or []
        gates["gate_2_heatmap_floor"] = _gate(
            2, "gate_2_heatmap_floor", "Heatmap floor >= 70%", "Heatmap floor ≥70%",
            round(floor, 3) if floor is not None else None,
            # An EMPTY heatmap is no-data, not a failure → unmeasurable.
            None if floor is None else floor >= GATE_2_TARGET,
            "student.get_regional_heatmap() — min mastery cell",
            target=GATE_2_TARGET,
            floor_breach_cells=breach,
        )
        sources["heatmap"] = "student.get_regional_heatmap()"
    except Exception as e:
        gates["gate_2_heatmap_floor"] = _gate(
            2, "gate_2_heatmap_floor", "Heatmap floor >= 70%", "Heatmap floor ≥70%",
            None, None, "student.get_regional_heatmap() — FAILED",
            floor_breach_cells=[], error=_safe_err(e))
        sources["heatmap"] = f"error: {_safe_err(e)}"

    # ── Gate #3: 0 fly ERRORs / 7d ─────────────────────────────────────────
    # THE honesty gate. R-F560 certified this whenever no ERROR was FOUND —
    # including on an empty ledger — with a hardcoded 7-day streak. R-F2622
    # rebuilt it on a durable, TTL-less anchor written at record_error() time
    # and reports pass=False + insufficient_history when 7 clean days cannot be
    # PROVEN. The fork still served the R-F560 version until R-F2639 deleted it.
    #
    # This reader must stay HONEST: absence of evidence is not evidence of
    # cleanliness. Do NOT coerce an unproven streak to a pass. Expect it to read
    # pending until real evidence accrues — that is the gate being EARNED.
    try:
        err_total_raw = await rs.get("crucix:aria:error_ledger:count")
        try:
            err_total = int(err_total_raw) if err_total_raw is not None else None
        except (TypeError, ValueError):
            err_total = None
        streak = await error_streak.compute_error_streak()
        streak_err = streak.get("error")
        gates["gate_3_zero_errors"] = _gate(
            3, "gate_3_zero_errors", "0 fly ERRORs/7d", "0 fly ERRORs in 7 days",
            streak.get("consecutive_clean_days"),
            None if streak_err else streak.get("phase_a_gate_3_pass"),
            "error_streak.compute_error_streak() (R-F2622 anchor)",
            measure_error=streak_err,
            streak_basis=streak.get("streak_basis"),
            insufficient_history=streak.get("insufficient_history"),
            clean_since=streak.get("clean_since"),
            gate_blocked_reason=streak.get("gate_blocked_reason"),
            error_ledger_total_all_time=err_total,
            note="R-F2622: MEASURED from the durable error-streak anchor. pass=true "
                 "requires 7 PROVEN clean days; an unproven streak reports "
                 "insufficient_history, never an assumed pass.",
        )
        sources["errors"] = "error_streak.compute_error_streak() (R-F2622)"
    except Exception as e:
        gates["gate_3_zero_errors"] = _gate(
            3, "gate_3_zero_errors", "0 fly ERRORs/7d", "0 fly ERRORs in 7 days",
            None, None, "error_streak.compute_error_streak() — FAILED", error=_safe_err(e))
        sources["errors"] = f"error: {_safe_err(e)}"

    # ── Gate #4: Quarantined DDs closed ────────────────────────────────────
    # R-F2643 (2026-07-16): this gate was a FABRICATED PASS, and R-F2639 nearly
    # shipped it forward unexamined.
    #
    # R-F2375 replaced a -1-sentinel reader with `len(get_json(
    # "crucix:aria:dd:quarantined")) == 0` and called that "the REAL source".
    # It was not: a repo-wide grep finds NO WRITER for that key — only the
    # readers themselves. get_json returns None for an absent key AND swallows
    # store failures (redis_store.py:299-303 — the None-on-error contract), so
    # `[] → 0 → pass=True` was unconditional. Gate #4 could not fail. That is
    # the R-F560 vacuous pass, and it is the evidence behind CLAUDE.md §1's
    # "#4 quarantined DDs closed ✅".
    #
    # The honest source is run_quarantine (_KEY = crucix:aria:quarantined_runs),
    # whose closure_summary() is documented as the "Phase A gate #4 closer
    # surface" and answers the question the gate actually asks: are quarantined
    # runs INVESTIGATED (closed), not merely "is the list empty". Its
    # gate_passes requires len(items) > 0, so an empty/unreadable store cannot
    # vacuously pass it either.
    try:
        from . import run_quarantine

        cs4 = await run_quarantine.closure_summary()
        # R-F3697 — preserve the TRI-STATE. `bool(...)` collapsed None to False,
        # which renders "could not measure" as "measured and failed" — exactly
        # the distinction this module's own contract calls load-bearing.
        # closure_summary now returns gate_passes=None when the quarantine store
        # is unreadable, instead of silently certifying on the four code-resident
        # seeds (all of which are hardcoded `investigation_status: "closed"`).
        _gp4 = cs4.get("gate_passes")
        gates["gate_4_quarantine_closed"] = _gate(
            4, "gate_4_quarantine_closed", "Quarantined DDs closed", "Quarantined DDs closed",
            cs4.get("open"), (None if _gp4 is None else bool(_gp4)),
            "run_quarantine.closure_summary() — investigated vs open (R-F2643/R-F3697)",
            total=cs4.get("total"),
            closed=cs4.get("closed"),
            open_run_ids=cs4.get("open_run_ids"),
            # R-F3697 — expose the BASIS so "4/4 closed" carried entirely by
            # code-resident seeds is distinguishable from a real investigated
            # estate. dynamic_total == 0 means the store contributed nothing.
            seeded_total=cs4.get("seeded_total"),
            dynamic_total=cs4.get("dynamic_total"),
            measure_error=cs4.get("measure_error"),
        )
        sources["quarantine"] = "run_quarantine.closure_summary() (R-F2643)"
    except Exception as e:
        gates["gate_4_quarantine_closed"] = _gate(
            4, "gate_4_quarantine_closed", "Quarantined DDs closed", "Quarantined DDs closed",
            None, None, "run_quarantine.closure_summary() — FAILED", error=_safe_err(e))
        sources["quarantine"] = f"error: {_safe_err(e)}"

    # ── Gate #5: required env vars set ─────────────────────────────────────
    # R-F1557: live fly secrets are ARIA_-prefixed; accept the prefixed name
    # first, fall back to the bare name for back-compat.
    # R-F2639: takes the FORK's stricter 3-var check. main.py checked only
    # AUTONOMOUS_ENABLED + OUTPUT_HARVEST_ENABLED and omitted ARIA_AUTONOMY_LEVEL,
    # so consolidating onto main would have LOOSENED a gate that currently reads
    # PASS. §17 declares all three; all three are checked.
    # ACLED is DEFERRED per operator 2026-06-07 (until MVP launch) — not checked.
    try:
        import os

        def _first_env(*names: str) -> str | None:
            for n in names:
                v = os.environ.get(n)
                if v is not None:
                    return v
            return None

        auto = _first_env("ARIA_AUTONOMOUS_ENABLED", "AUTONOMOUS_ENABLED")
        harvest = _first_env("ARIA_OUTPUT_HARVEST_ENABLED", "HARVEST_ENABLED")
        level = _first_env("ARIA_AUTONOMY_LEVEL", "AUTONOMY_LEVEL")

        # R-F3640 — the autonomy master switch is NOT env-only, so reading env alone
        # measured the wrong surface. `engine.is_enabled()` documents the precedence:
        # a durable override at crucix:autonomous:enabled_override wins over the env
        # var in BOTH directions ("1" force-enables, "0" force-disables), and it is the
        # designed control plane — /autonomous/enable exists so the switch can flip
        # without a redeploy (the 2026-04-18 wrong-environment incident in engine.py).
        #
        # Live on aria-intel 2026-08-02: ARIA_AUTONOMOUS_ENABLED=0 while the override
        # is "1" and the engine is genuinely running at L3 (98 tasks, ticking). The gate
        # reported OPEN for a capability that was ON — a false NEGATIVE. It is the mirror
        # of the fabricated passes this file keeps removing, and it is fixed the same way:
        # by measuring MORE, never by assuming. The source is reported per-var so a pass
        # earned by the override can never be mistaken for a pass earned by the secret.
        auto_ok: bool | None
        auto_src: str
        try:
            # get_STRICT, matching engine.refresh_runtime_override() exactly: the
            # override is written with rs.set() as a bare string, so the json layer in
            # get_json_strict would be a second interpretation of the same value — and
            # it swallows a parse failure into None, which here would silently fall
            # back to env and reproduce the very false negative this fixes.
            override = await rs.get_strict("crucix:autonomous:enabled_override")
        except Exception:
            # Cannot rule out an override in EITHER direction, so the effective state is
            # genuinely unknown — never "measured and failed" (the tri-state contract).
            override, auto_ok, auto_src = None, None, "override_unreadable"
        else:
            ov = str(override).strip() if override is not None else ""
            if ov in ("0", "1"):
                auto_ok, auto_src = ov == "1", f"runtime_override={ov}"
            else:
                auto_ok, auto_src = auto == "1", "env"

        env_status = {
            # harvest + level have no override mechanism — env is the whole truth there.
            "ARIA_AUTONOMOUS_ENABLED": auto_ok,
            "ARIA_OUTPUT_HARVEST_ENABLED": harvest == "1",
            "ARIA_AUTONOMY_LEVEL": bool(level and level.isdigit() and int(level) >= 1),
        }
        missing = [k for k, ok in env_status.items() if ok is False]
        unknown = [k for k, ok in env_status.items() if ok is None]
        gates["gate_5_env_vars"] = _gate(
            5, "gate_5_env_vars", "Env vars set", "Required env vars set",
            {"missing": missing, "total": len(env_status),
             **({"unknown": unknown} if unknown else {})},
            None if unknown else not missing,
            "os.environ + autonomous runtime override (R-F1557/R-F3640)",
            by_var=env_status,
            by_var_source={"ARIA_AUTONOMOUS_ENABLED": auto_src,
                           "ARIA_OUTPUT_HARVEST_ENABLED": "env",
                           "ARIA_AUTONOMY_LEVEL": "env"},
            env_var_value={"ARIA_AUTONOMOUS_ENABLED": auto},
            note="ACLED deferred per operator 2026-06-07 (MVP launch)",
        )
        sources["env_vars"] = "os.environ + crucix:autonomous:enabled_override"
    except Exception as e:
        gates["gate_5_env_vars"] = _gate(
            5, "gate_5_env_vars", "Env vars set", "Required env vars set",
            None, None, "os.environ — FAILED", error=_safe_err(e))
        sources["env_vars"] = f"error: {_safe_err(e)}"

    # ── Gate #6: 500-Q eval FROZEN ─────────────────────────────────────────
    # R-F2640: measures the PIN (count + content hash), not the size of a
    # mutable list. See eval_runner.get_freeze_status() for why both previous
    # readings were wrong in opposite directions.
    try:
        fs = await eval_runner.get_freeze_status()
        gates["gate_6_eval_frozen"] = _gate(
            6, "gate_6_eval_frozen", "500-Q eval frozen", "500-Q eval frozen",
            fs.get("live_count") if fs.get("measurable") else None,
            fs.get("gate_pass"),
            "eval_runner.get_freeze_status() — count+hash pin (R-F2640)",
            target=fs.get("target", eval_runner.GATE_6_TARGET),
            frozen=fs.get("frozen"),
            drifted=fs.get("drifted"),
            reason=fs.get("reason"),
            detail=fs.get("detail"),
            pinned_count=fs.get("pinned_count"),
            pinned_hash=fs.get("pinned_hash"),
            live_hash=fs.get("live_hash"),
            frozen_at=fs.get("frozen_at"),
            frozen_by=fs.get("frozen_by"),
            measure_error=fs.get("error"),
        )
        sources["eval"] = "eval_runner.get_freeze_status() (R-F2640)"
    except Exception as e:
        gates["gate_6_eval_frozen"] = _gate(
            6, "gate_6_eval_frozen", "500-Q eval frozen", "500-Q eval frozen",
            None, None, "eval_runner.get_freeze_status() — FAILED", error=_safe_err(e))
        sources["eval"] = f"error: {_safe_err(e)}"

    # ── Gate #7: >=4 design-partner conversations ──────────────────────────
    # R-F1987: reads the DesignPartnerTracker store.
    # R-F2639 DELETED the fork's chat-session proxy, which counted distinct
    # session_ids from chat_audit_log and closed the gate at >=4 — letting
    # ARIA's own traffic certify a gate CLAUDE.md §1 defines as operator-owned.
    # Its own comment conceded "a distinct session ≠ a verified design-partner".
    # Gate #7 does not close from code. That is the point of it.
    try:
        stats = design_partner_tracker.get_tracker().stats()
        # R-F2673: the gate VALUE is the QUALIFIED count, not total. Public
        # applications (status='applied', via partners.html) and declined rows
        # are in `total`/`by_status` for the admin UI but must NOT move the gate
        # — only an operator qualifying a partner does (CLAUDE.md §1: gate #7 is
        # operator-owned, does not close from code / public traffic).
        gates["gate_7_design_partners"] = _gate(
            7, "gate_7_design_partners", ">=4 design-partner convos",
            "≥4 design-partner conversations",
            stats.get("qualified", stats["total"]), stats["gate_pass"],
            "design_partner_tracker.stats() — QUALIFIED operator-vouched partners (R-F1987/R-F2673)",
            target=GATE_7_TARGET,
            by_status=stats["by_status"],
        )
        sources["design_partners"] = "design_partner_tracker.stats()"
    except Exception as e:
        gates["gate_7_design_partners"] = _gate(
            7, "gate_7_design_partners", ">=4 design-partner convos",
            "≥4 design-partner conversations",
            None, None, "design_partner_tracker.stats() — FAILED", error=_safe_err(e))
        sources["design_partners"] = f"error: {_safe_err(e)}"

    # ── Summary ────────────────────────────────────────────────────────────
    # R-F2375: an unmeasurable gate is EXCLUDED from the tally — never silently
    # counted as a failure, nor as a pass. all_pass is over MEASURABLE gates.
    measurable = [g for g in gates.values() if g.get("pass") is not None]
    passed = sum(1 for g in measurable if g.get("pass"))
    unmeasurable = len(gates) - len(measurable)
    return {
        "gates": gates,
        "summary": {
            "passed": passed,
            "measurable": len(measurable),
            "unmeasurable": unmeasurable,
            "total": len(gates),
            # R-F2639: `all_pass` means PHASE A IS EXITABLE, so it must require
            # every gate measured AND passing. The prior form (passed ==
            # len(measurable)) read True whenever the measurable subset passed —
            # so with a dead store, gates 1/2/3/6 go unmeasurable and a handful
            # of survivors could report all_pass=true on the PUBLIC endpoint.
            # The field name is what a reader acts on; "all the ones we managed
            # to measure passed" is not "all pass".
            "all_pass": unmeasurable == 0 and passed == len(gates),
            "all_measurable_pass": len(measurable) > 0 and passed == len(measurable),
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
        "sources_consulted": sources,
        "note": "R-F1643: every gate value reads from a live probe. Editing markdown "
                "does NOT change these values. R-F2639: ONE measure — /phase/gates and "
                "/api/aria/phase/gates render this same computation.",
    }


def to_status(passed: bool | None) -> str:
    """Render tri-state ``pass`` into the legacy open/closed/unknown vocabulary.

    None → "unknown" preserves the could-not-measure distinction; collapsing it
    to "open" would report a failure that was never measured.
    """
    if passed is None:
        return "unknown"
    return "closed" if passed else "open"
