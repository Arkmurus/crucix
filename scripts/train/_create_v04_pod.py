"""Create a GPU pod for the v0.4 cycle on the eval volume, with the SSH public
key injected (PUBLIC_KEY env) so the orchestrator can SSH in. Prints the pod id
on success, nothing on capacity failure. Exit 0 always (launcher reads stdout).

Why a helper: API-created pods (unlike console ones) don't get the account SSH
key automatically, and the create body needs JSON-safe quoting for the key.
Only VALID gpuTypeIds (the enum the REST API accepts) — one bad string rejects
the whole request (R-F1514 learned this the hard way)."""
import json, urllib.request, urllib.error, pathlib

VOL = "4vdw2zmqov"
# Valid enum strings only (a single invalid one => schema reject, not capacity).
GPUS = ["NVIDIA A40", "NVIDIA L40S", "NVIDIA RTX A6000", "NVIDIA L40",
        "NVIDIA RTX 6000 Ada Generation", "NVIDIA A100 80GB PCIe",
        "NVIDIA A100-SXM4-80GB"]

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
    "networkVolumeId": VOL, "volumeMountPath": "/workspace",
    "containerDiskInGb": 80, "ports": ["8888/http", "22/tcp"],
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
