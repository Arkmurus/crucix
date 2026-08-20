"""R-F4210 prevents SSE capability doubles drifting behind the real call."""

import pathlib

from ._source_probe import repo_path


def test_every_rf450_stream_double_accepts_speaker_name():
    """Every endpoint-driving fake must accept the real keyword argument."""
    source = pathlib.Path(
        repo_path("aria_service/tests/test_rf450_stream_footer_integration.py")
    ).read_text(encoding="utf-8")
    signatures = source.split("async def _fake_chat_stream(")[1:]

    assert len(signatures) == 3
    for signature in signatures:
        header = signature.split("):", 1)[0]
        assert "speaker_name" in header
    assert '"speaker_name"' in source
