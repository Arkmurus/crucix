"""Create a GPU pod for the v0.4 cycle, with the SSH public key injected
(PUBLIC_KEY env) so the orchestrator can SSH in. Prints the pod id on success,
nothing on capacity failure. Exit 0 always (launcher reads stdout).

R-F1516 — VOLUME-FREE. The earlier build pinned networkVolumeId "4vdw2zmqov",
which region-locks every pod to US-KS-2 — the datacenter that was DELETING pods
within minutes (3 lost 2026-06-11, one mid-train). A network volume is
region-locked, so the volume WAS the trap. Dropping it lets the scheduler place
the pod in ANY datacenter with capacity. The cost: no pre-cached HF base and no
on-volume v0.3 adapter — handled in v0_4_pod_run.sh (base downloads fresh from an
ungated mirror; v0.4 compares to the KNOWN v0.3=0.22, no re-serve). Container
disk is bumped to fit the ~15GB base download + LoRA checkpoints (was on volume).

Why a helper: API-created pods (unlike console ones) don't get the account SSH
key automatically, and the create body needs JSON-safe quoting for the key.
Only VALID gpuTypeIds (the enum the REST API accepts) — one bad string rejects
the whole request (R-F1514 learned this the hard way)."""
import json, os, urllib.request, urllib.error, pathlib

# Valid enum strings only (a single invalid one => schema reject, not capacity).
# R-F2037: ARIA_POD_GPUS env overrides the list (e.g. force A100-80 for vLLM
# colocate, which needs >=70GB) — comma-separated valid enum strings.
GPUS = (
    [g.strip() for g in os.getenv("ARIA_POD_GPUS", "").split(",") if g.strip()]
    or ["NVIDIA A40", "NVIDIA L40S", "NVIDIA RTX A6000", "NVIDIA L40",
        "NVIDIA RTX 6000 Ada Generation", "NVIDIA A100 80GB PCIe",
        "NVIDIA A100-SXM4-80GB"]
)

key = ""
for ln in pathlib.Path(".env").read_text().splitlines():
    if ln.startswith("RUNPOD_API_KEY="):
        key = ln.split("=", 1)[1].strip().strip('"').strip("'")
        break
pub = pathlib.Path.home().joinpath(".ssh", "runpod_aria.pub").read_text().strip()
body = {
    "name": "aria-v04-train",
    "imageName": "runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04",
    "gpuTypeIds": GPUS, "gpuCount": 1, "cloudType": "SECURE",
    # NO networkVolumeId — volume-free so the pod can land in any DC (R-F1516).
    # Container disk holds the fresh base download + checkpoints. R-F1671: a 7B
    # fits in 120GB, but a 14B (~30GB) + HF xet tmp-doubling overflowed it
    # ("No space left on device"). Env-overridable — the v0.8 14B run sets 250.
    "containerDiskInGb": int(os.getenv("ARIA_POD_DISK_GB", "120")), "ports": ["8888/http", "22/tcp"],
    "env": {"PUBLIC_KEY": pub},
}
req = urllib.request.Request(
    "https://rest.runpod.io/v1/pods", data=json.dumps(body).encode(),
    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    method="POST")
try:
    d = json.load(urllib.request.urlopen(req, timeout=60))
    if d.get("id"):
        print(d["id"])
except Exception:
    pass  # capacity / schema -> print nothing
