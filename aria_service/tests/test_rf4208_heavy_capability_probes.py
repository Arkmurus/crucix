"""R-F4208 gates preventing heavy capability failures from becoming false skips."""

import asyncio
import inspect

from aria_service.tests import test_rf1666_doc_read_no_loop_block as pdf_tests
from aria_service.tests import test_rf1890_encode_offload as encode_tests
from aria_service.tests import test_rf2260_read_image as ocr_tests


def _skipif_marker(function):
    return next(
        marker for marker in function.pytestmark
        if marker.name == "skipif"
    )


def test_pdf_capability_skips_only_when_pymupdf_is_truly_absent():
    """The PDF test must use the central manifest-backed dependency contract."""
    marker = _skipif_marker(pdf_tests.test_vision_pdf_does_not_block_event_loop)

    assert len(marker.args) == 1 and isinstance(marker.args[0], bool)
    assert "PyMuPDF" in marker.kwargs["reason"]
    assert "ENVIRONMENT gap" in marker.kwargs["reason"]
    assert "pytest.skip" not in inspect.getsource(
        pdf_tests.test_vision_pdf_does_not_block_event_loop
    )


def test_installed_embedding_dependency_cannot_hide_a_broken_pool():
    """Once the dependency exists, offload startup failure must fail normally."""
    marker = _skipif_marker(
        encode_tests.test_real_offload_encodes_in_separate_process_and_matches_inprocess
    )
    source = inspect.getsource(
        encode_tests.test_real_offload_encodes_in_separate_process_and_matches_inprocess
    )

    assert len(marker.args) == 1 and isinstance(marker.args[0], bool)
    assert "sentence-transformers" in marker.kwargs["reason"]
    assert "pytest.skip" not in source
    assert "assert eo.is_enabled()" in source


def test_ocr_capability_probes_the_tesseract_binary_not_python_binding():
    """OCR availability depends on the executable, not just pytesseract."""
    marker = _skipif_marker(ocr_tests.test_read_image_tool_extracts_text)

    assert len(marker.args) == 1 and isinstance(marker.args[0], bool)
    assert "tesseract" in marker.kwargs["reason"]
    assert "executable" in marker.kwargs["reason"]
    assert "binding and the binary are separate" in marker.kwargs["reason"]


def test_encode_offload_status_distinguishes_every_process_state(monkeypatch):
    """The real diagnostic must not collapse unstarted and broken pools."""
    from aria_service.intel import encode_offload as eo

    monkeypatch.setattr(eo, "_ENABLED", True)
    monkeypatch.setattr(eo, "_pool", object())
    monkeypatch.setattr(eo, "_pool_broken", False)
    assert eo.get_status() == {
        "configured": True,
        "pool_started": True,
        "pool_broken": False,
        "enabled": True,
        "model": eo._MODEL_NAME,
    }

    monkeypatch.setattr(eo, "_pool_broken", True)
    broken = eo.get_status()
    assert broken["pool_started"] is True
    assert broken["pool_broken"] is True
    assert broken["enabled"] is False


def test_health_perf_exposes_serving_process_offload_state(monkeypatch):
    """Drive the real operator endpoint and prove it reads process-local state."""
    from aria_service.intel import encode_offload as eo
    from aria_service.routes.aria import health_perf_ep

    expected = {
        "configured": True,
        "pool_started": True,
        "pool_broken": False,
        "enabled": True,
        "model": "capability-test-model",
    }
    monkeypatch.setattr(eo, "get_status", lambda: expected)

    result = asyncio.run(health_perf_ep())

    assert result["embedding_offload"] == expected
    assert result["_schema_version"] == "rf4208.v1"
