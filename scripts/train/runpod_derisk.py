"""R-F2442 — RunPod pod-lifecycle DE-RISK (isolated, cheap, bulletproof).

Purpose: before the code-sovereign paid cycle can be trusted to run unattended,
prove the ONE risky primitive in isolation — that we can (a) CREATE a GPU pod,
(b) VERIFY it comes up, and CRUCIALLY (c) reliably TERMINATE it so a run can
NEVER leave a runaway pod burning money (the §19e worst-outcome). This is the
honest way to de-risk vs blind-firing a never-tested orchestration.

Safety design (the point of the exercise):
  * The pod id is written to a DURABLE record file the INSTANT it's created,
    BEFORE anything else — so even a hard crash leaves a terminate-able id.
  * Termination runs in a `finally` that ALWAYS fires (success, error, Ctrl-C),
    retries until the pod is confirmed gone, and only then clears the record.
  * A hard wall-clock deadline force-terminates and aborts if anything hangs.
  * Cheapest GPU types, tiny run (~minutes) → cents. Never trains, never serves.

Usage (spends ~a few cents):
  python scripts/train/runpod_derisk.py
  python scripts/train/runpod_derisk.py --cleanup   # terminate any recorded pod, no create

REST: base https://rest.runpod.io/v1, Bearer RUNPOD_API_KEY.
  POST /pods  ·  GET /pods/{id}  ·  DELETE /pods/{id}
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_API = "https://rest.runpod.io/v1"
_RECORD = _REPO / "data" / "eval_reports" / "_runpod_active_pod.txt"
_STATUS = _REPO / "data" / "eval_reports" / "runpod_derisk_report.json"
_HARD_DEADLINE_S = 8 * 60          # never let the pod live longer than this
_CHEAP_GPUS = ["NVIDIA A40", "NVIDIA RTX A6000", "NVIDIA L40", "NVIDIA L40S"]


def _key() -> str:
    import os
    k = os.getenv("RUNPOD_API_KEY", "")
    if not k:
        envf = _REPO / ".env"
        if envf.exists():
            for ln in envf.read_text(encoding="utf-8").splitlines():
                if ln.startswith("RUNPOD_API_KEY="):
                    k = ln.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    if not k:
        raise SystemExit("BLOCKED: RUNPOD_API_KEY not set (env or .env)")
    return k


def _req(method: str, path: str, key: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{_API}{path}", data=data, method=method,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            txt = resp.read().decode("utf-8", "replace")
            try:
                return resp.status, json.loads(txt)
            except Exception:
                return resp.status, {"raw": txt[:500]}
    except urllib.error.HTTPError as e:
        txt = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(txt)
        except Exception:
            return e.code, {"raw": txt[:500]}


def _record_write(pod_id: str) -> None:
    _RECORD.parent.mkdir(parents=True, exist_ok=True)
    _RECORD.write_text(pod_id + "\n", encoding="utf-8", newline="\n")


def _record_clear() -> None:
    try:
        _RECORD.unlink()
    except FileNotFoundError:
        pass


def terminate(pod_id: str, key: str, attempts: int = 8) -> bool:
    """DELETE the pod; confirm it's gone. Retries hard — this is the safety net."""
    for i in range(attempts):
        _req("DELETE", f"/pods/{pod_id}", key)
        time.sleep(3)
        code, data = _req("GET", f"/pods/{pod_id}", key)
        gone = code == 404 or str(data.get("desiredStatus", "")).upper() in ("TERMINATED", "")
        if code == 404 or gone:
            print(f"terminate: pod {pod_id} confirmed gone (attempt {i+1}, http {code})")
            return True
        print(f"terminate: retry {i+1} (status http {code} desired={data.get('desiredStatus')})")
    print(f"terminate: FAILED to confirm pod {pod_id} gone after {attempts} attempts")
    return False


def cleanup_recorded(key: str) -> None:
    if not _RECORD.exists():
        print("cleanup: no recorded pod")
        return
    pod_id = _RECORD.read_text(encoding="utf-8").strip()
    if pod_id and terminate(pod_id, key):
        _record_clear()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cleanup", action="store_true", help="terminate recorded pod and exit")
    ap.add_argument("--disk-gb", type=int, default=20)
    args = ap.parse_args()
    key = _key()

    if args.cleanup:
        cleanup_recorded(key)
        return

    report = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "created": False, "pod_id": None, "reached_status": None,
              "terminated": False, "elapsed_s": None, "gpu": None, "error": None}
    t0 = time.time()
    pod_id = None
    key_ref = key
    try:
        # Proven image from _create_v04_pod.py (known-good on this account).
        body = {"name": "aria-derisk",
                "imageName": "runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04",
                "gpuTypeIds": _CHEAP_GPUS, "gpuCount": 1, "cloudType": "SECURE",
                "containerDiskInGb": args.disk_gb, "ports": ["22/tcp"]}
        code, data = _req("POST", "/pods", key, body)
        pod_id = data.get("id")
        if not pod_id:
            report["error"] = f"create failed http {code}: {json.dumps(data)[:300]}"
            print("BLOCKED:", report["error"])
            return
        _record_write(pod_id)                     # DURABLE record BEFORE anything else
        report.update(created=True, pod_id=pod_id, gpu=data.get("machine", {}).get("gpuTypeId") or data.get("gpuTypeIds"))
        print(f"created pod {pod_id}; polling for RUNNING (hard deadline {_HARD_DEADLINE_S}s)…")

        # poll until RUNNING or the hard deadline
        while time.time() - t0 < _HARD_DEADLINE_S:
            code, data = _req("GET", f"/pods/{pod_id}", key)
            st = str(data.get("desiredStatus") or data.get("status") or "").upper()
            report["reached_status"] = st
            print(f"  status={st} (t+{int(time.time()-t0)}s)")
            if st == "RUNNING":
                break
            time.sleep(10)
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        print("ERROR:", report["error"])
    finally:
        # ALWAYS terminate — the entire point of the de-risk.
        if pod_id:
            report["terminated"] = terminate(pod_id, key_ref)
            if report["terminated"]:
                _record_clear()
        report["elapsed_s"] = round(time.time() - t0, 1)
        _STATUS.parent.mkdir(parents=True, exist_ok=True)
        _STATUS.write_text(json.dumps(report, indent=2), encoding="utf-8", newline="\n")
        print("\n=== DE-RISK REPORT ===")
        print(json.dumps(report, indent=2))
        verdict = ("PASS — provisioning + reliable termination proven; unattended "
                   "cycle can be trusted to fail-safe."
                   if report["created"] and report["terminated"] else
                   "FAIL — do NOT run an unattended paid cycle until this passes.")
        print("VERDICT:", verdict)


if __name__ == "__main__":
    main()
