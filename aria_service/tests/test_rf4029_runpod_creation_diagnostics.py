"""R-F4029 capability tests for honest RunPod creation diagnostics."""
from __future__ import annotations

import io
import urllib.error

import pytest

from scripts.train import _create_v04_pod as creator


def test_create_pod_surfaces_provider_http_rejection(monkeypatch: pytest.MonkeyPatch) -> None:
    error = urllib.error.HTTPError(
        creator.urllib.request.Request("https://example.invalid").full_url,
        400,
        "Bad Request",
        {},
        io.BytesIO(b'{"error":"secure cloud capacity unavailable"}'),
    )
    monkeypatch.setattr(creator.urllib.request, "urlopen", lambda *args, **kwargs: (_ for _ in ()).throw(error))

    with pytest.raises(RuntimeError, match="HTTP 400.*secure cloud capacity unavailable"):
        creator.create_pod("secret-token", "ssh-rsa public-key")


def test_generation_driver_records_create_stderr() -> None:
    source = open("scripts/train/run_tooluse_generation.sh", encoding="utf-8").read()
    assert '2>"$CREATE_ERR"' in source
    assert "${CREATE_DETAIL:-no diagnostic}" in source
