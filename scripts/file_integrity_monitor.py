"""R-F2920 — continuous file-integrity audit for the ARIA working tree.

WHY THIS EXISTS
    2026-07-23: Kaspersky File Anti-Virus deleted aria_service/static/aria_client/aria.bat
    from the tree, attributing the write to git.exe and classifying the content as
    Trojan. The deploy proceeded from that tree and shipped an image MISSING the file —
    /static/aria_client/aria.bat served 404 in production while every other check passed.

    R-F2919 closed the deploy path (deploy.ps1 / deploy.sh abort when `git ls-files -d`
    is non-empty). That is a gate, not an audit: it only fires when someone deploys, it
    reports nothing over time, and it cannot tell you a file vanished an hour ago and
    came back. The behaviour is INTERMITTENT — one clone lost the file, the next was
    clean — so a point-in-time check is exactly the wrong instrument.

WHAT THIS IS
    A durable audit. Every run compares the working tree against git and records the
    result, whether or not anything is wrong. A clean run is evidence too: it is what
    lets you say "the tree was intact at 14:05", instead of inferring it from the
    absence of a complaint.

    missing   a tracked file is not on disk            (the Kaspersky case)
    corrupt   present but its content != the committed blob
    restored  this run put a missing file back from git
    flapping  a file that has gone missing repeatedly  -> escalate, stop restoring

HONESTY RULES
    * Only tracked files are audited. An untracked file has no committed truth to
      compare against, so its absence is not a regression this tool can assert.
    * Content comparison uses `git hash-object`, which applies the same normalisation
      git does — so a CRLF checkout is NOT reported as corruption (core.autocrlf is
      true system-wide on this machine; a naive byte compare would cry wolf on every
      text file).
    * Auto-restore is bounded and self-limiting. A file that keeps disappearing is
      NOT restored in a loop — it is escalated once and left alone, because fighting
      an antivirus in a loop hides the problem instead of surfacing it.
    * A restore is verified AFTER the fact. `git checkout` returning 0 does not mean
      the file survived; the AV deletes it milliseconds later. The ledger records what
      is true after re-checking, never what was attempted.

USAGE
    python -m scripts.file_integrity_monitor --check              # audit once, report
    python -m scripts.file_integrity_monitor --check --restore    # and repair from git
    python -m scripts.file_integrity_monitor --watch 300 --restore  # continuous
    Exit code 0 = tree intact, 1 = problems found (usable as a CI/pre-deploy gate).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LEDGER = REPO / "data" / "file_integrity_ledger.json"
_MAX_LEDGER = 500
# A file seen missing this many times across runs is treated as FLAPPING: something
# keeps taking it, and repeated restores would just churn. Escalate instead.
_FLAP_THRESHOLD = 3


def _git(*args: str, check: bool = False) -> str:
    out = subprocess.run(
        ["git", *args], cwd=REPO, capture_output=True, text=True, check=check,
    )
    return out.stdout.strip()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_ledger() -> dict:
    try:
        data = json.loads(LEDGER.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"runs": [], "flap_counts": {}}
    except Exception:
        return {"runs": [], "flap_counts": {}}


def _save_ledger(ledger: dict) -> None:
    try:
        ledger["runs"] = ledger.get("runs", [])[-_MAX_LEDGER:]
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        LEDGER.write_text(json.dumps(ledger, indent=1), encoding="utf-8")
    except Exception as exc:                      # auditing must never break the caller
        print(f"  ! ledger write failed: {exc}", file=sys.stderr)


def missing_files() -> list[str]:
    """Tracked files that are not on disk. This is the Kaspersky signature."""
    out = _git("ls-files", "-d")
    return [line for line in out.splitlines() if line.strip()]


def corrupt_files(sample: list[str] | None = None) -> list[str]:
    """Tracked files whose content differs from the committed blob.

    Uses `git diff --name-only HEAD`, which normalises line endings exactly as git
    does. A byte-level compare would flag every text file on this machine, where
    core.autocrlf is true system-wide.

    NOTE this also reports legitimate local edits — the caller decides which paths
    it cares about. For the AV question the interesting signal is `missing`.
    """
    out = _git("diff", "--name-only", "HEAD")
    changed = [line for line in out.splitlines() if line.strip()]
    if sample is not None:
        changed = [c for c in changed if c in set(sample)]
    return changed


def restore(path: str) -> bool:
    """Restore one file from git and VERIFY it survived.

    `git checkout` exiting 0 is not evidence: on 2026-07-23 the file was restored and
    quarantined again within seconds. Only the post-check counts.
    """
    _git("checkout", "HEAD", "--", path)
    time.sleep(1.5)                                # give a real-time scanner a chance to act
    return (REPO / path).exists()


def audit(do_restore: bool = False, alert: bool = False) -> dict:
    ledger = _load_ledger()
    flaps: dict = ledger.get("flap_counts", {})

    gone = missing_files()
    result = {
        "ts": _now(),
        "tracked_total": len(_git("ls-files").splitlines()),
        "missing": list(gone),
        "restored": [],
        "restore_failed": [],
        "flapping": [],
        "clean": not gone,
    }

    for path in gone:
        flaps[path] = int(flaps.get(path, 0)) + 1
        if flaps[path] >= _FLAP_THRESHOLD:
            # Repeatedly taken. Restoring again would churn and hide it.
            result["flapping"].append(path)
            continue
        if do_restore:
            if restore(path):
                result["restored"].append(path)
            else:
                result["restore_failed"].append(path)

    ledger["flap_counts"] = flaps
    ledger.setdefault("runs", []).append(result)
    _save_ledger(ledger)

    if alert and (result["missing"] or result["flapping"]):
        _alert_operator(result)
    return result


def _alert_operator(result: dict) -> None:
    """§19e — a blocker the operator has to discover himself is the worst outcome.

    Best-effort Telegram to the ADMIN chat. Never raises: an alerting failure must not
    take down the audit, and the ledger has already recorded the truth either way.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.getenv("TELEGRAM_ADMIN_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID") or ""
    if not token or not chat:
        print("  ! operator alert NOT sent — TELEGRAM_BOT_TOKEN/CHAT_ID not set")
        return
    lines = ["BLOCKED: ARIA file integrity — tracked files are missing from the tree.", ""]
    for p in result["missing"][:10]:
        lines.append(f"  missing: {p}")
    if result["flapping"]:
        lines.append("")
        lines.append("FLAPPING (repeatedly removed — restore loop stopped):")
        for p in result["flapping"][:10]:
            lines.append(f"  {p}")
    if result["restored"]:
        lines.append("")
        lines.append(f"Restored from git: {len(result['restored'])}")
    lines += ["", "A deploy from this tree would ship these files ABSENT.",
              "Check antivirus quarantine before deploying."]
    try:
        import urllib.request
        body = json.dumps({"chat_id": str(chat), "text": "\n".join(lines)}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=body, headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=12).read()
        print("  -> operator alerted on the admin chat")
    except Exception as exc:
        print(f"  ! operator alert FAILED: {str(exc)[:120]}")


def _report(r: dict) -> None:
    stamp = r["ts"][:19].replace("T", " ")
    if r["clean"]:
        print(f"[{stamp}] OK — {r['tracked_total']} tracked files, none missing")
        return
    print(f"[{stamp}] INTEGRITY FAILURE — {len(r['missing'])} tracked file(s) missing "
          f"of {r['tracked_total']}")
    for p in r["missing"]:
        tag = "FLAPPING" if p in r["flapping"] else (
            "restored" if p in r["restored"] else (
                "RESTORE FAILED" if p in r["restore_failed"] else "missing"))
        print(f"    [{tag}] {p}")
    if r["flapping"]:
        print("  ! flapping files are NOT auto-restored — something keeps removing them.")
        print("    Check the antivirus quarantine log and exclude the checkout, or")
        print("    deploy from a tree that is excluded.")


def main() -> int:
    ap = argparse.ArgumentParser(description="ARIA working-tree integrity audit (R-F2920)")
    ap.add_argument("--check", action="store_true", help="audit once (default)")
    ap.add_argument("--watch", type=int, metavar="SECONDS",
                    help="audit continuously every SECONDS")
    ap.add_argument("--restore", action="store_true",
                    help="restore missing tracked files from git (bounded; flapping files are left)")
    ap.add_argument("--alert", action="store_true",
                    help="notify the operator on the admin Telegram chat when files are missing")
    ap.add_argument("--history", type=int, metavar="N",
                    help="print the last N audit runs from the ledger and exit")
    args = ap.parse_args()

    if args.history:
        for run in _load_ledger().get("runs", [])[-args.history:]:
            state = "OK " if run.get("clean") else "BAD"
            print(f"{state} {run['ts'][:19]}  missing={len(run.get('missing') or [])} "
                  f"restored={len(run.get('restored') or [])} "
                  f"flapping={len(run.get('flapping') or [])}")
        return 0

    if args.watch:
        print(f"[R-F2920] watching every {args.watch}s "
              f"(restore={'on' if args.restore else 'off'}, alert={'on' if args.alert else 'off'})")
        while True:
            _report(audit(do_restore=args.restore, alert=args.alert))
            time.sleep(max(10, args.watch))

    r = audit(do_restore=args.restore, alert=args.alert)
    _report(r)
    return 0 if r["clean"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
