"""R-F4306 / C-259 - the training drivers never retrieved the model they trained.

Found while preparing to regenerate aria-llm-v0.4-dpo, and it is the root cause
of the blocker that made the regeneration necessary in the first place.

`run_v04_dpo_cycle.sh` scp's five things ONTO the pod (driver, scripts, SFT
corpus, DPO pairs, eval set) and pulls exactly one thing back: the eval REPORT.
A grep for `adapter`, `checkpoint`, `.tgz` or `tar` in the driver returns
nothing. The trained LoRA is written to `/workspace/checkpoints/` on the pod, the
driver then STOPS the pod (`trap stop_pod EXIT`), and the weights exist in
exactly one place - tied to one pod in one datacenter.

THAT IS PRECISELY HOW aria-llm-v0.4-dpo BECAME UNREACHABLE. Its weights live only
on a network volume in US-KS-2; when that datacenter ran out of allocatable
capacity - GPU and CPU alike - the model could not be served, evacuated or even
inspected, at any price. The eval report survived because the driver pulls it;
the model did not, because the driver does not.

So a cycle that ends without the adapter in hand has not delivered a model. It
has delivered a claim about a model, plus a machine that might not exist
tomorrow.

WHAT THIS FIXES AND WHAT IT DOES NOT. Retrieval makes the artefact portable: it
can be served from any datacenter with capacity, re-uploaded, hashed, and
attributed to the inputs that produced it (section 24's manifest). It does not
make the pod durable, and it is not a backup of /workspace - it is the one file
that matters, brought home.

FAILURE MUST BE LOUD. A silent retrieval failure would leave the operator
believing they hold a model they do not, which is worse than an obvious error -
the cycle would report a verdict while the thing the verdict describes was being
deleted with the pod.
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]

#: Drivers that train something and therefore must bring it home.
_DRIVERS = (
    "scripts/train/run_v04_dpo_cycle.sh",
)


def _text(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8", errors="replace")


def test_the_driver_retrieves_the_adapter() -> None:
    """THE CAPABILITY TEST. Without this the cycle produces a verdict about a
    model that is about to be stopped along with its only copy."""
    for rel in _DRIVERS:
        src = _text(rel)
        assert re.search(r"adapter|checkpoints", src), (
            f"{rel} never mentions the adapter it trains - the weights stay on "
            "the pod and die with it (this is exactly how v0.4-dpo was lost)")
        # It must actually COPY it back, not merely name it. Matched across a
        # window rather than one line: the real command uses a shell line
        # continuation, and the first version of this check demanded a single
        # line and so went red on a correct fix.
        pulls_back = any(
            "root@" in src[i:i + 400]
            and re.search(r"adapter|checkpoint|[.]tgz", src[i:i + 400])
            for i in (m.start() for m in re.finditer(r"\bscp\b", src))
        )
        assert pulls_back, (
            f"{rel} names the adapter but never scp's it back from the pod")


def test_retrieval_happens_before_the_pod_is_stopped() -> None:
    """`trap stop_pod EXIT` fires on exit. Retrieval placed after the stop, or
    left to the trap, races a pod that is already going away."""
    for rel in _DRIVERS:
        src = _text(rel)
        pull = src.find("ADAPTER_TGZ")
        assert pull != -1, f"{rel} has no adapter retrieval step"
        # the explicit stop_pod call at the end of the happy path
        tail = src.rfind("stop_pod")
        assert pull < tail, (
            f"{rel} retrieves the adapter AFTER stopping the pod")


def test_a_failed_retrieval_is_reported_not_swallowed() -> None:
    """A silent failure leaves the operator believing they hold a model they do
    not - worse than a loud error, because the verdict still prints."""
    for rel in _DRIVERS:
        src = _text(rel)
        i = src.find("ADAPTER_TGZ")
        window = src[i:i + 1400]
        assert re.search(r"NOT RETRIEVED|FATAL|WARN", window), (
            f"{rel} does not report a failed adapter retrieval")


def test_the_local_destination_is_the_checkpoints_dir() -> None:
    """Land it beside the other checkpoints so the manifest and the corpus
    tooling can see it, rather than in a scratch path nothing indexes."""
    for rel in _DRIVERS:
        assert "data/training/checkpoints" in _text(rel), (
            f"{rel} does not place the adapter in data/training/checkpoints")


def test_the_driver_still_pulls_the_eval_report() -> None:
    """The existing behaviour must survive - the verdict is still the point."""
    for rel in _DRIVERS:
        assert "aria_llm_v0_4_dpo_eval.json" in _text(rel)
