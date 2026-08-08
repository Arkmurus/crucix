#!/usr/bin/env python3
"""R-F3775 — turn the §21a wiring debt into capability Gaps, per §21e.

WHY THIS IS A SCRIPT AND NOT A HAND-FIX.

`scripts/ci/wiring_audit.py` reports 66 modules that do not reach a brain sink on
both branches. §21a says such a module is DARK: a failure inside it is invisible to
the brain, so it cannot trigger self-heal. That is the one item in the remaining
backlog that genuinely limits ARIA rather than merely annoying a reviewer.

§21e is binding about the route: a finding the coder can implement MUST become a
Gap, not a TODO and not sixty-six hand-written commits. Each of these is a
mechanical, local, single-file edit — add the missing `wire_failure`/`@fail_wire`
branch using the convention already present in the module — which is exactly the
shape `self_coder.fix_gap` consumes.

WHY IT POSTS OVER HTTP RATHER THAN CALLING record_gap DIRECTLY.

`capability_gaps.record_gap` writes through the state store. A FRESH process cannot
reach it — repeatedly proven during the 2026-08 sweep: a side process gets an
unconfigured store and the write lands nowhere, silently. Only the RUNNING app holds
a live handle. So this posts to the app's own endpoint and treats the HTTP response
as the evidence the gap was recorded. `--dry-run` needs neither.

USAGE (run from inside the machine, where the internal token already lives in env —
never copy the token out, cf. §18):

    flyctl ssh console -a aria-intel -C "python /app/scripts/admin/record_wiring_gaps.py"
    python scripts/admin/record_wiring_gaps.py --dry-run          # local, no store
    python scripts/admin/record_wiring_gaps.py --subsystem all    # all 66

Exit: 0 = every selected gap recorded · 1 = one or more failed · 2 = refused to run.
A partial success exits 1 and NAMES the failures; it never reports a clean sweep it
did not achieve.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
BASELINE = REPO_ROOT / "docs" / "wiring_audit_baseline.json"

#: R-F3775 — taken from the ENUM, never typed as a literal.
#:
#: My first draft hardcoded "MODULE_BUG". The real value is `"module_bug"`
#: (gap_detector.py:70, `class GapType(str)`), so all 16 gaps would have carried an
#: unrecognised type — recorded, visible, and never dispatched by the coder. The
#: capability test caught it only because it asserted against the vocabulary rather
#: than against my own string. Import the enum and the drift cannot happen.
#:
#: MODULE_BUG is also the right ROUTE, which is a separate question from the right
#: spelling. AUTONOMY_LEVEL (gap_detector.py:88) maps it to
#: (auto_fixable=True, requires_wa_approval=False, requires_hard_gate=False) — so
#: these drain through the normal staged pipeline. MISSING_CAPABILITY would be
#: defensible on the wording ("something ARIA lacks") but is
#: (False, True, False): it would put SIXTEEN approval prompts in front of the
#: operator for what is a mechanical one-line edit each. §21a calls a dark module a
#: violation of a binding rule — that is a defect in existing code, not a feature
#: request.
def _gap_type() -> str:
    try:
        from aria_service.autonomous.gap_detector import GapType
        return GapType.MODULE_BUG
    except Exception:
        return "module_bug"      # the literal at gap_detector.py:70, as a last resort

#: Default selection. These two packages ARE ARIA's cognition — the metacognitive
#: cycle and the learning loops. A dark failure here is the case §21 is actually
#: about, so they go first rather than the whole 66 at once.
DEFAULT_SUBSYSTEMS = ("metacognitive", "learning")

_WHAT = {
    "no-wiring": "reaches NO brain sink at all",
    "missing-failure": "has a success wire but NO failure wire",
    "missing-success": "has a failure wire but NO success wire",
}


def build_payloads(dark: dict, subsystems: tuple[str, ...]) -> list[dict]:
    """Gap payloads for the selected dark modules, sorted for a stable run.

    Kept separate from the POST so it is testable without a store or a network:
    the payload SHAPE is the part that can be wrong in a way nobody notices until
    the coder receives sixty useless gaps.
    """
    selected = [
        (p, v) for p, v in dark.items()
        if "all" in subsystems or any(f"/{s}/" in p for s in subsystems)
    ]
    out = []
    for path, verdict in sorted(selected):
        module = path.split("/")[-1].removesuffix(".py")
        detail = (
            f"§21a wiring gap in {path}: {_WHAT.get(verdict, verdict)}. "
            f"A failure here is invisible to the brain and therefore cannot trigger "
            f"self-heal, which is the condition §21a exists to forbid. Fix: add the "
            f"missing branch using the convention already used in this module — see "
            f"nearby @fail_wire / wire_success / wire_failure calls, and prefer "
            f"@wired or @fail_wire (they cover the failure branch without "
            f"restructuring the function). Reuse the module's existing gap_type "
            f"rather than inventing one. Do NOT satisfy this by logging locally: a "
            f"local log is DARK by §21a's definition. "
            f"REPRODUCE TEST (this is the part that decides whether the fix reaches "
            f"gold): the fault is NOT an exception, so there is no traceback to "
            f"replay — do not look for one. It is reproduced by the WIRING AUDIT, "
            f"which already goes fail-on-unfixed -> pass-on-fixed exactly as the "
            f"R-F1681/R-F1685 gold gate requires: "
            f"`python scripts/ci/wiring_audit.py` reports {path} while it is dark "
            f"and stops reporting it once both branches reach a sink. Assert THAT "
            f"transition. R-F1857 rejects a MODULE_BUG with no reproducible fault "
            f"because it can never become gold and only burns budget; this one has "
            f"a real, cheap, deterministic reproducer, so use it. "
            f"Verdict from wiring_harness.run_all_gates: {verdict}."
        )
        out.append({
            "gap_type": _gap_type(),
            "detail": detail,
            "message_context": f"R-F3775 §21a wiring debt sweep ({verdict})",
            "source": f"wiring_audit:{module}",
        })
    return out


def _post(base: str, token: str, payload: dict, timeout: float = 30.0) -> tuple[bool, str]:
    req = urllib.request.Request(
        f"{base.rstrip('/')}/api/aria/capability-gaps",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {token}"} if token else {})},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read(400).decode("utf-8", "replace")
            return (200 <= r.status < 300), f"HTTP {r.status} {body[:180]}"
    except urllib.error.HTTPError as e:                     # 4xx/5xx carry a body
        return False, f"HTTP {e.code} {e.read(300).decode('utf-8', 'replace')[:180]}"
    except Exception as e:                                  # network / DNS / timeout
        return False, f"{type(e).__name__}: {e}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--subsystem", default=",".join(DEFAULT_SUBSYSTEMS),
                    help="comma-separated package names, or 'all' (default: "
                         "metacognitive,learning)")
    ap.add_argument("--base", default=os.environ.get("ARIA_SELF_BASE",
                                                    "http://127.0.0.1:8000"))
    ap.add_argument("--dry-run", action="store_true",
                    help="print the payloads; touch neither store nor network")
    ap.add_argument("--limit", type=int, default=0, help="cap the batch (0 = no cap)")
    args = ap.parse_args(argv)

    if not BASELINE.exists():
        print(f"REFUSED: no wiring baseline at {BASELINE} — run "
              f"scripts/ci/wiring_audit.py first", file=sys.stderr)
        return 2
    try:
        dark = json.loads(BASELINE.read_text(encoding="utf-8")).get("known_dark") or {}
    except Exception as e:
        # An unreadable baseline must never read as "nothing is dark" — that is the
        # absence-as-measurement defect this whole sweep was about.
        print(f"REFUSED: baseline unreadable ({e}) — absent is not empty",
              file=sys.stderr)
        return 2

    subsystems = tuple(s.strip() for s in args.subsystem.split(",") if s.strip())
    payloads = build_payloads(dark, subsystems)
    if args.limit > 0 and len(payloads) > args.limit:
        # §21e "no silent caps" — say what was dropped, or a partial run reads as full.
        print(f"NOTE: capping {len(payloads)} -> {args.limit}; "
              f"{len(payloads) - args.limit} NOT recorded this run")
        payloads = payloads[:args.limit]

    print(f"selected {len(payloads)} dark module(s) from {len(dark)} "
          f"(subsystems: {','.join(subsystems)})")
    if not payloads:
        print("nothing to record")
        return 0

    if args.dry_run:
        for p in payloads:
            print(f"  [{p['source']}] {p['detail'][:110]}...")
        print(f"DRY-RUN: {len(payloads)} payload(s) built, nothing sent")
        return 0

    token = os.environ.get("ARIA_INTERNAL_TOKEN") or os.environ.get("ARIA_API_TOKEN") or ""
    if not token:
        print("WARNING: no ARIA_INTERNAL_TOKEN / ARIA_API_TOKEN in env — posting "
              "unauthenticated; expect 401 unless the route is open", file=sys.stderr)

    ok = failed = 0
    for i, p in enumerate(payloads, 1):
        good, msg = _post(args.base, token, p)
        if good:
            ok += 1
        else:
            failed += 1
            print(f"  FAILED[{i}] {p['source']}: {msg}", file=sys.stderr)
    print(f"RESULT total={len(payloads)} recorded={ok} failed={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":                                  # pragma: no cover
    raise SystemExit(main())
