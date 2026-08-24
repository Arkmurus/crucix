"""R-F4249 — launch the pre-registered alpha-band interpolation sweep, and exit.

LAUNCH-AND-EXIT, deliberately. R-F3420 established the pattern after a combined
driver was killed mid-run and its `trap stop_pod EXIT` never fired; the v1 and
v2 interpolation sweeps were then driven by hand, which is why R-F4197 had to
hand-write a recovery when one of them could not be collected. Nothing here
waits for the sweep: the pod works independently, a watchdog bounds it, and
`harvest_cycle.py` collects whenever.

NO TRAINING HAPPENS. This blends two existing LoRA adapters at three registered
weights and evaluates each blend on the unchanged 168-row held-out set. It
cannot damage the incumbent; it either finds a promotable blend or proves the
band is empty.

The alpha set is pinned in BOTH the manifest and the pod runner, and the runner
refuses an alpha set that differs from its registration. That is not ceremony:
choosing alphas after seeing results is how a sweep becomes a search for a
number that flatters the run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.train import pod_of_record as por  # noqa: E402

MANIFEST = ROOT / "data/eval_reports/aria_tooluse_lora_interpolation_v3_manifest.json"
STATE_FILE = ROOT / "data/eval_reports/.tooluse_lora_interpolation_v3_pod_state"
EVAL_LOCAL = ROOT / "data/training/split_v1/eval.jsonl"
POD_RUNNER = ROOT / "scripts/train/pod_tooluse_lora_interpolation_v3.sh"
WATCHDOG = ROOT / "scripts/train/pod_selfstop_watch_v04.sh"
SUPPORT = ("interpolate_lora_adapters.py", "eval_tooluse.py", "serve_eval_shim.py",
           "build_tooluse_corpus.py", "__init__.py")

SSH_KEY = pathlib.Path.home() / ".ssh/runpod_aria"
SSH_OPTIONS = ("-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
               "-o", "LogLevel=ERROR", "-o", "ConnectTimeout=20",
               "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=6")

# Three arms: ~40 min of evaluation each plus model loads, so ~2.5h with margin.
DEADLINE = 14400
GRACE = 900
COLLECT_GRACE = 900


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S', time.gmtime())}] [interp-v3] {message}",
          flush=True)


def ssh(host: str, port: str, command: str, *, timeout: int = 180):
    return subprocess.run(
        ["ssh", "-i", str(SSH_KEY), *SSH_OPTIONS, "-p", port, f"root@{host}", command],
        capture_output=True, text=True, timeout=timeout, check=False)


def scp(host: str, port: str, local: pathlib.Path, remote: str,
        *, attempts: int = 4, timeout: int = 900) -> bool:
    for _ in range(attempts):
        done = subprocess.run(
            ["scp", "-i", str(SSH_KEY), *SSH_OPTIONS, "-P", port,
             str(local), f"root@{host}:{remote}"],
            capture_output=True, text=True, timeout=timeout, check=False)
        if done.returncode == 0:
            return True
        time.sleep(10)
    return False


def verify_registration() -> dict:
    """Refuse to spend unless every pinned input still hashes to its manifest."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    checks = {
        "parent": (ROOT / manifest["parent_adapter"], manifest["parent_adapter_sha256"]),
        "candidate": (ROOT / manifest["candidate_adapter"],
                      manifest["candidate_adapter_sha256"]),
        "eval": (EVAL_LOCAL, manifest["eval_sha256"]),
    }
    for name, (path, expected) in checks.items():
        if not path.is_file():
            raise RuntimeError(f"registered {name} is missing: {path}")
        actual = sha256(path)
        if actual != expected:
            raise RuntimeError(
                f"registered {name} changed since registration\n"
                f"  manifest {expected}\n  on disk  {actual}")
    log(f"registration verified — alphas {manifest['alphas']}, "
        f"gate {manifest['promotion_gate']}")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="verify registration and pod availability, spend nothing")
    args = parser.parse_args()

    manifest = verify_registration()
    key = por._key()
    pods = por.read_inventory(key)
    if pods is None:
        log("BLOCKED: pod inventory unreadable")
        return 1
    capacity = por.read_host_capacity([str(p.get("id")) for p in pods], key)
    decision = por.decide_with_capacity(por.read_record(), pods, capacity)
    log(f"pod decision: {decision.action} {decision.pod_id} — {decision.reason}")
    if decision.action in {por.BLOCKED, por.CREATE}:
        log("BLOCKED: no pod we already own can seat a GPU right now. "
            "This is host capacity — retry, do not create.")
        return 1
    if args.dry_run:
        log("dry run — registration and capacity both good, nothing spent")
        return 0

    started = (por.start_and_wait(decision.pod_id, key)
               if decision.action == por.RESUME
               else {"pod_id": decision.pod_id,
                     "host": str(next(p for p in pods
                                      if p["id"] == decision.pod_id).get("publicIp")),
                     "port": str((next(p for p in pods if p["id"] == decision.pod_id)
                                  .get("portMappings") or {}).get("22"))})
    pod_id, host, port = started["pod_id"], started["host"], started["port"]
    log(f"pod {pod_id} running at {host}:{port} with {started.get('gpu_count')} GPU(s)")

    for _ in range(40):
        if ssh(host, port, "echo ok", timeout=30).stdout.strip() == "ok":
            break
        time.sleep(5)
    else:
        por.stop(pod_id, key)
        log("BLOCKED: SSH never stabilised; pod stopped")
        return 1

    # A reused pod may hold a previous run's reports — move them aside so a
    # harvest can never publish stale evidence as this run's.
    archive = por.archive_command(time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()))
    if "ARCHIVED" not in ssh(host, port, archive).stdout:
        por.stop(pod_id, key)
        log("BLOCKED: prior evidence not archived; pod stopped")
        return 1

    ssh(host, port, "mkdir -p /workspace/datasets /workspace/logs /workspace/eval "
                    "/workspace/crucix/scripts/train /workspace/checkpoints/adapters")
    ssh(host, port, "touch /workspace/crucix/scripts/__init__.py")

    uploads = [(POD_RUNNER, "/workspace/pod_tooluse_lora_interpolation_v3.sh"),
               (WATCHDOG, "/workspace/pod_selfstop_watch_v04.sh"),
               (EVAL_LOCAL, "/workspace/datasets/aria_tooluse_eval.jsonl")]
    uploads += [(ROOT / "scripts/train" / name,
                 f"/workspace/crucix/scripts/train/{name}") for name in SUPPORT]
    uploads += [(ROOT / manifest["parent_adapter"], "/workspace/parent.tgz"),
                (ROOT / manifest["candidate_adapter"], "/workspace/candidate.tgz")]
    for local, remote in uploads:
        if not local.is_file():
            log(f"note: skipping absent {local.name}")
            continue
        log(f"uploading {local.name} ({local.stat().st_size // 1024} KiB)")
        if not scp(host, port, local, remote):
            por.stop(pod_id, key)
            log(f"BLOCKED: upload failed for {local.name}; pod stopped")
            return 1

    log("extracting adapters")
    extract = (
        "set -e; cd /workspace/checkpoints/adapters; rm -rf parent candidate; "
        "mkdir -p parent candidate; "
        "tar -xzf /workspace/parent.tgz -C parent; "
        "tar -xzf /workspace/candidate.tgz -C candidate; "
        "P=$(dirname $(find parent -name adapter_config.json | head -1)); "
        "C=$(dirname $(find candidate -name adapter_config.json | head -1)); "
        "[ -n \"$P\" ] && [ -n \"$C\" ] || { echo NOADAPTER; exit 1; }; "
        "echo PARENT_DIR=/workspace/checkpoints/adapters/$P; "
        "echo CANDIDATE_DIR=/workspace/checkpoints/adapters/$C")
    out = ssh(host, port, extract, timeout=600).stdout
    dirs = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    if "PARENT_DIR" not in dirs or "CANDIDATE_DIR" not in dirs:
        por.stop(pod_id, key)
        log(f"BLOCKED: adapters did not extract cleanly; pod stopped. {out[:300]}")
        return 1
    log(f"parent={dirs['PARENT_DIR']} candidate={dirs['CANDIDATE_DIR']}")

    log("arming the self-stop watchdog BEFORE the work")
    armed = ssh(host, port,
                f"POD_ID={pod_id} RP_KEY='{key}' GRACE={GRACE} DEADLINE={DEADLINE} "
                f"COLLECT_GRACE={COLLECT_GRACE} setsid nohup bash "
                f"/workspace/pod_selfstop_watch_v04.sh "
                f">/workspace/logs/_selfstop.log 2>&1 </dev/null & "
                f"echo $! > /workspace/eval/_watchdog_pid; echo ARMED")
    if "ARMED" not in armed.stdout:
        por.stop(pod_id, key)
        log("BLOCKED: watchdog would not arm; refusing to start unbounded work")
        return 1

    log("starting the sweep detached")
    startup = ssh(host, port,
                  f"rm -f /workspace/eval/_cycle_status; "
                  f"PARENT={dirs['PARENT_DIR']} CANDIDATE={dirs['CANDIDATE_DIR']} "
                  f"setsid nohup bash /workspace/pod_tooluse_lora_interpolation_v3.sh "
                  f">/workspace/logs/interpolation_v3.log 2>&1 </dev/null & echo STARTED")
    if "STARTED" not in startup.stdout:
        por.stop(pod_id, key)
        log("BLOCKED: sweep did not start; pod stopped")
        return 1

    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        f"POD_ID={pod_id}\nHOST={host}\nPORT={port}\n"
        f"LAUNCHED_AT={int(time.time())}\nDEADLINE={DEADLINE}\n", encoding="utf-8", newline="\n")
    log(f"LAUNCHED — state in {STATE_FILE.name}")
    log("the pod now works independently of this machine and self-stops after "
        f"{DEADLINE}s even if nobody returns.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
