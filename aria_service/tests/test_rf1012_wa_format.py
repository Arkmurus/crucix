"""R-F1012 — Tests for WhatsApp Professional Message Formatter."""
from __future__ import annotations

import pytest


class TestWhatsAppFormatter:
    """Test the WhatsApp message formatter."""

    def test_welcome(self):
        """Welcome message should be clean and professional."""
        from aria_service.intel.wa_formatter import WhatsAppFormatter
        msg = WhatsAppFormatter.welcome("John")
        assert "John" in msg
        assert "ARIA" in msg
        assert "sanctions" in msg
        assert "due diligence" in msg
        assert len(msg) < WhatsAppFormatter.MAX_LENGTH

    def test_sanctions_result_clear(self):
        """Clean sanctions result should be concise."""
        from aria_service.intel.wa_formatter import WhatsAppFormatter
        msg = WhatsAppFormatter.sanctions_result("ABC Corp", [])
        assert "Clear" in msg
        assert "ABC Corp" in msg
        assert len(msg) < WhatsAppFormatter.MAX_LENGTH

    def test_sanctions_result_matches(self):
        """Sanctions with matches should show details."""
        from aria_service.intel.wa_formatter import WhatsAppFormatter
        matches = [
            {"list": "OFAC SDN", "score": 0.95},
            {"list": "EU Consolidated", "score": 0.88},
        ]
        msg = WhatsAppFormatter.sanctions_result("ABC Corp", matches)
        assert "Match" in msg or "Match" in msg
        assert "OFAC SDN" in msg
        assert "ABC Corp" in msg

    def test_dd_result(self):
        """DD result should show risk level and findings."""
        from aria_service.intel.wa_formatter import WhatsAppFormatter
        msg = WhatsAppFormatter.dd_result(
            "XYZ Trading", 
            "Company registered in high-risk jurisdiction",
            "high",
            ["Shell company indicators", "PEP involvement"]
        )
        assert "XYZ Trading" in msg
        assert "HIGH" in msg
        assert "Shell company" in msg

    def test_research_result(self):
        """Research result should show findings."""
        from aria_service.intel.wa_formatter import WhatsAppFormatter
        msg = WhatsAppFormatter.research_result(
            "Sudan defence market",
            ["Growing demand for small arms", "Regional instability increasing"],
            15
        )
        assert "Sudan" in msg
        assert "15 sources" in msg
        assert "small arms" in msg

    def test_compliance_result(self):
        """Compliance result should show status."""
        from aria_service.intel.wa_formatter import WhatsAppFormatter
        msg = WhatsAppFormatter.compliance_result(
            "Night vision goggles",
            "restricted",
            ["UK ML6 controls apply", "Export licence required"]
        )
        assert "Night vision" in msg
        assert "RESTRICTED" in msg
        assert "Export licence" in msg

    def test_document_analysis(self):
        """Document analysis should show entities and risks."""
        from aria_service.intel.wa_formatter import WhatsAppFormatter
        msg = WhatsAppFormatter.document_analysis(
            "contract.pdf",
            ["Acme Corp", "John Smith"],
            ["Unusual payment terms", "Missing signatures"],
            "Standard procurement contract with anomalies"
        )
        assert "contract.pdf" in msg
        assert "Acme Corp" in msg
        assert "Unusual payment" in msg

    def test_error(self):
        """Error message should be polite."""
        from aria_service.intel.wa_formatter import WhatsAppFormatter
        msg = WhatsAppFormatter.error("Could not connect to sanctions database")
        assert "Unable to Process" in msg
        assert "try again" in msg

    def test_help(self):
        """Help message should list all capabilities."""
        from aria_service.intel.wa_formatter import WhatsAppFormatter
        msg = WhatsAppFormatter.help()
        assert "Screen" in msg
        assert "Research" in msg
        assert "Due Diligence" in msg
        assert "Documents" in msg

    def test_coder_progress(self):
        """Coder progress should show stage."""
        from aria_service.intel.wa_formatter import WhatsAppFormatter
        msg = WhatsAppFormatter.coder_progress("Fixing sanctions module", "writing_code")
        assert "Coding Update" in msg
        assert "Fixing sanctions" in msg

    def test_coder_completed(self):
        """Coder completed should show R-number."""
        from aria_service.intel.wa_formatter import WhatsAppFormatter
        msg = WhatsAppFormatter.coder_completed(1012, "Added new sanctions source", 3)
        assert "R-F1012" in msg
        assert "3" in msg
        assert "auto-deployed" in msg.lower()

    def test_truncate(self):
        """Long messages should be truncated."""
        from aria_service.intel.wa_formatter import WhatsAppFormatter
        long = "x" * 5000
        truncated = WhatsAppFormatter.truncate(long, max_length=100)
        assert len(truncated) <= 100

    def test_no_jargon(self):
        """Messages should not contain technical jargon."""
        from aria_service.intel.wa_formatter import WhatsAppFormatter
        jargon = ["async", "callback", "endpoint", "API", "JSON", "Redis", "SQLite", 
                  "chromadb", "asyncio", "websocket", "webhook"]
        
        messages = [
            WhatsAppFormatter.welcome(),
            WhatsAppFormatter.help(),
            WhatsAppFormatter.sanctions_result("Test", []),
            WhatsAppFormatter.error("Test error"),
        ]
        
        for msg in messages:
            for term in jargon:
                assert term.lower() not in msg.lower(), f"Jargon '{term}' found in message"
