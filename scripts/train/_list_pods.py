import sys, json, urllib.request, re, pathlib
key = ""
for ln in pathlib.Path(".env").read_text().splitlines():
    if ln.startswith("RUNPOD_API_KEY="):
        key = ln.split("=", 1)[1].strip().strip('"').strip("'")
        break
req = urllib.request.Request("https://rest.runpod.io/v1/pods",
                             headers={"Authorization": f"Bearer {key}"})
d = json.load(urllib.request.urlopen(req, timeout=30))
pods = d if isinstance(d, list) else d.get("pods", [])
if not pods:
    print("  (no pods)")
for p in pods:
    print(" ", p.get("id"), "|", p.get("name"), "|", p.get("desiredStatus"))
