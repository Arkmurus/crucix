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

SCOPE NARROWED BY R-F4350 (C-295). This file originally also drove the
cache-disk selection, which R-F4347 fixed INLINE in `v0_4_pod_run.sh`. R-F4350
then found the same pin in EIGHTEEN more scripts and replaced all nineteen with
one sourced helper, `scripts/train/hf_cache_select.sh`. Those three tests moved
with the code to `test_rf4350_hf_cache_disk_is_one_definition.py`, which drives
the same cases against the helper and adds an enumeration guard so a twentieth
script cannot quietly reintroduce the pin. What remains here is what is
genuinely R-F4347's: the verdict gate, and the check that the mistaken
"container disk" comment has not come back.
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


def _verdict_block() -> str:
    """The cycle driver could not be marker-annotated: it was EXECUTING at the
    time and Windows refused the replace — which was the OS preventing an edit
    to a script bash was still reading incrementally. Anchored on content
    instead, which is what a marker would have provided anyway.
    """
    src = CYCLE.read_text(encoding="utf-8")
    return _between(src, 'if [ "${RC:-1}" != "0" ]; then',
                    'echo "[driver] === v0.5 GROUNDED CYCLE RESULT')


def _run(script: str, cwd: pathlib.Path, env: dict) -> subprocess.CompletedProcess:
    e = dict(os.environ)
    e.update(env)
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                          cwd=str(cwd), env=e, timeout=120)


# -------- (1) THE DISK TESTS MOVED TO R-F4350 --------
#
# R-F4347 fixed the cache-disk pin INLINE in v0_4_pod_run.sh, and these tests
# extracted that block by marker. R-F4350 (C-295) then found the same pin in
# EIGHTEEN more scripts and replaced all nineteen with one sourced helper,
# scripts/train/hf_cache_select.sh — so the block these tests read no longer
# exists, and keeping a marker-extraction against a deleted block would fail
# for the right reason but the wrong cause.
#
# The behaviour is not less covered, it is better covered:
# test_rf4350_hf_cache_disk_is_one_definition.py drives the same three cases
# (picks the roomy disk / refuses when none fits / honours an inherited
# HF_HOME) against the shared helper, and adds an enumeration guard so a
# twentieth script cannot quietly reintroduce the pin.
#
# What stays here is what is genuinely R-F4347's: the verdict gate below, and
# the check that the mistaken "container disk" comment has not returned.


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
