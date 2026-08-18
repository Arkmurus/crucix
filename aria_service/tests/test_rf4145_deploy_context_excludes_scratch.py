"""R-F4145 capability guard for reproducible Fly build contexts."""
from pathlib import Path


def test_local_scratch_tree_is_excluded_from_docker_build_context() -> None:
    rules = Path(".dockerignore").read_text(encoding="utf-8").splitlines()

    ephemeral_trees = {
        ".scratch/",
        ".pytest_cache/",
        "data/eval_reports/pytest-*/",
        "data/training/pytest_tmp_*/",
    }
    assert ephemeral_trees <= set(rules)
    assert not {f"!{rule}" for rule in ephemeral_trees} & set(rules)
