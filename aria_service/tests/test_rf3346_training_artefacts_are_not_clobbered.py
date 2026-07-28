"""R-F3346 — the curated training artefacts must survive a test run.

R-F1941 replaced a 74-line distillation toy with a real grounded-SFT trainer and
fixed the config it reads. It was reverted in June (commit 6fe94c43), restored by
R-F3336 — and then reverted AGAIN, in-session, by a single `pytest
aria_service/tests/` run.

The writer, not the sweep, is the cause. Two modules generate a training script
from a hardcoded template straight onto the curated path:

    llm_builder.py  ~line 206   (given a `root` seam by R-F3291)
    llm_pipeline.py ~line 232   (had none until R-F3346)

R-F3291 fixed half of this and the other half kept clobbering the file. These
tests exist so the NEXT generator added to this tree is caught by a failing test
rather than by someone noticing a 155-line file became 74 lines.
"""
from __future__ import annotations

import pathlib

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[2]
_TRAINING = _REPO / "data" / "training"


def _classes():
    from aria_service.intel.llm_builder import LLMBuilder
    from aria_service.intel.llm_pipeline import LLMTrainingPipeline
    return [LLMBuilder, LLMTrainingPipeline]


@pytest.mark.parametrize("idx", [0, 1])
def test_rf3346_generators_accept_a_root_and_honour_it(idx, tmp_path):
    """Both generators must be divertible; a hardcoded root cannot be tested."""
    cls = _classes()[idx]
    inst = cls(root=tmp_path)
    assert inst.data_dir == tmp_path / "data" / "training", (
        f"{cls.__name__} ignored the injected root — it will write into the repo"
    )
    assert _TRAINING not in inst.data_dir.parents and inst.data_dir != _TRAINING


def test_rf3346_generating_a_script_does_not_touch_the_curated_trainer(tmp_path):
    """The capability test: RUN the generator and prove the real file is intact.

    This is the check that would have caught the recurrence. It drives the actual
    write path rather than asserting the seam exists, because a seam that callers
    bypass protects nothing.
    """
    from aria_service.intel.llm_pipeline import LLMTrainingPipeline

    curated = _TRAINING / "train_aria_llm.py"
    before = curated.read_text(encoding="utf-8") if curated.exists() else None

    pipeline = LLMTrainingPipeline(root=tmp_path)
    config = pipeline._prepare_config("test-model", 100)
    pipeline._generate_training_script("test-model", config)

    # the generated artefacts landed in the disposable root...
    assert (tmp_path / "data" / "training" / "train_aria_llm.py").is_file()
    assert (tmp_path / "data" / "training" / "training_config.json").is_file()

    # ...and the curated one is byte-identical.
    if before is not None:
        assert curated.read_text(encoding="utf-8") == before, (
            "the generator overwrote the curated grounded trainer — this is the "
            "R-F1941 revert, reproduced"
        )


def test_rf3346_the_curated_trainer_is_the_grounded_one():
    """A second line of defence, independent of who did the writing.

    The toy is a flat script with no functions; the real trainer exposes the data
    pipeline its own tests drive. Shape, not content, so a legitimate edit to the
    trainer does not fail here.
    """
    curated = _TRAINING / "train_aria_llm.py"
    if not curated.is_file():
        pytest.skip("no trainer checked out")
    src = curated.read_text(encoding="utf-8")
    assert "def load_corpus" in src and "def split" in src, (
        "data/training/train_aria_llm.py has lost its data pipeline — a generator "
        "has overwritten it with the distillation template. Restore from git; do "
        "not rewrite it by hand."
    )
