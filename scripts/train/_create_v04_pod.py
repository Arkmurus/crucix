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

# R-F4241 — the launchers invoke this file as a SCRIPT (`python
# scripts/train/_create_v04_pod.py`), so sys.path[0] is scripts/train and the
# package import below cannot resolve on its own. Without this the import
# raised ModuleNotFoundError, the script printed NOTHING on stdout, and the
# launcher read that empty line as a capacity failure — 15 retries, ~22 minutes,
# then "GAVE UP", with the real cause visible only on stderr. Resolve the repo
# root explicitly rather than relying on how we happened to be invoked.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from scripts.train import pod_of_record  # noqa: E402 — needs the path above

# Valid enum strings only (a single invalid one => schema reject, not capacity).
# R-F2037: ARIA_POD_GPUS env overrides the list (e.g. force A100-80 for vLLM
# colocate, which needs >=70GB) — comma-separated valid enum strings.
GPUS = (
    [g.strip() for g in os.getenv("ARIA_POD_GPUS", "").split(",") if g.strip()]
    or ["NVIDIA A40", "NVIDIA L40S", "NVIDIA RTX A6000", "NVIDIA L40",
        "NVIDIA RTX 6000 Ada Generation", "NVIDIA A100 80GB PCIe",
        "NVIDIA A100-SXM4-80GB"]
)

_STOCK_RANK = {"High": 0, "Medium": 1, "Low": 2}
_STABLE_STOCK = frozenset({"High", "Medium"})


def select_secure_gpus(
    inventory: list[dict], max_hourly_price: float, *,
    allowed_stock: frozenset[str] = _STABLE_STOCK,
) -> list[str]:
    """Return approved secure GPUs in allowed stock within the price ceiling."""
    selected = []
    for row in inventory:
        gpu_id = str(row.get("id") or "")
        price = row.get("lowestPrice") or {}
        stock = price.get("stockStatus")
        hourly = price.get("uninterruptablePrice")
        if gpu_id not in GPUS or stock not in allowed_stock or hourly is None:
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


def pod_name() -> str:
    """What this pod is FOR. Overridable with ARIA_POD_NAME.

    R-F4373 (C-318) — every pod this script has ever created was called
    "aria-v04-train", so the fleet is a list of identical names and nothing in
    it says which run owns which pod. That is not cosmetic: R-F4241's doctrine
    is "reuse, do not accumulate", so a second identically-named pod reads as an
    abandoned stray, and on 2026-08-26 a coder-training pod was stopped 45
    minutes into a paid run for exactly that reason ("Exited by user", while its
    own watchdog had 3.75h left on the clock and the cycle was healthy).

    A pod cannot defend itself from a correct policy applied to a wrong
    identification. Naming it after the work is what makes the policy able to
    tell the two apart. The default is unchanged, so every existing launcher
    keeps the name it has always used.
    """
    return (os.getenv("ARIA_POD_NAME") or "").strip() or "aria-v04-train"


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
        name: {json.dumps(pod_name())}
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
    evaluation_only = os.getenv("ARIA_EVALUATION_ONLY", "0") == "1"
    allow_low_stock = os.getenv("ARIA_ALLOW_LOW_STOCK", "0") == "1"
    if allow_low_stock and not evaluation_only:
        raise RuntimeError("Low stock requires ARIA_EVALUATION_ONLY=1")
    allowed_stock = (
        frozenset({"High", "Medium", "Low"}) if allow_low_stock else _STABLE_STOCK
    )
    available_gpus = select_secure_gpus(
        query_secure_inventory(api_key), max_hourly_price,
        allowed_stock=allowed_stock,
    )
    if not available_gpus:
        stock_label = "High/Medium/Low" if allow_low_stock else "High/Medium"
        raise RuntimeError(
            f"inventory: no approved {stock_label} secure GPU at or below "
            f"${max_hourly_price:.2f}/hour"
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
        # R-F4373 (C-318) — BOTH create paths must name the pod, or the fix is
        # half-applied and the REST path silently keeps producing the ambiguous
        # name that got a live run stopped.
        "name": pod_name(),
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
    """Return a pod to work on: REUSE the pod of record, or create one.

    R-F4241 — every launcher in this tree reaches a GPU through this script, so
    this is the ONE decision point where "reuse, do not accumulate" can be made
    true for all of them. Curating which launcher reuses would be whack-a-mole;
    the fifteenth launcher would silently create a 71st pod.

    A create still happens when the pod of record is genuinely gone, and the new
    pod is registered as the record so the NEXT run reuses it. There is
    deliberately no environment flag to force a fresh pod: an exception you can
    switch on from a shell script is not a rule, and this exact behaviour was
    already the thing being fixed. To move to a different pod, adopt it
    explicitly (`python -m scripts.train.pod_of_record adopt --pod-id ...`).
    """
    key = ""
    for line in pathlib.Path(".env").read_text().splitlines():
        if line.startswith("RUNPOD_API_KEY="):
            key = line.split("=", 1)[1].strip().strip('"').strip("'")
            break
    public_key = pathlib.Path.home().joinpath(".ssh", "runpod_aria.pub").read_text().strip()
    try:
        # Capacity-aware: a pod on a host with no free GPU resumes WITHOUT one
        # (measured 2026-08-23), so choosing it would buy a doomed run. This
        # reads `machine.gpuAvailable` for the whole fleet in one free query and
        # moves to another pod WE ALREADY OWN rather than creating a new one.
        pods = pod_of_record.read_inventory(key)
        capacity = (
            pod_of_record.read_host_capacity([str(p.get("id")) for p in pods], key)
            if pods is not None else {})
        decision = pod_of_record.decide_with_capacity(
            pod_of_record.read_record(), pods, capacity)
        if decision.action == pod_of_record.BLOCKED:
            # Never create on an unmeasurable fleet — that is how the 71st pod
            # appears while the 70th is idle and healthy.
            print(f"[pod-create] BLOCKED: {decision.reason}", file=sys.stderr)
            return 1
        if decision.action == pod_of_record.REUSE:
            print(f"[pod-create] reusing pod of record {decision.pod_id} "
                  f"(already running)", file=sys.stderr)
            print(decision.pod_id)
            return 0
        if decision.action == pod_of_record.RESUME:
            print(f"[pod-create] resuming pod of record {decision.pod_id} "
                  f"(was {decision.observed_status})", file=sys.stderr)
            pod_of_record.start_and_wait(decision.pod_id, key)
            print(decision.pod_id)
            return 0
        print(f"[pod-create] creating a pod: {decision.reason}", file=sys.stderr)
        pod_id = create_pod(key, public_key)
        pod_of_record.write_record(
            pod_id, f"created because {decision.reason} (R-F4241)")
        print(pod_id)
        return 0
    except Exception as exc:  # noqa: BLE001 — CLI boundary must preserve category
        print(f"[pod-create] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
