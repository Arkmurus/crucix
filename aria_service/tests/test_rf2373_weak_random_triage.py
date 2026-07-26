"""R-F2373 — pseudo-random use is either secure or explicitly non-security."""

from __future__ import annotations

import re
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[2]
_TOUCHED_RANDOM_FILES = [
    "aria_service/intel/continuous_learner.py",
    "aria_service/intel/critique_collector.py",
    "aria_service/intel/deep_researcher.py",
    "aria_service/intel/llm_eval_framework.py",
    "aria_service/intel/rlaif.py",
    "aria_service/intel/self_improve.py",
    "aria_service/intel/student.py",
    "aria_service/intel/ua_rotation.py",
    "aria_service/main.py",
    "scripts/train/self_critique_sample.py",
]
_RANDOM_CALL_RE = re.compile(r"\b(?:random|_random|rng)\.(?:choice|sample|random|uniform|randint|Random)\b")


def test_rf2373_bridge_ids_use_secrets_not_random() -> None:
    source = (_ROOT / "aria_cli/bridge.py").read_text(encoding="utf-8")
    assert "import secrets" in source
    assert "secrets.randbits(32)" in source
    assert "import random" not in source
    assert "random." not in source


def test_rf2373_remaining_random_calls_are_marked_non_security() -> None:
    offenders: list[str] = []
    for rel in _TOUCHED_RANDOM_FILES:
        path = _ROOT / rel
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _RANDOM_CALL_RE.search(line) and "# nosec B311" not in line:
                offenders.append(f"{rel}:{line_no}")
    assert offenders == []
