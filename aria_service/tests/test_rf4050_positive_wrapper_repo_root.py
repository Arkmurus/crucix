"""R-F4050 capability coverage for immutable positive-wrapper repo routing."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_positive_wrapper_passes_repo_root_across_immutable_snapshot() -> None:
    wrapper = (
        ROOT / "scripts/train/run_tooluse_protected_positive_v1.sh"
    ).read_text(encoding="utf-8")
    immutable = (ROOT / "scripts/train/run_immutable_shell.sh").read_text(encoding="utf-8")
    driver = (ROOT / "scripts/train/run_tooluse_dpo.sh").read_text(encoding="utf-8")

    assert 'REPO="$ROOT"' in wrapper
    assert 'exec bash scripts/train/run_immutable_shell.sh' in wrapper
    assert 'SNAPSHOT=$(mktemp' in immutable
    assert 'REPO="${REPO:-' in driver
    assert 'grep -E \'^RUNPOD_API_KEY=\' .env' in driver
