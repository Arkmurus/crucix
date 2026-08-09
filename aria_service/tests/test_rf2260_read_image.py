"""Capability test for R-F2260: read_image tool."""
import pathlib
import sys

import pytest

# R-F3795 — OCR shells out to the `tesseract` EXECUTABLE. pytesseract (the
# binding) is installed here while the binary is not, so a module probe would
# wrongly call this satisfied. ENVIRONMENT gap, not a code defect.
from ._env_probe import requires_binary

# Ensure tesseract is in PATH for Windows
if sys.platform == "win32":
    tesseract_paths = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ]
    for tp in tesseract_paths:
        if pathlib.Path(tp).exists():
            import pytesseract
            pytesseract.pytesseract.tesseract_cmd = tp
            break


def _make_test_image(text: str = "Hello World 123") -> bytes:
    """Create a small PNG image with the given text for testing."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        pytest.skip("Pillow not installed")
    img = Image.new("RGB", (200, 50), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 16)
    except Exception:
        font = ImageFont.load_default()
    draw.text((10, 15), text, fill=(0, 0, 0), font=font)
    import io
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@requires_binary("tesseract")
def test_read_image_tool_extracts_text():
    """Capability: read_image() extracts text from a PNG image."""
    from aria_cli.coder_tools import CoderToolbox
    from aria_cli.tools import Toolbox, WriteGuard

    guard = WriteGuard(pathlib.Path("."))
    base = Toolbox(root=pathlib.Path("."), guard=guard)
    tb = CoderToolbox(toolbox=base)

    # Create a temp image file
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(_make_test_image("ARIA OCR Test 456"))
        tmp_path = f.name

    try:
        result = tb.read_image(tmp_path)
        assert not result.is_error, f"Expected success, got error: {result.output}"
        assert "ARIA OCR Test 456" in result.output, (
            f"Expected 'ARIA OCR Test 456' in output, got: {result.output}"
        )
    finally:
        pathlib.Path(tmp_path).unlink(missing_ok=True)


def test_read_image_file_not_found():
    """Capability: read_image() returns error for missing file."""
    from aria_cli.coder_tools import CoderToolbox
    from aria_cli.tools import Toolbox, WriteGuard

    guard = WriteGuard(pathlib.Path("."))
    base = Toolbox(root=pathlib.Path("."), guard=guard)
    tb = CoderToolbox(toolbox=base)

    result = tb.read_image("nonexistent_file.png")
    assert result.is_error
    assert "not found" in result.output.lower()


def test_read_image_unsupported_format():
    """Capability: read_image() returns error for unsupported format."""
    from aria_cli.coder_tools import CoderToolbox
    from aria_cli.tools import Toolbox, WriteGuard

    guard = WriteGuard(pathlib.Path("."))
    base = Toolbox(root=pathlib.Path("."), guard=guard)
    tb = CoderToolbox(toolbox=base)

    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
        f.write(b"<svg></svg>")
        tmp_path = f.name

    try:
        result = tb.read_image(tmp_path)
        assert result.is_error
        assert "unsupported" in result.output.lower()
    finally:
        pathlib.Path(tmp_path).unlink(missing_ok=True)
