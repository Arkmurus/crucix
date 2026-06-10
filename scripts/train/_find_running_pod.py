"""Print the id of a RUNNING (or starting) pod on the eval volume, else nothing.

Catches a pod the operator migrated/started from the console — a migration keeps
the network volume (US-KS-2) so we match on networkVolumeId. Exit 0 always; the
launcher reads stdout.
"""
import json, pathlib, urllib.request

VOL = "4vdw2zmqov"
key = ""
for ln in pathlib.Path(".env").read_text().splitlines():
    if ln.startswith("RUNPOD_API_KEY="):
        key = ln.split("=", 1)[1].strip().strip('"').strip("'")
        break
try:
    req = urllib.request.Request("https://rest.runpod.io/v1/pods",
                                 headers={"Authorization": f"Bearer {key}"})
    d = json.load(urllib.request.urlopen(req, timeout=30))
    pods = d if isinstance(d, list) else d.get("pods", [])
except Exception:
    pods = []

# Prefer RUNNING; accept a pod that's on the volume and not EXITED/TERMINATED.
for want in ("RUNNING",):
    for p in pods:
        if p.get("networkVolumeId") == VOL and p.get("desiredStatus") == want:
            print(p.get("id", ""))
            raise SystemExit(0)
