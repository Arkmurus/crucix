"""R-F3393 — the SFT trainer's default base model contradicted the one every cycle script uses.

FOUND DURING THE §24 PRE-FLIGHT. `sft_train.py` declared:

    ap.add_argument("--base-model", default="meta-llama/Llama-3.3-70B-Instruct")

but ARIA's base model is `mistralai/Mistral-7B-Instruct-v0.3`, and the repo says
so in every place that actually runs:

  - activate_aria_llm_v01.sh:13   BASE_MODEL="mistralai/Mistral-7B-Instruct-v0.3"
  - baseline_pod_run.sh:17        same, commented "v0.2 actual base (R-F1454)"
  - baseline_pod_run.sh:42        FAILS the run if the adapter's recorded base
                                  does not match it
  - aria_llm_v01_activation.md    vLLM serves mistral-7b-instruct-v0.3; the
                                  adapter ships the identical vocab
  - and the north star: "The moat is verification, not the 7B"

WHY A STALE DEFAULT IS NOT COSMETIC. Anyone invoking the trainer directly — a new
cycle script, a manual pod run, an autonomous job — silently trains against a
DIFFERENT architecture. The adapter is then unusable: baseline_pod_run.sh's own
guard exists because an adapter/base mismatch has bitten before. And the failure
is expensive and late: Llama-3.3-70B is HF-gated (this account gets 403 on it),
so a run would consume pod time and die at model download.

A default is a decision that gets made when nobody is looking. This one disagreed
with the recorded decision, so it is removed: `--base-model` is now REQUIRED, and
the agreed value is exported as a named constant the cycle scripts can reference.
Choosing what ARIA trains on should be an explicit act.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SFT = ROOT / "scripts" / "train" / "sft_train.py"
ACTIVATE = ROOT / "scripts" / "train" / "activate_aria_llm_v01.sh"
BASELINE = ROOT / "scripts" / "train" / "baseline_pod_run.sh"

AGREED = "mistralai/Mistral-7B-Instruct-v0.3"


def _sft_src() -> str:
    return SFT.read_text(encoding="utf-8")


# ── the trainer must not carry a contradicting default ────────────────────

def test_no_llama_default_remains():
    src = _sft_src()
    assert "meta-llama/Llama-3.3-70B-Instruct" not in src, (
        "the trainer still defaults to a model ARIA does not train on, and which "
        "this HF account cannot even download"
    )


def test_base_model_is_required_not_defaulted():
    """A default is a decision made when nobody is looking. Make it explicit."""
    src = _sft_src()
    m = re.search(r'add_argument\(\s*"--base-model"[^)]*\)', src, re.S)
    assert m, "--base-model argument not found"
    arg = m.group(0)
    assert "required=True" in arg, arg
    assert "default=" not in arg, f"still has a default: {arg}"


def test_agreed_base_is_exported_as_a_constant():
    import importlib.util
    spec = importlib.util.spec_from_file_location("_sft", SFT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert getattr(mod, "ARIA_BASE_MODEL", None) == AGREED


def test_trainer_still_parses():
    ast.parse(_sft_src())


# ── the recorded decision is what the cycle scripts use ───────────────────

def test_cycle_scripts_agree_on_the_base():
    for p in (ACTIVATE, BASELINE):
        assert p.exists(), p
        assert AGREED in p.read_text(encoding="utf-8"), f"{p.name} disagrees"


def test_adapter_base_guard_still_present():
    """baseline_pod_run.sh fails when the adapter's recorded base does not match.
    That guard is why a wrong default is dangerous — keep it."""
    src = BASELINE.read_text(encoding="utf-8")
    assert "adapter base" in src and "fail" in src


# ── the trainer rejects a run with no explicit base ───────────────────────

def test_missing_base_model_is_a_hard_error():
    import subprocess, sys
    r = subprocess.run(
        [sys.executable, str(SFT), "--train-file", "x.jsonl", "--output-dir", "out"],
        capture_output=True, text=True, timeout=90,   # R-F3459: below the 120s per-test budget
    )
    assert r.returncode != 0
    assert "base-model" in (r.stderr + r.stdout).lower()
