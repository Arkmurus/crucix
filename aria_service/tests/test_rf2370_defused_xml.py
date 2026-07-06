"""R-F2370 — XML ingest paths use defusedxml, not stdlib ElementTree."""

from __future__ import annotations

import pytest
from defusedxml.common import EntitiesForbidden


_ENTITY_XML = b"""<?xml version="1.0"?>
<!DOCTYPE root [
  <!ENTITY local SYSTEM "file:///etc/passwd">
]>
<root>&local;</root>
"""


def test_rf2370_direct_ingest_parser_blocks_xml_entities() -> None:
    """Drive a real XML ingester parser and prove entity expansion is blocked."""
    from aria_service.intel import uk_ofsi_ingest

    with pytest.raises(EntitiesForbidden):
        uk_ofsi_ingest._parse_xml(_ENTITY_XML)


def test_rf2370_source_adapter_returns_empty_on_blocked_xml_entities() -> None:
    """Drive a real sanctions source adapter through its parse-error wrapper."""
    from aria_service.intel.sources import fcdo_sanctions

    assert fcdo_sanctions._parse_xml(_ENTITY_XML.decode("utf-8")) == []
