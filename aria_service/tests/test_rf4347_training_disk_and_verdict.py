"""R-F4347 / C-291 — the training cache filled the wrong disk, and the driver
then scored a run that never trained.

BOTH DEFECTS WERE OBSERVED IN ONE RUN, 2026-08-26, and the second is the one
that would have misled a reader.

(1) THE CACHE WAS PINNED TO THE SMALL DISK BY A MISTAKEN COMMENT.
`v0_4_pod_run.sh` read:

    export HF_HOME=/workspace/.cache/huggingface   # container disk

/workspace is not the container disk. Measured on the pod mid-failure:

    /dev/md0   20G   20G  1.5M  100%  /workspace     <- a 20G VOLUME
    overlay   122G   16M  122G    1%  /              <- the actual container disk

The base model is ~15G, so the download filled the volume and died with
"OSError: No space left on device (os error 28)" after pulling gigabytes. And
because it used `export` rather than ``${HF_HOME:-...}``, it overwrote any
inherited value — so the cache could not be redirected from outside either.

(2) THE DRIVER PRINTED A GATE VERDICT FOR A RUN THAT PRODUCED NO MODEL.
After the FATAL, and immediately after logging "(v0.5 grounded report not
pulled)", it printed:

    v0.5 GROUNDED (open-book): judge-DD=0.3 (n=500) | leak_rate=0.2
    G1 accuracy: FAIL (v0.5 0.3 vs 0.316 parity)

There was no checkpoint and no new report. That 0.3 came from the STALE local
file left by an earlier cycle, presented as this run's result. A reader — human
or agent — would take it as the trained model's score and conclude the
curriculum had failed, when nothing had been trained at all. That is the
absence-reads-as-measurement shape CLAUDE.md §1 records for three Phase A
gates, here applied to a training verdict.

WHY THESE TESTS EXTRACT AND RUN THE REAL SHELL rather than re-implementing the
logic: a Python copy of the rule would pass forever while the shipped script
rotted. The blocks are located by CONTENT ANCHOR, never by line number — §16
records `inspect.getsource` line slicing silently returning a different
function's body when a peer commit shifted the file (R-F3597), and the same
fragility applies to `sed -n '34,80p'`.

THE `df` SHIM IS THE POINT OF THE FIRST TEST. Free space cannot be faked on a
real filesystem, so the test puts a scripted `df` on PATH. That also exercises
the awk parse of `df -Pm` output, which is itself a real failure point — a
changed column order would silently yield 0 MB free and, before the fail-fast
guard, would have picked a disk at random.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
POD_RUN = ROOT / "scripts" / "train" / "v0_4_pod_run.sh"
CYCLE = ROOT / "scripts" / "train" / "run_v0_5_grounded_cycle.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None, reason="needs bash to drive the real scripts")


# ---------------------------------------------------------------- helpers

def _between(text: str, start: str, end: str) -> str:
    """Extract a block by CONTENT anchors. Never by line number (R-F3597)."""
    i = text.index(start)
    j = text.index(end, i)
    return text[i:j]


def _hf_cache_block() -> str:
    src = POD_RUN.read_text(encoding="utf-8")
    return _between(src, "# --- R-F4347:hf-cache BEGIN",
                    "# --- R-F4347:hf-cache END")


def _verdict_block() -> str:
    """The cycle driver could not be marker-annotated: it was EXECUTING at the
    time and Windows refused the replace — which was the OS preventing an edit
    to a script bash was still reading incrementally. Anchored on content
    instead, which is what a marker would have provided anyway.
    """
    src = CYCLE.read_text(encoding="utf-8")
    return _between(src, 'if [ "${RC:-1}" != "0" ]; then',
                    'echo "[driver] === v0.5 GROUNDED CYCLE RESULT')


def _bash_path(p) -> str:
    """bash sees POSIX paths. A Windows path in a `case` pattern would have its
    backslashes eaten as escapes and match nothing — which made the shim report
    0 MB for every candidate and the fail-fast guard fire on all three tests.
    """
    try:
        out = subprocess.run(["cygpath", "-u", str(p)], capture_output=True,
                             text=True, timeout=30)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:
        pass
    return str(p).replace("\\", "/")


def _df_shim(tmp_path: pathlib.Path, free_by_path: dict) -> pathlib.Path:
    """A `df -Pm` reporting scripted free space, in the real column layout."""
    bindir = tmp_path / "shim"
    bindir.mkdir(exist_ok=True)
    lines = [
        "#!/usr/bin/env bash",
        'args=(); for a in "$@"; do [ "$a" = "-Pm" ] && continue; args+=("$a"); done',
        'echo "Filesystem 1048576-blocks Used Available Capacity Mounted-on"',
        'for p in "${args[@]}"; do',
        '  case "$p" in',
    ]
    for path, free in free_by_path.items():
        lines.append('    ' + path + '*) echo "fake 100000 1 ' + str(free) + ' 1% $p" ;;')
    lines += [
        '    *) echo "fake 100000 1 0 100% $p" ;;',
        '  esac',
        'done',
    ]
    sh = bindir / "df"
    sh.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    sh.chmod(0o755)
    return bindir


def _run(script: str, cwd: pathlib.Path, env: dict) -> subprocess.CompletedProcess:
    e = dict(os.environ)
    e.update(env)
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                          cwd=str(cwd), env=e, timeout=120)


# ------------------------------------------- (1) THE CAPABILITY TEST: disk

def test_it_picks_the_disk_with_room_instead_of_the_pinned_small_one(tmp_path):
    """THE LIVE SYMPTOM. The pinned candidate has 2 GB, the other has 122 GB.
    Choosing the pinned one is what produced ENOSPC ten minutes into a paid
    GPU run."""
    small, big = tmp_path / "vol", tmp_path / "overlay"
    small.mkdir()
    big.mkdir()
    shim = _df_shim(tmp_path, {_bash_path(small): 2000, _bash_path(big): 122000})
    r = _run(_hf_cache_block() + '\necho "PICKED=$HF_HOME"', tmp_path, {
        "PATH": str(shim) + os.pathsep + os.environ["PATH"],
        "HF_CACHE_CANDIDATES": _bash_path(small) + " " + _bash_path(big),
        "HF_HOME": "",
    })
    assert r.returncode == 0, r.stderr[-800:]
    assert "PICKED=" + _bash_path(big) in r.stdout, (
        "picked the smaller disk — this is the ENOSPC defect.\n"
        + r.stdout + "\n" + r.stderr)


def test_it_refuses_to_start_when_no_disk_can_hold_the_model(tmp_path):
    """FAIL FAST WITH THE NUMBER. Before this, the run died at ENOSPC after
    pulling gigabytes; the operator saw a torch traceback rather than a disk
    problem. One second and a stated figure beats ten minutes and a stack."""
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    shim = _df_shim(tmp_path, {_bash_path(a): 2000, _bash_path(b): 3000})
    r = _run(_hf_cache_block(), tmp_path, {
        "PATH": str(shim) + os.pathsep + os.environ["PATH"],
        "HF_CACHE_CANDIDATES": _bash_path(a) + " " + _bash_path(b),
        "HF_HOME": "",
    })
    assert r.returncode == 1, "started anyway on a full disk:\n" + r.stdout
    assert "FATAL" in r.stderr and "3000 MB free" in r.stderr, r.stderr
    assert "18000" in r.stderr, (
        "the requirement is not stated, so the message is not actionable")


def test_an_inherited_HF_HOME_is_honoured(tmp_path):
    """The `export` overwrote any inherited value, so the cache could not be
    redirected without editing the script — on a pod, mid-incident."""
    chosen = tmp_path / "chosen"
    chosen.mkdir()
    shim = _df_shim(tmp_path, {_bash_path(chosen): 99000})
    r = _run(_hf_cache_block() + '\necho "PICKED=$HF_HOME"', tmp_path, {
        "PATH": str(shim) + os.pathsep + os.environ["PATH"],
        "HF_HOME": _bash_path(chosen),
    })
    assert r.returncode == 0, r.stderr[-800:]
    assert "PICKED=" + _bash_path(chosen) in r.stdout, r.stdout
    assert "inherited" in r.stdout, r.stdout


def test_the_mistaken_container_disk_comment_is_gone():
    """The comment is what let the bug survive review: it asserted the wrong
    disk was the right one, so a reader checking the line agreed with it."""
    src = POD_RUN.read_text(encoding="utf-8")
    for ln in src.splitlines():
        if "HF_HOME=/workspace/.cache/huggingface" in ln and "container disk" in ln:
            pytest.fail("the mistaken pin is back: " + ln.strip())


# --------------------------------------- (2) THE CAPABILITY TEST: verdict

_VERDICT_SETUP = (
    'stop_pod(){ echo "[stub] stop_pod"; }\n'
    '_RUN_STARTED="$(mktemp)"\n'
    'mkdir -p data/eval_reports\n'
)


def test_a_failed_run_gets_no_verdict(tmp_path):
    """THE DANGEROUS DEFECT. The run died with no checkpoint and the driver
    still printed 'judge-DD=0.3 ... G1 accuracy: FAIL'.

    THE REPORT IS DELIBERATELY MADE FRESH, AND THE ASSERTION NAMES THE RC GATE
    SPECIFICALLY. A first version left the report stale and asserted only
    "NO VERDICT" — mutation testing then showed it passing against a build with
    the RC gate DELETED, because the freshness guard blocked instead and BOTH
    guards print "NO VERDICT". The test was confirming an outcome that two
    different mechanisms produce, so it bound to neither. Isolating the gate is
    what gives the test the ability to fail.
    """
    d = tmp_path / "data" / "eval_reports"
    d.mkdir(parents=True)
    (d / "aria_llm_v0_5_grounded_eval.json").write_text(
        '{"defence_dd": {"accuracy": 0.3, "total": 500}}', encoding="utf-8")
    script = (_VERDICT_SETUP + 'RC=1\ntouch "$_RUN_STARTED"\nsleep 1\n'
              'touch data/eval_reports/aria_llm_v0_5_grounded_eval.json\n'
              + _verdict_block())
    r = _run(script, tmp_path, {})
    assert r.returncode == 1, "a failed cycle fell through to the verdict"
    assert "cycle FAILED" in r.stdout, (
        "blocked for some reason OTHER than the run having failed — the RC "
        "gate is not what stopped it:\n" + r.stdout)
    assert "judge-DD" not in r.stdout, (
        "scored a run that produced no model:\n" + r.stdout)


def test_a_stale_report_gets_no_verdict(tmp_path):
    """FRESHNESS, NOT EXISTENCE. The file always exists after the first
    successful cycle — existence is exactly what made the stale read look
    valid. Here the cycle 'succeeded' but no new report arrived."""
    d = tmp_path / "data" / "eval_reports"
    d.mkdir(parents=True)
    rep = d / "aria_llm_v0_5_grounded_eval.json"
    rep.write_text('{"defence_dd": {"accuracy": 0.3, "total": 500}}',
                   encoding="utf-8")
    old = 1_600_000_000  # long before any run start
    os.utime(rep, (old, old))
    r = _run(_VERDICT_SETUP + "RC=0\n" + _verdict_block(), tmp_path, {})
    assert r.returncode == 1, "a stale report was accepted as this run's result"
    assert "older than this run" in r.stdout, r.stdout


def test_a_real_result_is_still_reported(tmp_path):
    """THE GATE MUST OPEN. A guard that only ever blocks would make every
    cycle unreportable — pinning that it refuses is half a test."""
    d = tmp_path / "data" / "eval_reports"
    d.mkdir(parents=True)
    (d / "aria_llm_v0_5_grounded_eval.json").write_text(
        '{"defence_dd": {"accuracy": 0.42, "total": 500}}', encoding="utf-8")
    script = (_VERDICT_SETUP + 'RC=0\ntouch "$_RUN_STARTED"\nsleep 1\n'
              'touch data/eval_reports/aria_llm_v0_5_grounded_eval.json\n'
              + _verdict_block() + "\necho REACHED_VERDICT")
    r = _run(script, tmp_path, {})
    assert r.returncode == 0, (
        "blocked a legitimate result:\n" + r.stdout + "\n" + r.stderr)
    assert "REACHED_VERDICT" in r.stdout, r.stdout
    assert "NO VERDICT" not in r.stdout, r.stdout
