"""R-F4241 — ARIA trains on ONE durable pod, reused, and never deleted.

WHY THIS EXISTS. Measured 2026-08-23 against the live RunPod account: **70 pods**,
every one of them EXITED, created one-per-run by `_create_v04_pod.py`. Nothing
ever deleted them — the operator's standing instruction — but nothing ever reused
one either, so each cycle created the next. With **zero pods RUNNING** the account
still billed `currentSpendPerHr = 0.077` — **$55.44/month of idle disk** — against
a `clientBalance` of `$3.92`. The stopped pods, not the training, were draining
the account: about 51 hours of runway with no work being done.

So "stop deleting pods" and "use the same pod" are the SAME fix, not two. The
cost does not come from deleting too little; it comes from CREATING too much.
Reuse one pod and the fleet stops growing, with nothing destroyed.

The second, larger cost is stranded evidence. R-F4167's interpolation sweep sat
on a stopped pod for three days because the harvest could not get the machine
back (`not_enough_free_gpus_on_host`). Resuming that same pod on 2026-08-23
recovered **three complete 168-row reports** — measurements that a delete would
have destroyed and that a new pod could never reproduce without paying for the
sweep again. A pod holding unharvested work is evidence, not garbage.

WHAT THIS MODULE REFUSES TO DO
  * It never issues DELETE. There is no terminate path here, and
    `test_rf4241_pod_of_record.py` asserts the source contains none.
  * It never treats an unreadable inventory as permission to create. An API
    error means "I could not measure", and creating on a failed read is how a
    71st pod appears while the 70th is sitting idle and healthy — the same
    absence-reads-as-a-measurement shape CLAUDE.md section 1 records for three
    Phase A gates. Unreadable is BLOCKED, never CREATE.
  * It never reports a decision without the evidence that produced it. Every
    Decision carries the observed status, so a CREATE can be audited afterwards.

WHAT IT DOES NOT SOLVE, STATED PLAINLY. A reused pod is pinned to one host
machine, so a busy host can refuse to start it — that is the exact error that
blocked R-F4167. Reuse therefore trades a rare hard stop for a fleet that stops
growing. The answer to a refusal is to retry, or to tell the operator; it is NOT
to quietly create a new pod, because that is the behaviour being fixed.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

ROOT = pathlib.Path(__file__).resolve().parents[2]
RECORD_FILE = ROOT / "data/training/pod_of_record.json"
REST = "https://rest.runpod.io/v1"

# The provider's own words for a host that cannot seat the pod right now. This
# is transient — the same pod that failed with it on 2026-08-20 started on
# 2026-08-23 — so it is retried, never escalated into a create.
TRANSIENT_START_MARKERS = ("not enough free gpus", "no free gpus", "capacity")

REUSE = "reuse"        # already RUNNING — take it as it is
RESUME = "resume"      # EXITED but alive — start the same pod
CREATE = "create"      # genuinely gone; a new pod is the only option
BLOCKED = "blocked"    # could not measure, or an unrecognised state


@dataclass(frozen=True)
class Decision:
    """One auditable choice about which pod the next cycle runs on."""

    action: str
    pod_id: str | None
    reason: str
    observed_status: str | None = None
    evidence: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "action": self.action, "pod_id": self.pod_id, "reason": self.reason,
            "observed_status": self.observed_status, "evidence": self.evidence,
        }


def _status(pod: dict) -> str:
    return str(pod.get("desiredStatus") or pod.get("status") or "").upper()


def decide(record: dict | None, pods: list[dict] | None) -> Decision:
    """Choose REUSE / RESUME / CREATE / BLOCKED from what was actually observed.

    Pure: no network, no clock, no filesystem. `pods is None` means the
    inventory could not be read and is never grounds to create.
    """
    if pods is None:
        return Decision(BLOCKED, None, "pod inventory unreadable — refusing to "
                                       "create a pod that may already exist")
    pod_id = str((record or {}).get("pod_id") or "").strip()
    if not pod_id:
        return Decision(CREATE, None, "no pod of record is registered",
                        evidence={"fleet_size": len(pods)})
    match = next((p for p in pods if str(p.get("id") or "") == pod_id), None)
    if match is None:
        return Decision(CREATE, None,
                        f"pod of record {pod_id} is absent from the provider fleet",
                        evidence={"fleet_size": len(pods), "prior_pod_id": pod_id})
    status = _status(match)
    if status == "RUNNING":
        return Decision(REUSE, pod_id, "pod of record is already running", status)
    if status == "EXITED":
        return Decision(RESUME, pod_id, "pod of record is stopped and resumable", status)
    if status in {"TERMINATED", "DEAD"}:
        return Decision(CREATE, None,
                        f"pod of record {pod_id} is {status.lower()} and cannot be resumed",
                        status, {"prior_pod_id": pod_id})
    return Decision(BLOCKED, pod_id,
                    f"pod of record is in an unrecognised state: {status or 'EMPTY'}",
                    status)


def is_transient_start_failure(detail: str) -> bool:
    """True when the provider refused a start for capacity, not for good."""
    lowered = (detail or "").lower()
    return any(marker in lowered for marker in TRANSIENT_START_MARKERS)


# -- provider glue (kept thin so `decide` stays the testable part) -----------

def _key() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("RUNPOD_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("RUNPOD_API_KEY not in .env")


def _req(method: str, path: str, key: str, body: dict | None = None) -> tuple[int, object]:
    request = urllib.request.Request(
        REST + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return response.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as exc:
        return exc.code, {"error": exc.read(400).decode("utf-8", errors="replace")}


def read_inventory(key: str) -> list[dict] | None:
    """Every pod on the account, or None when it could not be read.

    None is load-bearing: `decide` turns it into BLOCKED. Collapsing an
    unreadable inventory into an empty list would read as "no pods exist" and
    authorise a create against a healthy fleet.
    """
    code, payload = _req("GET", "/pods", key)
    if code != 200:
        return None
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("pods"), list):
        return payload["pods"]
    return None


def read_record(path: pathlib.Path = RECORD_FILE) -> dict | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_record(pod_id: str, note: str, path: pathlib.Path = RECORD_FILE) -> dict:
    """Register the pod every future cycle must reuse."""
    record = {
        "pod_id": pod_id,
        "note": note,
        "registered_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "policy": "reuse this pod; never DELETE any pod; create only when this "
                  "one is absent or terminated (R-F4241)",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record


GRAPHQL = "https://api.runpod.io/graphql"


def gpu_count(payload: dict | None) -> int | None:
    """GPUs actually attached to a RUNNING pod, or None when unmeasurable.

    Tri-state on purpose. `0` is a MEASURED absence and must stop a paid run;
    `None` is "the instrument did not answer" and is not evidence either way.
    Collapsing them would either strand a healthy pod or bless a useless one.
    """
    pod = ((payload or {}).get("data") or {}).get("pod")
    if not isinstance(pod, dict):
        return None
    runtime = pod.get("runtime")
    if isinstance(runtime, dict) and isinstance(runtime.get("gpus"), list):
        return len(runtime["gpus"])
    count = pod.get("gpuCount")
    return int(count) if isinstance(count, int) else None


def read_gpu_count(pod_id: str, key: str) -> int | None:
    """Ask the provider how many GPUs the running pod actually holds."""
    query = ('query { pod(input:{podId:"%s"}) { gpuCount '
             'runtime { gpus { id } } } }' % pod_id)
    request = urllib.request.Request(
        f"{GRAPHQL}?api_key={urllib.parse.quote(key)}",
        data=json.dumps({"query": query}).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "aria-pod-of-record/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return gpu_count(json.loads(response.read().decode("utf-8", errors="replace")))
    except (urllib.error.URLError, ValueError, OSError):
        return None


def start_and_wait(pod_id: str, key: str, *, attempts: int = 6,
                   retry_seconds: int = 90, ready_ticks: int = 40,
                   require_gpu: bool = True) -> dict:
    """Start the pod of record, retrying only a transient capacity refusal.

    R-F4241, MEASURED 2026-08-23 and the reason `require_gpu` exists: this pod
    resumed to `desiredStatus: RUNNING` with SSH working and **no GPU** —
    `runtime.gpus: []`, and from inside the machine `torch.cuda.is_available()`
    False with no `/dev/nvidia*` at all. The provider reported success; the host
    simply had no A40 free, which is the same shortage that returns
    `not_enough_free_gpus_on_host` on other attempts. A start that succeeds
    without the GPU is a capacity refusal wearing a success, and handing that
    pod to a training launcher buys a pip install and a doomed run at $0.22/hr.
    So a paid caller must see the GPU CONFIRMED — not merely un-refuted — and
    the pod is stopped again when it is not, so a failed reuse costs minutes,
    not an unattended day of billing.
    """
    last = ""
    for attempt in range(1, attempts + 1):
        code, payload = _req("POST", f"/pods/{pod_id}/start", key)
        detail = json.dumps(payload)[:300]
        if code in {200, 201, 202}:
            break
        last = f"HTTP {code}: {detail}"
        if not is_transient_start_failure(detail):
            raise RuntimeError(f"pod {pod_id} start refused permanently — {last}")
        print(f"[pod-of-record] start attempt {attempt}/{attempts} refused for "
              f"capacity; retrying in {retry_seconds}s", file=sys.stderr)
        time.sleep(retry_seconds)
    else:
        raise RuntimeError(f"pod {pod_id} never started — {last}")
    for _ in range(ready_ticks):
        code, pod = _req("GET", f"/pods/{pod_id}", key)
        if code == 200 and isinstance(pod, dict):
            host = str(pod.get("publicIp") or "")
            port = str((pod.get("portMappings") or {}).get("22") or "")
            if _status(pod) == "RUNNING" and host and port:
                gpus = read_gpu_count(pod_id, key)
                if require_gpu and not (isinstance(gpus, int) and gpus >= 1):
                    stop(pod_id, key)
                    detail = ("the provider reports 0 GPUs attached"
                              if gpus == 0 else
                              "the GPU count could not be read")
                    raise RuntimeError(
                        f"pod {pod_id} reached RUNNING without a usable GPU — "
                        f"{detail}. Stopped it again rather than billing for a "
                        f"pod that cannot train. This is a host-capacity "
                        f"condition, not a reason to create another pod.")
                return {"pod_id": pod_id, "host": host, "port": port,
                        "gpu_count": gpus}
        time.sleep(10)
    stop(pod_id, key)
    raise RuntimeError(f"pod {pod_id} started but never exposed SSH")


def stop(pod_id: str, key: str) -> bool:
    """Stop a pod. Stopping is not destroying — its /workspace survives."""
    code, _ = _req("POST", f"/pods/{pod_id}/stop", key)
    return code in {200, 201, 204}


def archive_command(stamp: str) -> str:
    """Shell that moves a previous run's EVIDENCE aside on a reused pod.

    THE ONE HAZARD REUSE INTRODUCES. A fresh pod starts with an empty
    /workspace, so a harvest could only ever collect what this run produced. A
    reused pod already holds the last run's `/workspace/eval/*.json`, and a
    cycle that dies before writing its own report would let the harvester pull
    a THREE-DAY-OLD report and publish it as this run's measurement. That is
    not a stale file; it is a fabricated result on the path that decides
    whether an adapter gets promoted.

    So evidence directories are cleared before work starts — by MOVING, never
    deleting, per the operator's standing instruction and because a superseded
    report is still a measurement somebody paid for. `checkpoints/` is left
    alone: its contents are already named per run, so they cannot be mistaken
    for each other, and moving 1.6 GB every cycle would be the expensive half
    of a problem that only the small half actually has.
    """
    prior = f"/workspace/_prior/{stamp}"
    return (
        f"set -e; mkdir -p {prior}; "
        f"for d in eval logs; do "
        f"  if [ -d /workspace/$d ]; then mv /workspace/$d {prior}/$d; fi; "
        f"  mkdir -p /workspace/$d; "
        f"done; "
        f"rm -f /workspace/eval/_cycle_status; "
        f"echo ARCHIVED {prior}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command",
                        choices=("status", "decide", "adopt", "ensure", "archive-command"))
    parser.add_argument("--pod-id", default="", help="pod to adopt")
    parser.add_argument("--note", default="adopted by operator direction")
    parser.add_argument("--no-start", action="store_true",
                        help="ensure: report the decision without paying to start")
    args = parser.parse_args()

    if args.command == "archive-command":
        print(archive_command(time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())))
        return 0

    if args.command == "adopt":
        if not args.pod_id:
            print("adopt requires --pod-id", file=sys.stderr)
            return 2
        print(json.dumps(write_record(args.pod_id, args.note), indent=2))
        return 0

    key = _key()
    decision = decide(read_record(), read_inventory(key))
    if args.command in {"status", "decide"}:
        print(json.dumps(decision.as_dict(), indent=2))
        return 0 if decision.action != BLOCKED else 1

    if decision.action == BLOCKED:
        print(f"BLOCKED: {decision.reason}", file=sys.stderr)
        return 1
    if decision.action == CREATE:
        print(f"CREATE REQUIRED: {decision.reason}", file=sys.stderr)
        return 3
    if args.no_start:
        print(json.dumps(decision.as_dict(), indent=2))
        return 0
    if decision.action == REUSE:
        code, pod = _req("GET", f"/pods/{decision.pod_id}", key)
        host = str((pod or {}).get("publicIp") or "")
        port = str(((pod or {}).get("portMappings") or {}).get("22") or "")
        print(json.dumps({"pod_id": decision.pod_id, "host": host, "port": port}))
        return 0
    print(json.dumps(start_and_wait(decision.pod_id, key)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
