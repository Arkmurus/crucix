"""R-F4142 keeps the hash-pinned resolution DPO artifact platform-stable."""
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ARTIFACT = ROOT / "data/training/aria_tooluse_resolution_boundary_dpo_v1.jsonl"
MANIFEST = ROOT / "data/eval_reports/aria_tooluse_resolution_boundary_dpo_v1_manifest.json"


def test_checked_out_artifact_bytes_match_the_pinned_manifest() -> None:
    raw = ARTIFACT.read_bytes()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert b"\r\n" not in raw
    assert hashlib.sha256(raw).hexdigest() == manifest["output_sha256"]


def test_gitattributes_pins_the_training_artifact_to_lf() -> None:
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert (
        "data/training/aria_tooluse_resolution_boundary_dpo_v1.jsonl text eol=lf"
        in attributes.splitlines()
    )
