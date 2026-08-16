"""R-F4044 capability coverage for the DPO status probe on Windows."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_dpo_pod_status_uses_real_curl_binary() -> None:
    source = (ROOT / "scripts/train/run_tooluse_dpo.sh").read_text(encoding="utf-8")
    assert 'PD=$(curl.exe -s "$API/pods/$POD_ID"' in source
    assert 'PD=$(curl -s "$API/pods/$POD_ID"' not in source
