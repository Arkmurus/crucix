"""R-F1437 — Capability test: _read_docx strips tracked-change deletions, keeps insertions.

Builds a .docx WITH tracked changes (w:del + w:ins) using raw OOXML, runs it
through _read_docx, and asserts:
1. Deleted text (w:delText) is ABSENT from the output
2. Inserted text (w:ins runs) is PRESENT in the output
3. Normal text (no tracked change) is PRESENT in the output

This drives the REAL _read_docx path (not a helper) per CLAUDE.md §23.
"""
import os
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from aria_service.intel.document_reader import _read_docx


def _make_docx_with_tracked_changes(filepath: str) -> None:
    """Create a .docx with tracked changes using raw OOXML.

    The document contains:
    - Normal text: "This is normal text."
    - A tracked deletion: "This text was deleted." (w:delText)
    - A tracked insertion: "This text was inserted." (w:ins)
    - More normal text: "This is also normal."
    """
    ooxml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
            xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <w:body>
    <w:p>
      <w:r>
        <w:t>This is a contract agreement between Party A and Party B for the provision of services.</w:t>
      </w:r>
    </w:p>
    <w:p>
      <w:del w:id="1" w:author="Reviewer" w:date="2026-06-08T00:00:00Z">
        <w:r>
          <w:delText xml:space="preserve">This clause has been deleted from the agreement and should not appear.</w:delText>
        </w:r>
      </w:del>
    </w:p>
    <w:p>
      <w:ins w:id="2" w:author="Reviewer" w:date="2026-06-08T00:00:00Z">
        <w:r>
          <w:t>This clause was inserted as a replacement and is the current live term.</w:t>
        </w:r>
      </w:ins>
    </w:p>
    <w:p>
      <w:r>
        <w:t>All parties agree to the terms and conditions set forth in this agreement.</w:t>
      </w:r>
    </w:p>
  </w:body>
</w:document>"""

    with zipfile.ZipFile(filepath, "w") as zf:
        zf.writestr("word/document.xml", ooxml.encode("utf-8"))
        # Minimal required parts for a valid .docx
        zf.writestr("[Content_Types].xml", """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="xml" ContentType="application/xml"/>
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>""")
        zf.writestr("word/_rels/document.xml.rels", """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="/word/document.xml"/>
</Relationships>""")
        zf.writestr("_rels/.rels", """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="/word/document.xml"/>
</Relationships>""")


def test_rf1437_read_docx_strips_deleted_text():
    """Deleted text (w:delText) must be ABSENT from _read_docx output."""
    with tempfile.TemporaryDirectory() as tmp:
        docx_path = os.path.join(tmp, "test_tracked_changes.docx")
        _make_docx_with_tracked_changes(docx_path)

        result = _read_docx(docx_path)

        assert result is not None, "_read_docx should return a result"
        assert result.text, "_read_docx should return non-empty text"
        assert result.confidence > 0.5, (
            f"confidence should be >0.5, got {result.confidence}"
        )

        text = result.text
        # Normal text should be present
        assert "Party A" in text, (
            f"Normal text should be present in output: {text!r}"
        )
        assert "terms and conditions" in text, (
            f"Normal text should be present in output: {text!r}"
        )

        # Inserted text should be present
        assert "inserted as a replacement" in text, (
            f"Inserted text should be PRESENT in output: {text!r}"
        )

        # Deleted text must be ABSENT
        assert "deleted from the agreement" not in text, (
            f"Deleted text should be ABSENT from output: {text!r}"
        )


def test_rf1437_read_docx_preserves_normal_text():
    """Normal text (no tracked changes) must survive intact."""
    with tempfile.TemporaryDirectory() as tmp:
        docx_path = os.path.join(tmp, "test_normal.docx")
        # Simple docx with no tracked changes
        ooxml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
            xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <w:body>
    <w:p>
      <w:r>
        <w:t>This is a contract between Party A and Party B.</w:t>
      </w:r>
    </w:p>
    <w:p>
      <w:r>
        <w:t>Clause 1: Definitions.</w:t>
      </w:r>
    </w:p>
    <w:p>
      <w:r>
        <w:t>Clause 2: Payment Terms.</w:t>
      </w:r>
    </w:p>
  </w:body>
</w:document>"""
        with zipfile.ZipFile(docx_path, "w") as zf:
            zf.writestr("word/document.xml", ooxml.encode("utf-8"))
            zf.writestr("[Content_Types].xml", """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="xml" ContentType="application/xml"/>
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>""")
            zf.writestr("word/_rels/document.xml.rels", """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="/word/document.xml"/>
</Relationships>""")
            zf.writestr("_rels/.rels", """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="/word/document.xml"/>
</Relationships>""")

        result = _read_docx(docx_path)
        assert result is not None, "_read_docx should return a result"
        assert "Party A" in result.text, "Normal text should survive"
        assert "Clause 1" in result.text, "Normal text should survive"
        assert "Clause 2" in result.text, "Normal text should survive"
