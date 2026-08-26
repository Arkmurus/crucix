"""R-F4352 / C-297 — the eval-only runner assumed a Python environment that a
pod restart destroys.

MEASURED 2026-08-26. The v0.8 adapter finished training at 01:48, the pod was
stopped, and the eval-only re-run was launched against it at 07:32. It hung.
GPU 0%, 0 MiB, zero answers, the runner sitting in its curl-retry loop — the
exact signature of a slow model load. It was nothing of the kind:

    /workspace/logs/shim_aria-llm-v0.7.log
    ModuleNotFoundError: No module named 'uvicorn'

RunPod restores the image layer and the `/workspace` VOLUME on start. Everything
pip-installed AFTER boot lives on the container overlay and is destroyed. Probed
on the restarted pod:

    torch 2.4.1+cu124   OK        (baked into the image)
    transformers        MISSING
    peft                MISSING
    bitsandbytes        MISSING
    uvicorn / fastapi   MISSING

`v0_4_pod_run.sh` installs a pinned set and import-checks it before training.
`pod_eval_only.sh` did NEITHER — and it is the runner MOST likely to meet a cold
pod, because its entire purpose is to re-evaluate an adapter already sitting on
the volume, long after the training pod was stopped. The one script written for
the restarted-pod case was the one that assumed the pod had never restarted.

WHY THE IMPORT-CHECK MATTERS AS MUCH AS THE INSTALL. Without it the failure
surfaces as a serve timeout: the shim dies on an import, never binds the port,
and the runner's retry loop reports "could not serve". That sent the first
diagnosis to the GPU and the model load. Failing at the import costs one second
and names the module.

THE PINS ARE NOT COSMETIC. A LoRA adapter is loaded by the same
peft/transformers stack that wrote it. Installing whatever is newest would
change what is being measured while every other signal still looked healthy —
the same class as the base/LoRA collision (C-281), where a config check passed
while the wrong weights answered.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts" / "train" / "pod_eval_only.sh"
TRAINER = ROOT / "scripts" / "train" / "v0_4_pod_run.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None, reason="needs bash to drive the real script")


def _dep_block() -> str:
    """Extract by CONTENT anchor, never line number (§16 / R-F3597)."""
    src = RUNNER.read_text(encoding="utf-8")
    i = src.index("# R-F4352 (C-297)")
    # The CLOSING delimiter is `PY` alone on its own line. A bare
    # `index("PY", ...)` matches the `PY` inside `<<'PY'` itself and truncates
    # the heredoc, which bash then reports as "delimited by end-of-file" — the
    # block still ran, so the mistake showed up as a missing final echo rather
    # than as an extraction error.
    start = src.index("python - <<'PY'", i)
    j = src.index("\nPY\n", start) + len("\nPY\n")
    return src[i:j]


def _run(script: str, cwd: pathlib.Path, extra_path: pathlib.Path | None = None):
    env = dict(os.environ)
    if extra_path:
        env["PATH"] = str(extra_path) + os.pathsep + env["PATH"]
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                          cwd=str(cwd), env=env, timeout=180)


def _stub(tmp_path: pathlib.Path, name: str, body: str) -> pathlib.Path:
    d = tmp_path / "stub"
    d.mkdir(exist_ok=True)
    p = d / name
    p.write_text("#!/usr/bin/env bash\n" + body + "\n", encoding="utf-8", newline="\n")
    p.chmod(0o755)
    return d


_PRELUDE = 'log(){ echo "[log] $*"; }\nfail(){ echo "[FATAL] $*" >&2; exit 1; }\n'


# --------------------------------------------- THE CAPABILITY TEST

def test_it_refuses_to_serve_when_a_dependency_is_missing(tmp_path):
    """THE LIVE SYMPTOM, inverted. The shim died on `import uvicorn` and the
    failure presented as a serve timeout with the GPU idle. The runner must now
    stop at the import check and say which stack is broken."""
    d = _stub(tmp_path, "pip", "exit 0")                       # install "succeeds"
    (d / "python").write_text(                                  # imports still fail
        "#!/usr/bin/env bash\ncat >/dev/null\n"
        "echo \"ModuleNotFoundError: No module named 'uvicorn'\" >&2\nexit 1\n",
        encoding="utf-8", newline="\n")
    (d / "python").chmod(0o755)
    r = _run(_PRELUDE + _dep_block() + "\necho REACHED_SERVE", tmp_path, d)
    assert r.returncode != 0, (
        "proceeded to serve with a broken stack — this is the hang:\n" + r.stdout)
    assert "REACHED_SERVE" not in r.stdout
    assert "dep import check failed" in r.stderr, r.stderr


def test_it_proceeds_when_the_stack_is_intact(tmp_path):
    """THE GATE MUST OPEN. A check that only ever blocks would make every
    eval-only run impossible — pinning the refusal is half a test."""
    d = _stub(tmp_path, "pip", "exit 0")
    (d / "python").write_text(
        "#!/usr/bin/env bash\ncat >/dev/null\necho '[deps] transformers 4.46.3 peft 0.13.2 OK'\n",
        encoding="utf-8", newline="\n")
    (d / "python").chmod(0o755)
    r = _run(_PRELUDE + _dep_block() + "\necho REACHED_SERVE", tmp_path, d)
    assert r.returncode == 0, r.stderr[-600:]
    assert "REACHED_SERVE" in r.stdout, r.stdout


def test_it_actually_installs_rather_than_only_checking(tmp_path):
    """An import check alone would fail honestly and still never run. The
    install is what makes a cold pod usable."""
    marker = tmp_path / "pip_was_called"
    d = _stub(tmp_path, "pip", f'echo "$@" > "{marker.as_posix()}"; exit 0')
    (d / "python").write_text("#!/usr/bin/env bash\ncat >/dev/null\nexit 0\n",
                              encoding="utf-8", newline="\n")
    (d / "python").chmod(0o755)
    r = _run(_PRELUDE + _dep_block(), tmp_path, d)
    assert r.returncode == 0, r.stderr[-400:]
    assert marker.exists(), "pip was never invoked — nothing would be installed"
    args = marker.read_text(encoding="utf-8")
    for pkg in ("transformers", "peft", "uvicorn", "fastapi", "bitsandbytes"):
        assert pkg in args, f"{pkg} is imported by the shim but never installed"


# ------------------------------------------- the pins must not drift

def test_the_pins_match_the_trainer_exactly():
    """A LoRA adapter is loaded by the stack that wrote it. If the eval-only
    runner drifts from the trainer, the two measure different things while
    every other signal still reads healthy — the C-281 shape."""
    import re
    runner = RUNNER.read_text(encoding="utf-8")
    trainer = TRAINER.read_text(encoding="utf-8")
    pinned = re.compile(r'"(transformers|peft)==([0-9.]+)"')
    r_pins = dict(pinned.findall(runner))
    t_pins = dict(pinned.findall(trainer))
    assert t_pins, "the trainer pins nothing — this comparison is meaningless"

    # THE KEY SET FIRST. A version-by-version loop over what the runner HAPPENS
    # to pin cannot see a pin that was DELETED — mutation testing caught exactly
    # that: replacing "transformers==4.46.3" with "transformers" passed,
    # because the missing key simply dropped out of the iteration. A guard that
    # only validates what is present certifies whatever is absent.
    missing = set(t_pins) - set(r_pins)
    assert not missing, (
        f"the trainer pins {sorted(missing)} but the eval-only runner does not "
        f"— the adapter would be loaded by whatever pip resolves today")

    for pkg, ver in r_pins.items():
        assert t_pins.get(pkg) == ver, (
            f"{pkg}: eval-only pins {ver} but the trainer pins "
            f"{t_pins.get(pkg)} — the adapter would be loaded by a different "
            f"stack than wrote it")


def test_the_install_precedes_the_serve():
    """Ordering is the whole point: installing after the shim launch would fix
    nothing, because the shim is what imports the modules."""
    src = RUNNER.read_text(encoding="utf-8")
    assert src.index("# R-F4352 (C-297)") < src.index("serve_eval_shim.py"), (
        "deps are installed after the shim is launched — too late to help it")
