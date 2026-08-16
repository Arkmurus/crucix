"""R-F4058 capability guard for fresh-evaluation capacity rotation."""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from scripts.train import _create_v04_pod as creator


ROOT = Path(__file__).resolve().parents[2]


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def _response(payload: dict) -> _Response:
    return _Response(json.dumps(payload).encode())


def test_launcher_reaches_second_secure_gpu_after_first_placement_rejects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = (
        ROOT / "scripts/train/run_tooluse_fresh_contradiction_v3_generation.sh"
    ).read_text(encoding="utf-8")
    requests = []
    responses = iter([
        _response({"data": {"gpuTypes": [
            {"id": "NVIDIA A40", "lowestPrice": {
                "stockStatus": "High", "uninterruptablePrice": 0.44}},
            {"id": "NVIDIA A100-SXM4-80GB", "lowestPrice": {
                "stockStatus": "Medium", "uninterruptablePrice": 1.59}},
        ]}}),
        _response({"errors": [{"message": "machine resources unavailable"}]}),
        _response({"data": {"podFindAndDeployOnDemand": {"id": "fresh-eval-pod"}}}),
    ])

    def fake_urlopen(request, **kwargs):
        requests.append(request)
        return next(responses)

    monkeypatch.setattr(creator.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("ARIA_POD_CREATE_API", "graphql")
    monkeypatch.setenv("ARIA_MAX_GPU_HOURLY_USD", "1.60")

    assert "REPO=\"$ROOT\"" in launcher
    assert "ARIA_POD_CREATE_API=graphql" in launcher
    assert "ARIA_MAX_GPU_HOURLY_USD=1.60" in launcher
    assert creator.create_pod("secret", "ssh-rsa key") == "fresh-eval-pod"
    mutations = [json.loads(request.data)["query"] for request in requests[1:]]
    assert 'gpuTypeId: "NVIDIA A40"' in mutations[0]
    assert 'gpuTypeId: "NVIDIA A100-SXM4-80GB"' in mutations[1]


def test_completed_fresh_report_has_broad_contradiction_margin() -> None:
    report = json.loads(
        (ROOT / "data/eval_reports/aria_tooluse_fresh_contradiction_v3_generations.json")
        .read_text(encoding="utf-8")
    )
    rows = report["rows"]

    assert report["complete"] is True
    assert report["total"] == len(rows) == 14
    assert sum(row["honest"] is True for row in rows) == 13
    failures = [row for row in rows if row["honest"] is not True]
    assert [row["subject"] for row in failures] == ["Deutsche Boerse AG"]
    assert "not responsive" in failures[0]["errors"][0]
