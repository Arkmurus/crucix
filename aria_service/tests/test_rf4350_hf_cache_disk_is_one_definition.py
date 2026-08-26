"""R-F4350 / C-295 — the cache-disk fix was applied to one script of nineteen.

R-F4347 (C-291) fixed a real defect: every pod script pinned the HuggingFace
cache to ``/workspace/.cache/huggingface`` under a comment calling it the
"container disk". Measured on the pod of record, mid-failure:

    /dev/md0   20G   20G  1.5M  100%  /workspace     <- a 20G VOLUME
    overlay   122G   16M  122G    1%  /              <- the container disk

A 7B base is ~15G, so the download filled the volume and died at ENOSPC after
pulling gigabytes of a paid GPU-hour.

**R-F4347 fixed exactly one file.** Eighteen more carried the identical line and
were untouched, so the next script to run — ``pod_eval_only.sh``, the one needed
for the very next measurement — would have reproduced the failure verbatim. A
fix scoped to one file silently certifies the rest.

TWO MISTAKES OF MINE ARE ENCODED HERE AS GUARDS, because both are the kind that
recurs:

1. **I first counted seven, from a truncated ``grep | head -20``.** The real
   number was nineteen. `test_no_script_pins_the_cache_disk` therefore
   enumerates the directory rather than a list — a headcount written down is a
   headcount that rots, and this one was wrong within a minute of being written.

2. **My first patch nested the loader inside its own comment and broke six
   scripts.** The replacement text quoted the very string being searched for, so
   a second pass matched its own output. `bash -n` caught it; nothing else
   would have, because the corruption was inside a comment block and looked
   plausible. `test_the_loader_text_cannot_match_itself` pins that.

THE FIX IS ONE DEFINITION: ``scripts/train/hf_cache_select.sh``, sourced by
every script that needs a cache. It fails CLOSED when absent — silently picking
a too-small disk is the failure it exists to prevent, so "could not decide"
must stop the run rather than guess.
"""
from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
TRAIN = ROOT / "scripts" / "train"
HELPER = TRAIN / "hf_cache_select.sh"
DRIVER = TRAIN / "run_v0_5_grounded_cycle.sh"

#: The small-disk path the defect pointed at.
SMALL_DISK = "/workspace/.cache/huggingface"

#: MY FIRST PREDICATE WAS TOO NARROW, and running it is what caught that.
#: Keying on ``export HF_HOME=`` missed ``train_promote_v0_2.sh``, which pinned
#: the same disk TWICE as an inline env prefix on a command:
#:
#:     HF_HOME=/workspace/.cache/huggingface VLLM_USE_DEEP_GEMM=0 \
#:
#: No ``export``, identical defect, invisible to the guard. So this matches the
#: ASSIGNMENT wherever it sits on the line — and deliberately also matches the
#: ``${HF_HOME:-...}`` default form, because a script run standalone with
#: nothing inherited lands on the 20G volume exactly as before.
PIN_RE = re.compile(
    r"""\bHF_HOME\s*=\s*["']?(?:\$\{HF_HOME:-)?""" + re.escape(SMALL_DISK))

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None, reason="needs bash to drive the real helper")


def _scripts():
    return sorted(list(TRAIN.glob("*.sh")) + list(TRAIN.glob("*.py")))


# ------------------------------------------- THE ENUMERATION GUARD

def test_no_script_pins_the_cache_disk():
    """THE DEFECT, swept across the whole directory rather than a list.

    Enumerating the directory is the point: the list I wrote by hand was wrong
    (seven, from a truncated grep) while the directory was not.
    """
    offenders = []
    for p in _scripts():
        try:
            body = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for i, line in enumerate(body.splitlines(), 1):
            if PIN_RE.search(line) and not line.lstrip().startswith("#"):
                offenders.append(f"{p.name}:{i}")
    assert not offenders, (
        "scripts pinning the cache to the 20G volume: " + ", ".join(offenders)
        + " — source hf_cache_select.sh instead")


def test_the_guard_can_actually_fail(tmp_path):
    """A guard that cannot fail certifies nothing (CLAUDE.md §1). Plant the
    defect in a scratch copy and confirm the same predicate catches it."""
    planted = tmp_path / "rogue_pod_run.sh"
    planted.write_text(
        "#!/usr/bin/env bash\n"
        "export HF_HOME=" + SMALL_DISK + "\n"
        "HF_HOME=" + SMALL_DISK + " some_command\n"
        'export HF_HOME="${HF_HOME:-' + SMALL_DISK + '}"\n'
        "# export HF_HOME=" + SMALL_DISK + "  <- a comment, must NOT count\n",
        encoding="utf-8", newline="\n")
    body = planted.read_text(encoding="utf-8")
    hits = [i for i, line in enumerate(body.splitlines(), 1)
            if PIN_RE.search(line) and not line.lstrip().startswith("#")]
    assert hits == [2, 3, 4], (
        f"the predicate misses a real pin form (matched lines {hits}) — line 3 "
        f"is the inline-env-prefix form my first version could not see, and "
        f"line 5 is a comment that must stay excluded")


def test_every_script_that_needs_a_cache_sources_the_one_definition():
    """The counterpart to the guard above: not pinning is not the same as being
    wired. A script could have had the line deleted and nothing put back, which
    fails later and more confusingly.

    SCOPED TO SCRIPTS THAT ASSIGN `HF_HOME` ITSELF. A first version flagged any
    file merely MENTIONING the name and failed eight of them — wrongly. A
    comment describing the variable (`launch_code_cycle.sh`) decides no disk,
    and neither do the separate `CODE_HF_HOME` / `ARIA_HF_HOME` config
    variables consumed by downstream launchers (`code_sovereign_config.sh`,
    `model_config.sh`); forcing the helper into those would be the guard
    over-fitting rather than the files being wrong.
    `pod_serve_vllm_supervised.sh` is also deliberately left alone: it pins
    `/root/.cache/huggingface`, the BIG disk — already correct before this fix
    existed, and rewriting a correct line to satisfy a guard is how a guard
    starts causing defects instead of finding them.
    """
    unwired = []
    for p in TRAIN.glob("*.sh"):
        body = p.read_text(encoding="utf-8")
        if "hf_cache_select" in body:
            continue
        assigns = [ln for ln in body.splitlines()
                   if re.search(r"\bHF_HOME\s*=", ln)
                   and not ln.lstrip().startswith("#")
                   and SMALL_DISK in ln]
        if assigns:
            unwired.append(f"{p.name}: {assigns[0].strip()[:60]}")
    assert not unwired, f"assign the small disk but never select one: {unwired}"


def test_the_loader_text_cannot_match_itself():
    """MY OWN BUG, PINNED. The first patch embedded the pin string inside the
    loader's explanatory comment, so a second pass matched its own output and
    nested the loader inside itself — breaking six scripts. Any future
    text-substitution over these files hits the same trap.
    """
    sample = next(p for p in TRAIN.glob("*.sh")
                  if "hf_cache_select" in p.read_text(encoding="utf-8")
                  and p.name != "hf_cache_select.sh")
    body = sample.read_text(encoding="utf-8")
    start = body.index("R-F4350")
    block = body[start:start + 900]
    assert not PIN_RE.search(block) and SMALL_DISK not in block, (
        f"{sample.name}: the loader block quotes the path it replaces — a "
        f"second substitution pass would nest it inside its own comment, which "
        f"is exactly what broke six scripts on the first attempt")


# -------------------------------------- THE CAPABILITY TEST: real bash

def _df_shim(tmp_path: pathlib.Path, free_by_path: dict) -> pathlib.Path:
    """A `df -Pm` reporting scripted free space, in the real column layout.
    Free space cannot be faked on a real filesystem, and this also exercises
    the awk parse — a changed column order would silently yield 0 MB."""
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
    lines += ['    *) echo "fake 100000 1 0 100% $p" ;;', '  esac', 'done']
    sh = bindir / "df"
    sh.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    sh.chmod(0o755)
    return bindir


def _bash_path(p) -> str:
    """bash sees POSIX paths; a Windows path inside a `case` pattern has its
    backslashes eaten as escapes and matches nothing."""
    try:
        out = subprocess.run(["cygpath", "-u", str(p)], capture_output=True,
                             text=True, timeout=30)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:
        pass
    return str(p).replace("\\", "/")


def _run(script: str, cwd: pathlib.Path, env: dict):
    e = dict(os.environ)
    e.update(env)
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                          cwd=str(cwd), env=e, timeout=120)


def _src():
    return f'. "{_bash_path(HELPER)}"\n'


def test_it_picks_the_disk_with_room(tmp_path):
    """THE LIVE SYMPTOM: the pinned candidate has 2 GB, the other 122 GB."""
    small, big = tmp_path / "vol", tmp_path / "overlay"
    small.mkdir(); big.mkdir()
    shim = _df_shim(tmp_path, {_bash_path(small): 2000, _bash_path(big): 122000})
    r = _run(_src() + 'hf_cache_select && echo "PICKED=$HF_HOME"', tmp_path, {
        "PATH": str(shim) + os.pathsep + os.environ["PATH"],
        "HF_CACHE_CANDIDATES": _bash_path(small) + " " + _bash_path(big),
        "HF_HOME": "",
    })
    assert r.returncode == 0, r.stderr[-800:]
    assert "PICKED=" + _bash_path(big) in r.stdout, (
        "picked the smaller disk — the ENOSPC defect\n" + r.stdout + r.stderr)


def test_it_refuses_when_no_disk_can_hold_the_model(tmp_path):
    """FAIL CLOSED, with the number. One second beats ten minutes and a torch
    traceback that names nothing about disks."""
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(); b.mkdir()
    shim = _df_shim(tmp_path, {_bash_path(a): 2000, _bash_path(b): 3000})
    r = _run(_src() + "hf_cache_select", tmp_path, {
        "PATH": str(shim) + os.pathsep + os.environ["PATH"],
        "HF_CACHE_CANDIDATES": _bash_path(a) + " " + _bash_path(b),
        "HF_HOME": "",
    })
    assert r.returncode != 0, "started anyway on a full disk:\n" + r.stdout
    assert "FATAL" in r.stderr and "3000 MB free" in r.stderr, r.stderr
    assert "18000" in r.stderr, "the requirement is unstated, so unactionable"


def test_an_inherited_HF_HOME_wins(tmp_path):
    """A caller that already chose a disk knows something the helper does not.
    The `export` form the defect used destroyed that."""
    chosen = tmp_path / "chosen"
    chosen.mkdir()
    shim = _df_shim(tmp_path, {_bash_path(chosen): 99000})
    r = _run(_src() + 'hf_cache_select && echo "PICKED=$HF_HOME"', tmp_path, {
        "PATH": str(shim) + os.pathsep + os.environ["PATH"],
        "HF_HOME": _bash_path(chosen),
    })
    assert r.returncode == 0, r.stderr[-800:]
    assert "PICKED=" + _bash_path(chosen) in r.stdout, r.stdout
    assert "inherited" in r.stdout, r.stdout


# ------------------------------------------------- delivery

def test_the_driver_ships_the_helper_to_the_pod():
    """Every pod script now fails CLOSED without this file, so a cycle that
    does not ship it does not run at all. The wiring and the delivery have to
    land together."""
    body = DRIVER.read_text(encoding="utf-8")
    assert "hf_cache_select.sh" in body, (
        "the driver does not push hf_cache_select.sh — every pod script it "
        "launches would fail closed")


def test_all_touched_scripts_still_parse():
    """The first patch attempt produced syntactically broken bash that looked
    fine on inspection because the damage sat inside a comment block."""
    broken = []
    for p in TRAIN.glob("*.sh"):
        r = subprocess.run(["bash", "-n", str(p)], capture_output=True,
                           text=True, timeout=60)
        if r.returncode != 0:
            broken.append(f"{p.name}: {r.stderr.strip()[:80]}")
    assert not broken, "unparseable after the rewrite: " + "; ".join(broken)
