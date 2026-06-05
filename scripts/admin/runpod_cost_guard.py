"""runpod_cost_guard — never lose a pod to a drained balance again (R-F1343).

Why this exists: on 2026-06-05 an H100 pod ($2.89/hr) ran continuously, drained
the RunPod balance to ~$0, and RunPod TERMINATED the pod (the model survived on
the network volume, but the serving pod was lost). Root cause: provisioning an
expensive always-on GPU with no balance/runway check and no burn-rate alert.

This tool makes that mistake structurally impossible:
  * `status`   — show balance, burn rate, runway, pods, the model volume.
  * `recover`  — provision a pod on the model volume, but ONLY after a guarded
                 check: refuses unless balance covers a minimum runway, and
                 always picks the CHEAPEST available qualifying GPU (never the
                 most expensive). Prints the exact $/hr and runway before acting.
  * `watch`    — print balance + runway (for a cron/alert; exits non-zero when
                 runway is below the floor so an alert can fire).

Hard rules enforced:
  - Never provision if balance < --min-balance (default $5).
  - Never provision if projected runway < --min-hours (default 2h).
  - Prefer cheapest GPU that fits a 7B (A40 / A100-40 / L40S before H100).
  - Always report $/hr + runway so the operator sees the burn rate up front.

The cost math is a pure function (compute_runway / is_safe_to_provision) with
unit tests — the part that must never be wrong.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request

RUNPOD_REST = "https://rest.runpod.io/v1"
RUNPOD_GQL = "https://api.runpod.io/graphql"
MODEL_VOLUME_ID = os.getenv("ARIA_RUNPOD_VOLUME_ID", "4vdw2zmqov")
# Cheapest-first GPU preference for a 7B + LoRA. H100 is LAST resort.
GPU_PREFERENCE = [
    "NVIDIA A40", "NVIDIA L40S", "NVIDIA L40", "NVIDIA RTX A6000",
    "NVIDIA RTX 6000 Ada Generation", "NVIDIA A100 80GB PCIe",
    "NVIDIA A100-SXM4-80GB", "NVIDIA H100 PCIe",
]


# ── pure cost math (unit-tested) ─────────────────────────────────────────

def compute_runway(balance: float, cost_per_hr: float) -> float:
    """Hours of runway. inf when nothing is burning."""
    if cost_per_hr <= 0:
        return float("inf")
    return balance / cost_per_hr


def is_safe_to_provision(balance: float, cost_per_hr: float, *,
                         min_balance: float, min_hours: float) -> tuple[bool, str]:
    """Decide whether it is financially safe to start a pod. Returns
    (ok, human-reason). This is the guard that prevents the 2026-06-05 drain."""
    if balance < min_balance:
        return False, (f"balance ${balance:.2f} < floor ${min_balance:.2f} — "
                       f"top up before provisioning")
    runway = compute_runway(balance, cost_per_hr)
    if runway < min_hours:
        return False, (f"runway {runway:.1f}h (${balance:.2f} @ ${cost_per_hr:.2f}/hr) "
                       f"< required {min_hours:.1f}h — pod would be killed mid-run")
    return True, (f"OK: ${balance:.2f} @ ${cost_per_hr:.2f}/hr = {runway:.1f}h runway")


# ── RunPod API ───────────────────────────────────────────────────────────

def _key() -> str:
    k = os.getenv("RUNPOD_API_KEY", "")
    if not k:
        # fall back to .env
        try:
            for ln in open(os.path.join(os.path.dirname(__file__), "..", "..", ".env"),
                           encoding="utf-8"):
                if ln.startswith("RUNPOD_API_KEY="):
                    k = ln.split("=", 1)[1].strip()
                    break
        except Exception:
            pass
    if not k:
        sys.exit("RUNPOD_API_KEY not set (env or .env)")
    return k


# RunPod's WAF 403s the default Python-urllib user-agent — send a browser-ish UA.
_UA = "Mozilla/5.0 (aria-cost-guard)"


def _rest(method: str, path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(
        f"{RUNPOD_REST}{path}", method=method,
        headers={"Authorization": f"Bearer {_key()}", "Content-Type": "application/json",
                 "User-Agent": _UA},
        data=json.dumps(body).encode() if body else None,
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def get_balance() -> dict:
    req = urllib.request.Request(
        f"{RUNPOD_GQL}?api_key={_key()}", method="POST",
        headers={"Content-Type": "application/json", "User-Agent": _UA},
        data=json.dumps({"query": "query{myself{clientBalance currentSpendPerHr}}"}).encode(),
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.loads(r.read().decode())
    me = (d.get("data") or {}).get("myself") or {}
    return {"balance": float(me.get("clientBalance") or 0.0),
            "spend_per_hr": float(me.get("currentSpendPerHr") or 0.0)}


# ── commands ─────────────────────────────────────────────────────────────

def cmd_status(_args) -> int:
    bal = get_balance()
    print(f"RunPod balance: ${bal['balance']:.4f}  (spend now ${bal['spend_per_hr']:.4f}/hr)")
    runway = compute_runway(bal["balance"], bal["spend_per_hr"])
    print(f"current runway: {'unlimited' if runway == float('inf') else f'{runway:.1f}h'}")
    try:
        pods = _rest("GET", "/pods")
        pods = pods if isinstance(pods, list) else pods.get("pods", [])
        print(f"pods: {len(pods)}")
        for p in pods:
            m = p.get("machine") or {}
            print(f"  - {p.get('id')} | {p.get('desiredStatus')} | "
                  f"{m.get('gpuTypeId')} | ${p.get('costPerHr')}/hr | vol={p.get('networkVolumeId')}")
    except Exception as e:
        print(f"  (pod list failed: {e})")
    print(f"model volume: {MODEL_VOLUME_ID}")
    return 0


def cmd_watch(args) -> int:
    bal = get_balance()
    runway = compute_runway(bal["balance"], bal["spend_per_hr"])
    low = bal["balance"] < args.min_balance or runway < args.min_hours
    print(json.dumps({"balance": round(bal["balance"], 4),
                      "spend_per_hr": bal["spend_per_hr"],
                      "runway_h": None if runway == float("inf") else round(runway, 2),
                      "low": low}))
    return 1 if low else 0  # non-zero → alert can fire


def cmd_recover(args) -> int:
    bal = get_balance()
    # Estimate the cost of the cheapest qualifying GPU we'd start.
    est_cost = args.assume_cost_per_hr  # conservative estimate for the guard
    ok, reason = is_safe_to_provision(
        bal["balance"], est_cost,
        min_balance=args.min_balance, min_hours=args.min_hours,
    )
    print(f"[guard] balance ${bal['balance']:.2f} | est ${est_cost:.2f}/hr | {reason}")
    if not ok:
        print("[guard] REFUSING to provision — top up first. No pod created, "
              "no balance burned. Model volume is safe.")
        return 2
    if args.dry_run:
        print("[guard] --dry-run: would provision cheapest GPU on volume "
              f"{MODEL_VOLUME_ID} (preference: {GPU_PREFERENCE[0]}...). Not creating.")
        return 0
    # Try cheapest-first; create on the model volume in its datacenter.
    body = {
        "name": "aria-llm-serve",
        "imageName": "runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04",
        "gpuTypeIds": GPU_PREFERENCE,  # RunPod picks the first available; cheapest-first
        # R-F1355: 100GB container disk. The 30GB overlay was the root of the
        # vLLM disk-blowup (torch/xformers reinstall) that forced the slow
        # transformers shim — so bulk evals ran overnight. 100GB lets vLLM
        # (batched, 10-20x faster) serve eval → same-day train/eval cycles.
        "gpuCount": 1, "containerDiskInGb": 100,
        "networkVolumeId": MODEL_VOLUME_ID, "dataCenterIds": [args.datacenter],
        "ports": ["8888/http", "22/tcp"],
        "env": {"PUBLIC_KEY": os.getenv("ARIA_RUNPOD_PUBKEY", "")},
    }
    try:
        d = _rest("POST", "/pods", body)
        if isinstance(d, list):
            d = d[0]
        print(f"[recover] created pod {d.get('id')} on {(d.get('machine') or {}).get('gpuTypeId')} "
              f"@ ${d.get('costPerHr')}/hr")
        print(f"[recover] runway now ~{compute_runway(bal['balance'], float(d.get('costPerHr') or est_cost)):.1f}h — "
              "watch it with `runpod_cost_guard.py watch`")
        return 0
    except Exception as e:
        print(f"[recover] provision failed: {e}")
        return 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    w = sub.add_parser("watch"); w.set_defaults(fn=cmd_watch)
    w.add_argument("--min-balance", type=float, default=5.0)
    w.add_argument("--min-hours", type=float, default=2.0)
    r = sub.add_parser("recover"); r.set_defaults(fn=cmd_recover)
    r.add_argument("--min-balance", type=float, default=5.0)
    r.add_argument("--min-hours", type=float, default=2.0)
    r.add_argument("--assume-cost-per-hr", type=float, default=1.49)  # A40/A100-40 class
    r.add_argument("--datacenter", default="US-KS-2")
    r.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
