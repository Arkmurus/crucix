"""R-F4171 capability tests for evaluation-only Low-stock pod allocation."""
from __future__ import annotations

import io
import json

import pytest

from scripts.train import _create_v04_pod as creator


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def _response(payload: dict) -> _Response:
    return _Response(json.dumps(payload).encode())


def test_real_creator_allows_low_stock_only_for_explicit_evaluation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = []
    responses = iter([
        _response({"data": {"gpuTypes": [{
            "id": "NVIDIA A40",
            "lowestPrice": {"stockStatus": "Low", "uninterruptablePrice": 0.44},
        }]}}),
        _response({"id": "evaluation-pod"}),
    ])

    def fake_urlopen(request, **kwargs):
        requests.append(request)
        return next(responses)

    monkeypatch.setattr(creator.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("ARIA_MAX_GPU_HOURLY_USD", "1.60")
    monkeypatch.setenv("ARIA_EVALUATION_ONLY", "1")
    monkeypatch.setenv("ARIA_ALLOW_LOW_STOCK", "1")

    assert creator.create_pod("token", "public-key") == "evaluation-pod"
    body = json.loads(requests[1].data)
    assert body["gpuTypeIds"] == ["NVIDIA A40"]
    assert body["cloudType"] == "SECURE"


def test_low_stock_opt_in_is_rejected_for_training(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARIA_ALLOW_LOW_STOCK", "1")
    monkeypatch.delenv("ARIA_EVALUATION_ONLY", raising=False)
    with pytest.raises(RuntimeError, match="requires ARIA_EVALUATION_ONLY"):
        creator.create_pod("token", "public-key")
