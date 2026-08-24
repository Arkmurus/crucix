"""R-F2443 — on-pod EXEC de-risk (SSH exec + minimal sft_train adapter-emit).

The last primitive to prove before an unattended paid cycle can be trusted. The
pod lifecycle (create/terminate) is proven (R-F2442); this proves the TRAINING-
EXECUTION path the same bounded, always-terminate way:

  create pod -> wait SSH -> nvidia-smi (GPU reachable) -> scp sft_train.py + a
  tiny SFT file -> run sft_train.py on a CHEAP tiny base (Qwen2.5-Coder-0.5B) for
  1 epoch on ~10 rows -> confirm an ADAPTER is emitted -> TERMINATE + confirm gone
  -> report actual cost.

This is a SMOKE (prove the mechanics), NOT the real cycle. Cheap: A40 (~$0.44/hr),
tiny base (~1GB), a couple of minutes -> well under $1. Always-terminate via the
R-F2442 finally + retry-until-gone; a durable pod-id record survives a crash.

Usage:  python scripts/train/runpod_exec_smoke.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "scripts" / "train"))
import runpod_derisk as RD  # reuse _key/_req/terminate/_record_write/_record_clear

_SSH_KEY = Path.home() / ".ssh" / "runpod_aria"
_PUB_KEY = Path.home() / ".ssh" / "runpod_aria.pub"
_STATUS = _REPO / "data" / "eval_reports" / "runpod_exec_smoke_report.json"
_MINI = _REPO / "data" / "training" / "_mini_sft_smoke.jsonl"
_SFT = _REPO / "scripts" / "train" / "sft_train.py"
_HARD_DEADLINE_S = 20 * 60          # absolute ceiling; pod force-killed past this
_TINY_BASE = "Qwen/Qwen2.5-Coder-0.5B-Instruct"

_SSH_OPTS = ["-i", str(_SSH_KEY), "-o", "StrictHostKeyChecking=no",
             "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
             "-o", "ConnectTimeout=10"]


def _make_mini() -> int:
    """10 smallest real SFT pairs (fast smoke), else a synthetic fallback."""
    src = _REPO / "data" / "training" / "code_sft_v1.jsonl"
    rows = []
    if src.exists():
        rows = [json.loads(l) for l in src.read_text(encoding="utf-8").splitlines() if l.strip()]
        rows.sort(key=lambda r: len(r["input"]) + len(r["output"]))
        rows = rows[:10]
    if not rows:
        rows = [{"input": f"Fix: return {i}", "output": f"### FIXED 1\n```python\nreturn {i}\n```"}
                for i in range(10)]
    _MINI.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8", newline="\n")
    return len(rows)


def _ssh(host: str, port: str, cmd: str, timeout: int) -> tuple[int, str]:
    p = subprocess.run(["ssh", *_SSH_OPTS, "-p", port, f"root@{host}", cmd],
                       capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _scp(host: str, port: str, local: Path, remote: str, timeout: int = 120) -> bool:
    p = subprocess.run(["scp", *_SSH_OPTS, "-P", port, str(local), f"root@{host}:{remote}"],
                       capture_output=True, text=True, timeout=timeout)
    return p.returncode == 0


def main() -> None:
    key = RD._key()
    if not _SSH_KEY.exists() or not _PUB_KEY.exists():
        raise SystemExit(f"BLOCKED: missing SSH keypair {_SSH_KEY}")
    pub = _PUB_KEY.read_text(encoding="utf-8").strip()
    n_rows = _make_mini()

    report = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "created": False, "pod_id": None, "ssh_ready": False,
              "gpu_ok": False, "adapter_emitted": False, "terminated": False,
              "cost_per_hr": None, "elapsed_s": None, "cost_usd": None,
              "mini_rows": n_rows, "base": _TINY_BASE, "error": None}
    t0 = time.time()
    pod_id = None
    try:
        body = {"name": "aria-exec-smoke",
                "imageName": "runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04",
                "gpuTypeIds": RD._CHEAP_GPUS, "gpuCount": 1, "cloudType": "SECURE",
                "containerDiskInGb": 40, "ports": ["22/tcp"], "env": {"PUBLIC_KEY": pub}}
        code, data = RD._req("POST", "/pods", key, body)
        pod_id = data.get("id")
        if not pod_id:
            report["error"] = f"create failed http {code}: {json.dumps(data)[:300]}"
            print("BLOCKED:", report["error"]); return
        RD._record_write(pod_id)
        report.update(created=True, pod_id=pod_id, cost_per_hr=data.get("costPerHr"))
        print(f"created pod {pod_id}")

        # wait for RUNNING + publicIp + portMappings['22']
        host = port = None
        while time.time() - t0 < _HARD_DEADLINE_S:
            code, d = RD._req("GET", f"/pods/{pod_id}", key)
            st = str(d.get("desiredStatus") or "").upper()
            host = d.get("publicIp")
            pm = d.get("portMappings") or {}
            port = str(pm.get("22") or "") if pm else ""
            report["cost_per_hr"] = d.get("costPerHr") or report["cost_per_hr"]
            print(f"  status={st} host={host} port={port} (t+{int(time.time()-t0)}s)")
            if st == "RUNNING" and host and port:
                break
            time.sleep(10)
        if not (host and port):
            report["error"] = "pod never exposed SSH (host/port)"; return

        # wait for sshd
        for _ in range(24):  # ~4 min
            rc, out = 1, ""
            try:
                rc, out = _ssh(host, port, "echo ready", timeout=15)
            except subprocess.TimeoutExpired:
                pass
            if rc == 0 and "ready" in out:
                report["ssh_ready"] = True
                break
            time.sleep(10)
        if not report["ssh_ready"]:
            report["error"] = "SSH never became ready"; return
        print("SSH ready")

        # ship the trainer + mini dataset
        if not (_scp(host, port, _SFT, "/workspace/sft_train.py")
                and _scp(host, port, _MINI, "/workspace/mini.jsonl")):
            report["error"] = "scp failed"; return

        # nvidia-smi
        rc, out = _ssh(host, port, "nvidia-smi -L", timeout=30)
        report["gpu_ok"] = rc == 0 and "GPU" in out
        print("nvidia-smi:", out.strip()[:200])

        # minimal SFT smoke (pin trl to the API sft_train.py targets)
        remote = (
            "set -o pipefail; "
            "pip install -q --disable-pip-version-check 'trl==0.12.2' 'transformers>=4.46' "
            "peft datasets accelerate 2>&1 | tail -2; "
            f"python /workspace/sft_train.py --base-model {_TINY_BASE} "
            "--train-file /workspace/mini.jsonl --output-dir /workspace/smoke_adapter "
            "--epochs 1 --lora-rank 8 --lora-alpha 16 --max-seq-len 1024 --batch-size 1 2>&1 | tail -15; "
            "test -f /workspace/smoke_adapter/adapter_config.json && echo ADAPTER_EMITTED=YES || echo ADAPTER_EMITTED=NO; "
            "ls -la /workspace/smoke_adapter 2>/dev/null | tail -6")
        rc, out = _ssh(host, port, remote, timeout=15 * 60)
        report["adapter_emitted"] = "ADAPTER_EMITTED=YES" in out
        report["train_tail"] = out[-1500:]
        print("=== on-pod train tail ===\n", out[-1500:])
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        print("ERROR:", report["error"])
    finally:
        if pod_id:
            report["terminated"] = RD.terminate(pod_id, key)
            if report["terminated"]:
                RD._record_clear()
        report["elapsed_s"] = round(time.time() - t0, 1)
        if report["cost_per_hr"]:
            report["cost_usd"] = round(report["elapsed_s"] / 3600 * float(report["cost_per_hr"]), 4)
        _STATUS.parent.mkdir(parents=True, exist_ok=True)
        _STATUS.write_text(json.dumps(report, indent=2), encoding="utf-8", newline="\n")
        print("\n=== EXEC-SMOKE REPORT ===")
        print(json.dumps({k: v for k, v in report.items() if k != "train_tail"}, indent=2))
        ok = report["created"] and report["gpu_ok"] and report["adapter_emitted"] and report["terminated"]
        print("VERDICT:", "PASS — on-pod SSH exec + sft_train adapter-emit + terminate PROVEN."
              if ok else "FAIL — see error/train_tail; do not run unattended cycle until this passes.")
        if not report["terminated"]:
            print("!!! MANUAL CLEANUP: python scripts/train/runpod_derisk.py --cleanup")


if __name__ == "__main__":
    main()
