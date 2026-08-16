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
import json
import os
import pathlib
import sys
import urllib.error
import urllib.parse
import urllib.request

# Valid enum strings only (a single invalid one => schema reject, not capacity).
# R-F2037: ARIA_POD_GPUS env overrides the list (e.g. force A100-80 for vLLM
# colocate, which needs >=70GB) — comma-separated valid enum strings.
GPUS = (
    [g.strip() for g in os.getenv("ARIA_POD_GPUS", "").split(",") if g.strip()]
    or ["NVIDIA A40", "NVIDIA L40S", "NVIDIA RTX A6000", "NVIDIA L40",
        "NVIDIA RTX 6000 Ada Generation", "NVIDIA A100 80GB PCIe",
        "NVIDIA A100-SXM4-80GB"]
)

_STOCK_RANK = {"High": 0, "Medium": 1}


def select_secure_gpus(inventory: list[dict], max_hourly_price: float) -> list[str]:
    """Return approved High/Medium secure GPUs within the hourly price ceiling."""
    selected = []
    for row in inventory:
        gpu_id = str(row.get("id") or "")
        price = row.get("lowestPrice") or {}
        stock = price.get("stockStatus")
        hourly = price.get("uninterruptablePrice")
        if gpu_id not in GPUS or stock not in _STOCK_RANK or hourly is None:
            continue
        if float(hourly) <= max_hourly_price:
            selected.append((_STOCK_RANK[stock], float(hourly), gpu_id))
    return [gpu_id for _, _, gpu_id in sorted(selected)]


def query_secure_inventory(api_key: str) -> list[dict]:
    """Read current secure stock and prices from RunPod's inventory API."""
    query = """query {
      gpuTypes {
        id
        lowestPrice(input: { gpuCount: 1, secureCloud: true }) {
          stockStatus
          uninterruptablePrice
          availableGpuCounts
        }
      }
    }"""
    payload = graphql_request(api_key, query)
    rows = (payload.get("data") or {}).get("gpuTypes")
    if not isinstance(rows, list):
        raise RuntimeError("inventory response omitted gpuTypes")
    return rows


def graphql_request(api_key: str, query: str) -> dict:
    """Execute one authenticated GraphQL operation with RunPod's required client identity."""
    url = "https://api.runpod.io/graphql?" + urllib.parse.urlencode({"api_key": api_key})
    request = urllib.request.Request(
        url,
        data=json.dumps({"query": query}).encode(),
        headers={
            "Content-Type": "application/json",
            "User-Agent": "aria-capacity-gate/1.0",
        },
    )
    try:
        payload = json.load(urllib.request.urlopen(request, timeout=30))
    except urllib.error.HTTPError as exc:
        detail = " ".join(exc.read(500).decode("utf-8", errors="replace").split())
        raise RuntimeError(f"GraphQL HTTP {exc.code}: {detail or exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"GraphQL transport: {exc.reason}") from exc
    errors = payload.get("errors") or []
    if errors:
        raise RuntimeError(f"GraphQL error: {str(errors)[:500]}")
    return payload


def create_pod_graphql(
    api_key: str, public_key: str, gpu_id: str, container_disk_gb: int,
) -> str:
    """Create one Secure Cloud pod through RunPod's stock-specific mutation."""
    mutation = f"""mutation {{
      podFindAndDeployOnDemand(input: {{
        cloudType: SECURE
        gpuCount: 1
        volumeInGb: 0
        containerDiskInGb: {container_disk_gb}
        minVcpuCount: 2
        minMemoryInGb: 15
        gpuTypeId: {json.dumps(gpu_id)}
        name: "aria-v04-train"
        imageName: "runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04"
        dockerArgs: ""
        ports: "8888/http,22/tcp"
        volumeMountPath: "/workspace"
        env: [{{ key: "PUBLIC_KEY", value: {json.dumps(public_key)} }}]
      }}) {{ id }}
    }}"""
    payload = graphql_request(api_key, mutation)
    pod_id = str((payload.get("data") or {}).get("podFindAndDeployOnDemand", {}).get("id") or "").strip()
    if not pod_id:
        raise RuntimeError("GraphQL create response omitted pod id")
    return pod_id

def create_pod(api_key: str, public_key: str) -> str:
    """Create one pod or raise with the provider's bounded rejection detail."""
    max_hourly_price = float(os.getenv("ARIA_MAX_GPU_HOURLY_USD", "1.60"))
    available_gpus = select_secure_gpus(query_secure_inventory(api_key), max_hourly_price)
    if not available_gpus:
        raise RuntimeError(
            f"inventory: no approved High/Medium secure GPU at or below ${max_hourly_price:.2f}/hour"
        )
    container_disk_gb = int(os.getenv("ARIA_POD_DISK_GB", "120"))
    create_api = os.getenv("ARIA_POD_CREATE_API", "rest").strip().lower()
    if create_api == "graphql":
        rejections: list[str] = []
        for gpu_id in available_gpus:
            try:
                return create_pod_graphql(
                    api_key, public_key, gpu_id, container_disk_gb,
                )
            except RuntimeError as exc:
                rejections.append(f"{gpu_id}: {exc}")
        raise RuntimeError(
            "all approved GraphQL GPU placements rejected: "
            + " | ".join(rejections)
        )
    if create_api != "rest":
        raise RuntimeError(f"unsupported pod create API: {create_api}")
    body = {
        "name": "aria-v04-train",
        "imageName": "runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04",
        "gpuTypeIds": available_gpus, "gpuTypePriority": "availability",
        "dataCenterPriority": "availability", "gpuCount": 1, "cloudType": "SECURE",
        # NO networkVolumeId — volume-free so the pod can land in any DC (R-F1516).
        "containerDiskInGb": container_disk_gb,
        "ports": ["8888/http", "22/tcp"], "env": {"PUBLIC_KEY": public_key},
    }
    req = urllib.request.Request(
        "https://rest.runpod.io/v1/pods", data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST")
    try:
        response = json.load(urllib.request.urlopen(req, timeout=60))
    except urllib.error.HTTPError as exc:
        detail = exc.read(500).decode("utf-8", errors="replace")
        detail = " ".join(detail.split())
        raise RuntimeError(f"HTTP {exc.code}: {detail or exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"transport: {exc.reason}") from exc
    pod_id = str(response.get("id") or "").strip()
    if not pod_id:
        raise RuntimeError("HTTP 2xx response omitted pod id")
    return pod_id


def main() -> int:
    """Load local credentials, create one pod, and report honest failure detail."""
    key = ""
    for line in pathlib.Path(".env").read_text().splitlines():
        if line.startswith("RUNPOD_API_KEY="):
            key = line.split("=", 1)[1].strip().strip('"').strip("'")
            break
    public_key = pathlib.Path.home().joinpath(".ssh", "runpod_aria.pub").read_text().strip()
    try:
        print(create_pod(key, public_key))
        return 0
    except Exception as exc:  # noqa: BLE001 — CLI boundary must preserve category
        print(f"[pod-create] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
