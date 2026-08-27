"""R-F4373 (C-318) — a training pod must say what it is for.

Every pod `_create_v04_pod` has ever made was called "aria-v04-train", so the
fleet is a list of identical names and nothing in it says which run owns which
pod.

That is not cosmetic. R-F4241's doctrine is "reuse, do not accumulate", so a
SECOND identically-named pod reads as an abandoned stray. On 2026-08-26 a
coder-training pod was stopped 45 minutes into a paid run — provider record
"Exited by user: Wed Aug 26 2026 22:30:28" — while its own self-stop watchdog
still had 3.75 hours on the clock, the cycle log was healthy, and the baseline
evaluation was in progress. Nothing was wrong with the run. It was correctly
identified as a stray by a policy that had no way to tell it apart.

A pod cannot defend itself from a correct policy applied to a wrong
identification. Naming it after the work is what lets the policy distinguish
them.

BOTH create paths are covered. The GraphQL mutation and the REST payload each
carried the literal separately, so fixing one would leave the other silently
producing the ambiguous name — the half-applied fix this repo keeps recording.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.train import _create_v04_pod as C  # noqa: E402

SOURCE = pathlib.Path(C.__file__).read_text(encoding="utf-8")


def test_the_default_name_is_unchanged(monkeypatch):
    """Every existing launcher must keep the name it has always used — this is
    additive, not a rename of the fleet."""
    monkeypatch.delenv("ARIA_POD_NAME", raising=False)
    assert C.pod_name() == "aria-v04-train"


def test_the_name_can_say_what_the_pod_is_for(monkeypatch):
    monkeypatch.setenv("ARIA_POD_NAME", "aria-coder-sft-R-F4372")
    assert C.pod_name() == "aria-coder-sft-R-F4372"


def test_an_empty_name_falls_back_rather_than_creating_an_unnamed_pod(monkeypatch):
    """An unnamed pod is worse than an ambiguously-named one: it cannot be
    identified at all."""
    monkeypatch.setenv("ARIA_POD_NAME", "   ")
    assert C.pod_name() == "aria-v04-train"


def test_both_create_paths_use_the_helper():
    """THE HALF-APPLIED-FIX GUARD. The GraphQL mutation and the REST payload
    each carried the literal name separately. A fix to one leaves the other
    producing the ambiguous name, and the failure is invisible until a pod is
    stopped mid-run."""
    import ast

    # AST, not line filtering. The first version stripped `#` comments and then
    # counted the name inside pod_name()'s own DOCSTRING as a live literal — a
    # line heuristic reading prose as code, which is the class of mistake this
    # repo has already fixed twice (R-F3597, R-F3858).
    tree = ast.parse(SOURCE)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                docstrings.add(doc)
    literals = [n for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and n.value == "aria-v04-train"
                and n.value not in docstrings]

    # The literal may survive ONLY as the fallback inside pod_name().
    assert len(literals) == 1, (
        f"{len(literals)} hardcoded pod name(s) remain — every create path must "
        f"call pod_name(), or the fix is half-applied")
    assert SOURCE.count("pod_name()") >= 2, "a create path still names the pod itself"


def test_the_helper_is_used_where_the_pod_is_actually_created():
    """Naming a variable is not naming the pod: assert the helper reaches the
    provider payloads, not merely that it exists."""
    assert "name: {json.dumps(pod_name())}" in SOURCE, "GraphQL path unnamed"
    assert '"name": pod_name(),' in SOURCE, "REST path unnamed"
