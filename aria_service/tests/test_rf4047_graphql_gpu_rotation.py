"""R-F4047 capability coverage for GraphQL GPU-class rotation."""
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


def test_real_creator_rotates_past_stale_graphql_stock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rejected first stock class must not hide a deployable approved class."""
    requests = []
    responses = iter([
        _response({"data": {"gpuTypes": [
            {"id": "NVIDIA A40", "lowestPrice": {
                "stockStatus": "High", "uninterruptablePrice": 0.44}},
            {"id": "NVIDIA A100-SXM4-80GB", "lowestPrice": {
                "stockStatus": "Medium", "uninterruptablePrice": 1.59}},
        ]}}),
        _response({"errors": [{"message": "machine resources unavailable"}]}),
        _response({"data": {"podFindAndDeployOnDemand": {"id": "a100-pod"}}}),
    ])

    def fake_urlopen(request, **kwargs):
        requests.append(request)
        return next(responses)

    monkeypatch.setattr(creator.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("ARIA_POD_CREATE_API", "graphql")
    monkeypatch.setenv("ARIA_MAX_GPU_HOURLY_USD", "1.60")

    assert creator.create_pod("secret-token", "ssh-rsa public-key") == "a100-pod"
    mutations = [json.loads(request.data)["query"] for request in requests[1:]]
    assert 'gpuTypeId: "NVIDIA A40"' in mutations[0]
    assert 'gpuTypeId: "NVIDIA A100-SXM4-80GB"' in mutations[1]


def test_real_creator_reports_every_approved_graphql_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exhaustion must name each attempted approved class and fail closed."""
    responses = iter([
        _response({"data": {"gpuTypes": [
            {"id": "NVIDIA A40", "lowestPrice": {
                "stockStatus": "High", "uninterruptablePrice": 0.44}},
            {"id": "NVIDIA A100-SXM4-80GB", "lowestPrice": {
                "stockStatus": "Medium", "uninterruptablePrice": 1.59}},
        ]}}),
        _response({"errors": [{"message": "A40 unavailable"}]}),
        _response({"errors": [{"message": "A100 unavailable"}]}),
    ])

    monkeypatch.setattr(
        creator.urllib.request, "urlopen", lambda request, **kwargs: next(responses),
    )
    monkeypatch.setenv("ARIA_POD_CREATE_API", "graphql")
    monkeypatch.setenv("ARIA_MAX_GPU_HOURLY_USD", "1.60")

    with pytest.raises(RuntimeError) as exc_info:
        creator.create_pod("secret-token", "ssh-rsa public-key")
    detail = str(exc_info.value)
    assert "all approved GraphQL GPU placements rejected" in detail
    assert "NVIDIA A40" in detail
    assert "NVIDIA A100-SXM4-80GB" in detail
