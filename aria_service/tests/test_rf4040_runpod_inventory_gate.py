"""R-F4040 capability tests for secure stock and price-gated pod creation."""
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


def test_real_creator_uses_only_live_secure_stock_below_price_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real creator must convert inventory evidence into its REST request."""
    requests = []
    responses = iter([
        _response({"data": {"gpuTypes": [
            {"id": "NVIDIA A40", "lowestPrice": {
                "stockStatus": "High", "uninterruptablePrice": 0.44}},
            {"id": "NVIDIA A100-SXM4-80GB", "lowestPrice": {
                "stockStatus": "Medium", "uninterruptablePrice": 1.59}},
            {"id": "NVIDIA L40S", "lowestPrice": {
                "stockStatus": "Low", "uninterruptablePrice": 0.99}},
            {"id": "NVIDIA H100 PCIe", "lowestPrice": {
                "stockStatus": "High", "uninterruptablePrice": 1.20}},
        ]}}),
        _response({"id": "secure-pod-id"}),
    ])

    def fake_urlopen(request, **kwargs):
        requests.append(request)
        return next(responses)

    monkeypatch.setattr(creator.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("ARIA_MAX_GPU_HOURLY_USD", "1.60")

    assert creator.create_pod("secret-token", "ssh-rsa public-key") == "secure-pod-id"
    assert requests[0].get_header("User-agent") == "aria-capacity-gate/1.0"
    body = json.loads(requests[1].data)
    assert body["gpuTypeIds"] == ["NVIDIA A40", "NVIDIA A100-SXM4-80GB"]
    assert body["gpuTypePriority"] == "availability"
    assert body["dataCenterPriority"] == "availability"
    assert body["cloudType"] == "SECURE"


def test_real_creator_fails_closed_without_acceptable_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def fake_urlopen(request, **kwargs):
        calls.append(request)
        return _response({"data": {"gpuTypes": [
            {"id": "NVIDIA A40", "lowestPrice": {
                "stockStatus": "Low", "uninterruptablePrice": 0.44}},
            {"id": "NVIDIA A100-SXM4-80GB", "lowestPrice": {
                "stockStatus": "High", "uninterruptablePrice": 1.61}},
        ]}})

    monkeypatch.setattr(creator.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("ARIA_MAX_GPU_HOURLY_USD", "1.60")

    with pytest.raises(RuntimeError, match="no approved High/Medium secure GPU"):
        creator.create_pod("secret-token", "ssh-rsa public-key")
    assert len(calls) == 1


def test_protected_recipe_pins_hourly_price_ceiling() -> None:
    launcher = open(
        "scripts/train/run_tooluse_protected_dpo_v1.sh", encoding="utf-8"
    ).read()
    assert "ARIA_MAX_GPU_HOURLY_USD=1.60" in launcher
