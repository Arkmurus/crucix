"""R-F4245 — collect a finished cycle from its pod, whatever happened locally.

WHY THIS IS GENERAL AND NOT ANOTHER ONE-OFF.

CORRECTION FIRST (R-F4247, same day): this docstring originally said the driver
"was killed ~40 minutes into a paid run" on 2026-08-23. **That did not happen.**
The harness stopped TRACKING the foreground task and reported it killed; the
bash process kept running and finished normally at 07:34:30, recovering
`intermediate=1 report=1 diagnostics=1 logs=1` and verifying the pod stopped. I
wrote the motivation from a notification instead of from the driver's own log,
which was still advancing the whole time.

The tool is still worth having, on the evidence that is real: R-F3420 records
long background drivers genuinely being killed on this machine, rebuilt
`tooluse_launch.sh` as launch-and-exit because of it, and wrote *"nothing here
waits for the cycle, so a kill costs nothing"* — while `run_tooluse_dpo.sh`, the
driver every recent resolution cycle uses, still waits for ~40 minutes. R-F4197
then had to hand-write a recovery for ONE pod when a harvest really did fail.
This generalises that recovery; it does not commemorate an incident.

What IS demonstrated: the pod side is built correctly. Work is detached
(`setsid nohup`), a self-stop watchdog bounds it, and the driver writes
POD_ID/HOST/PORT to a durable STATE_FILE *before* starting the cycle — which is
what let this tool read the live run's handoff and pull a validated report.

AND ONE THING THIS TOOL GOT WRONG, kept here because it bit on first use: run
against a cycle whose driver was still finishing, it resumed a pod the driver
was about to stop and pulled a half-written adapter (178 MB against the driver's
complete 310 MB). It refused to publish it, which is the behaviour that matters,
but it should not have raced at all. Do not run this while a driver is alive;
check the driver's log first.

R-F4197 already solved this once — for one pod, with the id, the phase and the
three report names hardcoded. That is the shape that guarantees a fourth
incident writes a fourth script. This reads the STATE_FILE the driver already
wrote, so it works for any cycle that wrote one.

TWO PROPERTIES CARRIED OVER FROM THE INCIDENTS THAT PRODUCED THEM:

  * **Harvest does not need a GPU.** R-F4241 makes a paid start demand a
    CONFIRMED GPU, because a training run on a GPU-less pod burns money for
    nothing. Collection is the opposite case — it moves files — and the R-F4167
    rescue succeeded on a pod that had come back with `runtime.gpus: []`.
    Requiring a GPU here would strand results behind a host-capacity problem
    that has no bearing on reading a file.

  * **Diagnostics first, then results.** If the pull budget runs out or the pod
    stops mid-harvest, the thing that explains a failure is worth more than a
    partial artefact of it.

Nothing is ever deleted, and the pod is stopped and CONFIRMED stopped on every
exit path — including a failed harvest, because a pod left running after a
failed collection is the bill nobody notices.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.train import pod_of_record as por  # noqa: E402

SSH_KEY = pathlib.Path.home() / ".ssh/runpod_aria"
SSH_OPTIONS = ("-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
               "-o", "LogLevel=ERROR", "-o", "ConnectTimeout=20")

# Remote layout the tool-use pod runners agree on.
REMOTE_SENTINEL = "/workspace/eval/_cycle_status"
REMOTE_REPORT = "/workspace/eval/aria_tooluse_dpo_eval.json"
REMOTE_ADAPTER = "/workspace/eval/aria_tooluse_dpo_adapter.tgz"
REMOTE_DIAGNOSTICS = "/workspace/eval/aria_tooluse_curve_diagnostics.tgz"


def read_state(path: pathlib.Path) -> dict[str, str]:
    """The durable handoff the driver wrote before starting the cycle."""
    if not path.is_file():
        raise RuntimeError(f"no cycle state file: {path}")
    state: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if sep and key and value:
            state[key.strip()] = value.strip()
    for required in ("POD_ID", "HOST", "PORT"):
        if not state.get(required):
            raise RuntimeError(f"cycle state is missing {required}: {path}")
    return state


def _ssh(host: str, port: str, command: str, *, timeout: int = 90):
    return subprocess.run(
        ["ssh", "-i", str(SSH_KEY), *SSH_OPTIONS, "-p", port, f"root@{host}", command],
        capture_output=True, text=True, timeout=timeout, check=False)


def _pull(host: str, port: str, remote: str, local: pathlib.Path,
          *, timeout: int = 900) -> bool:
    local.parent.mkdir(parents=True, exist_ok=True)
    done = subprocess.run(
        ["scp", "-i", str(SSH_KEY), *SSH_OPTIONS, "-P", port,
         f"root@{host}:{remote}", str(local)],
        capture_output=True, text=True, timeout=timeout, check=False)
    return done.returncode == 0


def read_sentinel(host: str, port: str) -> str | None:
    """The cycle's own exit code, or None when it has not finished."""
    done = _ssh(host, port, f"cat {REMOTE_SENTINEL} 2>/dev/null")
    value = (done.stdout or "").strip()
    return value or None


def validate_report(path: pathlib.Path, expected_rows: int) -> dict:
    """A report is only publishable if it PROVES it is complete.

    A partial report is the dangerous artefact here: it parses, it carries an
    honest count, and nothing about it says the run stopped early. Refusing it
    is the whole reason collection is a separate, checked step.
    """
    report = json.loads(path.read_text(encoding="utf-8"))
    rows = report.get("rows")
    if report.get("complete") is not True:
        raise RuntimeError(f"{path.name}: report does not declare completeness")
    if report.get("total") != expected_rows or not isinstance(rows, list) \
            or len(rows) != expected_rows:
        raise RuntimeError(
            f"{path.name}: expected {expected_rows} rows, found "
            f"{len(rows) if isinstance(rows, list) else 'none'} "
            f"(total={report.get('total')})")
    if not report.get("scorer_version"):
        # R-F4244 — an unversioned report cannot be compared to anything.
        raise RuntimeError(f"{path.name}: report declares no scorer_version")
    return report


def validate_adapter(path: pathlib.Path) -> None:
    """A LoRA tarball must actually contain an adapter."""
    import tarfile
    with tarfile.open(path, "r:gz") as bundle:
        if not any(name.endswith("/adapter_config.json") or
                   name == "adapter_config.json" for name in bundle.getnames()):
            raise RuntimeError(f"{path.name}: no adapter_config.json in tarball")


def confirm_stopped(pod_id: str, key: str, *, attempts: int = 12,
                    delay: float = 5.0) -> bool:
    """Stop the pod and read back that it actually stopped.

    The wait is injectable because a hardcoded one is untestable: the first
    version slept a real 5s x 12 per mocked case and made the suite take two
    minutes to prove some string handling. A retry loop nobody can drive at
    speed is a retry loop nobody covers.
    """
    por.stop(pod_id, key)
    for _ in range(attempts):
        code, pod = por._req("GET", f"/pods/{pod_id}", key)
        if code == 200 and por._status(pod) == "EXITED":
            return True
        time.sleep(delay)
    return False


def harvest(state_file: pathlib.Path, *,
            reports: "list[tuple[str, pathlib.Path]]",
            adapter_out: pathlib.Path | None,
            diagnostics_out: pathlib.Path | None,
            expected_rows: int, stop_when_done: bool = True,
            stop_attempts: int = 12, stop_delay: float = 5.0) -> dict:
    """Resume if needed, collect, validate, publish atomically, stop the pod."""
    state = read_state(state_file)
    key = por._key()
    pod_id = state["POD_ID"]
    host, port = state["HOST"], state["PORT"]

    code, pod = por._req("GET", f"/pods/{pod_id}", key)
    if code != 200 or not isinstance(pod, dict):
        raise RuntimeError(f"provider readback failed for {pod_id} (HTTP {code})")
    if por._status(pod) == "EXITED":
        # No GPU required — collection moves files. See the module docstring.
        started = por.start_and_wait(pod_id, key, require_gpu=False)
        host, port = started["host"], started["port"]

    outcome: dict = {"pod_id": pod_id, "resumed_host": host, "resumed_port": port}
    harvest_error: Exception | None = None
    try:
        outcome["cycle_status"] = read_sentinel(host, port)
        # Diagnostics FIRST — they explain a failure the results cannot.
        if diagnostics_out is not None:
            partial = diagnostics_out.with_suffix(diagnostics_out.suffix + ".download")
            if _pull(host, port, REMOTE_DIAGNOSTICS, partial):
                partial.replace(diagnostics_out)
                outcome["diagnostics"] = str(diagnostics_out)
        staged: list[tuple[pathlib.Path, pathlib.Path]] = []

        outcome["reports"] = {}
        for remote, destination in reports:
            partial = destination.with_suffix(destination.suffix + ".download")
            if not _pull(host, port, remote, partial):
                raise RuntimeError(f"report could not be pulled: {remote}")
            report = validate_report(partial, expected_rows)
            outcome["reports"][destination.name] = {
                "honest": report["honest"], "total": report["total"],
                "scorer_version": report["scorer_version"]}
            staged.append((partial, destination))

        if adapter_out is not None:
            partial = adapter_out.with_suffix(adapter_out.suffix + ".download")
            if not _pull(host, port, REMOTE_ADAPTER, partial):
                raise RuntimeError("candidate adapter could not be pulled")
            validate_adapter(partial)
            staged.append((partial, adapter_out))

        for source, destination in staged:
            destination.parent.mkdir(parents=True, exist_ok=True)
            source.replace(destination)
        outcome["published"] = [str(d) for _, d in staged]
    except Exception as exc:  # noqa: BLE001 — every failure must still stop the pod
        harvest_error = exc
    finally:
        if stop_when_done:
            outcome["stop_confirmed"] = confirm_stopped(
                pod_id, key, attempts=stop_attempts, delay=stop_delay)
    if harvest_error is not None:
        outcome["error"] = str(harvest_error)
        raise RuntimeError(json.dumps(outcome, indent=2)) from harvest_error
    return outcome


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--state-file", required=True, type=pathlib.Path)
    parser.add_argument("--report-out", type=pathlib.Path, default=None,
                        help=f"publish {REMOTE_REPORT} here (single-report runs)")
    parser.add_argument("--report", action="append", default=[],
                        metavar="REMOTE=LOCAL",
                        help="repeatable; collect an explicitly named remote "
                             "report. A sweep writes one per arm, so a single "
                             "hardcoded name made this tool general over DPO "
                             "runs only (R-F4250).")
    parser.add_argument("--adapter-out", type=pathlib.Path, default=None)
    parser.add_argument("--diagnostics-out", type=pathlib.Path, default=None)
    parser.add_argument("--expected-rows", type=int, default=168)
    parser.add_argument("--leave-running", action="store_true",
                        help="do NOT stop the pod (use only when more work follows)")
    args = parser.parse_args(argv)
    reports = [(remote, pathlib.Path(local)) for remote, _, local in
               (item.partition("=") for item in args.report) if local]
    if args.report_out is not None:
        reports.append((REMOTE_REPORT, args.report_out))
    if not reports:
        print("nothing to collect: pass --report-out or --report REMOTE=LOCAL",
              file=sys.stderr)
        return 2
    outcome = harvest(args.state_file, reports=reports,
                      adapter_out=args.adapter_out,
                      diagnostics_out=args.diagnostics_out,
                      expected_rows=args.expected_rows,
                      stop_when_done=not args.leave_running)
    print(json.dumps(outcome, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
