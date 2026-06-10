"""Retry pod start every 60s until GPU frees up or timeout."""
import httpx, json, time, sys, os

pod_id = os.environ.get("RUNPOD_POD_ID", "7ei3hldcpz4j2v")
api_base = os.environ.get("RUNPOD_API_BASE", "https://rest.runpod.io/v1")

# Load API key from .env
key = None
env_path = os.path.join(os.path.dirname(__file__), "..", "..", ".env")
with open(env_path, encoding="utf-8") as f:
    for line in f:
        if line.startswith("RUNPOD_API_KEY="):
            key = line.split("=", 1)[1].strip().strip("\"'").strip()
            break

if not key:
    print("RUNPOD_API_KEY not found in .env")
    sys.exit(1)

print(f"Retrying pod {pod_id} start every 60s (up to 30 attempts)...")
sys.stdout.flush()

for i in range(1, 31):
    try:
        r = httpx.post(
            f"{api_base}/pods/{pod_id}/start",
            headers={"Authorization": f"Bearer {key}"},
            timeout=30,
        )
        if r.status_code == 200:
            d = r.json()
            print(f"[{i}] STARTED! desiredStatus={d.get('desiredStatus')}")
            sys.stdout.flush()
            sys.exit(0)
        err = r.json().get("error", "unknown")
        print(f"[{i}] {r.status_code}: {err[:100]}")
        sys.stdout.flush()
    except Exception as e:
        print(f"[{i}] Error: {e}")
        sys.stdout.flush()
    if i < 30:
        time.sleep(60)

print("GPU did not free up after 30 min")
sys.exit(1)
