"""R-F4041 capability tests for stock-selected GraphQL pod creation."""
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


def test_real_creator_uses_stock_selected_graphql_without_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = []
    responses = iter([
        _response({"data": {"gpuTypes": [
            {"id": "NVIDIA A40", "lowestPrice": {
                "stockStatus": "High", "uninterruptablePrice": 0.44}},
            {"id": "NVIDIA A100-SXM4-80GB", "lowestPrice": {
                "stockStatus": "Medium", "uninterruptablePrice": 1.59}},
        ]}}),
        _response({"data": {"podFindAndDeployOnDemand": {"id": "graphql-pod"}}}),
    ])

    def fake_urlopen(request, **kwargs):
        requests.append(request)
        return next(responses)

    monkeypatch.setattr(creator.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("ARIA_POD_CREATE_API", "graphql")
    monkeypatch.setenv("ARIA_MAX_GPU_HOURLY_USD", "1.60")

    assert creator.create_pod("secret-token", "ssh-rsa public-key") == "graphql-pod"
    assert len(requests) == 2
    mutation = json.loads(requests[1].data)["query"]
    assert "podFindAndDeployOnDemand" in mutation
    assert 'gpuTypeId: "NVIDIA A40"' in mutation
    assert "cloudType: SECURE" in mutation
    assert "containerDiskInGb: 120" in mutation
    assert "PUBLIC_KEY" in mutation
    assert all(request.full_url.startswith("https://api.runpod.io/graphql?") for request in requests)


def test_protected_recipe_pins_graphql_creation_mode() -> None:
    launcher = open(
        "scripts/train/run_tooluse_protected_dpo_v1.sh", encoding="utf-8"
    ).read()
    assert "ARIA_POD_CREATE_API=graphql" in launcher
