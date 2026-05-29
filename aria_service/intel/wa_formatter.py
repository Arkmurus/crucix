"""R-F1012 — ARIA WhatsApp Professional Message Formatter.

Clean, professional, jargon-free messaging for WhatsApp.
Consistent templates for all message types.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger("aria.wa_formatter")


class WhatsAppFormatter:
    """Professional WhatsApp message formatting.
    
    Principles:
    - Clean, minimal, professional
    - No technical jargon
    - Consistent structure per message type
    - Relevant information only
    - Proper WhatsApp markdown (*bold*, _italic_, ~strikethrough~)
    """

    # Max WhatsApp message length
    MAX_LENGTH = 4096

    @staticmethod
    def header(emoji: str, title: str) -> str:
        """Format a message header."""
        return f"{emoji} *{title}*"

    @staticmethod
    def section(title: str, content: str) -> str:
        """Format a section."""
        return f"\n*{title}*:\n{content}"

    @staticmethod
    def bullet(text: str) -> str:
        """Format a bullet point."""
        return f"• {text}"

    @staticmethod
    def key_value(key: str, value: str) -> str:
        """Format a key-value pair."""
        return f"_{key}_: {value}"

    @staticmethod
    def bold(text: str) -> str:
        return f"*{text}*"

    @staticmethod
    def italic(text: str) -> str:
        return f"_{text}_"

    @staticmethod
    def code(text: str) -> str:
        return f"`{text}`"

    @classmethod
    def truncate(cls, text: str, max_length: int = MAX_LENGTH) -> str:
        """Truncate message to max length."""
        if len(text) <= max_length:
            return text
        return text[:max_length-3] + "..."

    # ── Message Templates ────────────────────────────────────────────────

    @classmethod
    def welcome(cls, user_name: str = "") -> str:
        """Welcome message for new users."""
        greeting = f"Hello {user_name}" if user_name else "Hello"
        return (
            f"{greeting} 👋\n\n"
            f"I'm ARIA — your defence intelligence assistant.\n\n"
            f"Here's what I can help you with:\n"
            f"{cls.bullet('Screen entities against global sanctions lists')}\n"
            f"{cls.bullet('Run due diligence on companies and individuals')}\n"
            f"{cls.bullet('Research defence markets and opportunities')}\n"
            f"{cls.bullet('Analyse documents and extract intelligence')}\n"
            f"{cls.bullet('Check export compliance and regulations')}\n\n"
            f"Just tell me what you need. For example:\n"
            f"{cls.italic('Screen ABC Corp for sanctions')}\n"
            f"{cls.italic('What is the risk in Sudan?')}\n"
            f"{cls.italic('Run DD on XYZ Trading')}"
        )

    @classmethod
    def sanctions_result(cls, name: str, matches: list[dict]) -> str:
        """Sanctions screening result."""
        if not matches:
            return (
                f"{cls.header('✅', 'Sanctions Screen: Clear')}\n\n"
                f"No sanctions matches found for {cls.bold(name)}.\n\n"
                f"_{cls.italic('This is not a guarantee of clean status. '
                              'Always conduct full due diligence.')}_"
            )
        
        lines = [cls.header('🚩', f'Sanctions Screen: {len(matches)} Match(es)')]
        lines.append(f"\nEntity: {cls.bold(name)}\n")
        
        for m in matches[:5]:
            list_name = m.get("list", m.get("source", "Unknown"))
            score = m.get("score", m.get("confidence", "?"))
            lines.append(f"{cls.bullet(f'{list_name} (score: {score})')}")
        
        if len(matches) > 5:
            lines.append(f"\n... and {len(matches) - 5} more matches")
        
        lines.append(f"\n_{cls.italic('Further investigation recommended.')}_")
        return cls.truncate("\n".join(lines))

    @classmethod
    def dd_result(cls, entity: str, summary: str, risk_level: str, key_findings: list[str]) -> str:
        """Due diligence result."""
        risk_emoji = {"low": "🟢", "medium": "🟡", "high": "🟠", "critical": "🔴"}
        emoji = risk_emoji.get(risk_level.lower(), "⚪")
        
        lines = [cls.header(emoji, f'Due Diligence: {entity}')]
        lines.append(f"\nRisk Level: {cls.bold(risk_level.upper())}")
        lines.append(f"\n{summary}\n")
        
        if key_findings:
            lines.append(f"*Key Findings*:")
            for finding in key_findings[:5]:
                lines.append(cls.bullet(finding))
        
        return cls.truncate("\n".join(lines))

    @classmethod
    def research_result(cls, topic: str, findings: list[str], sources: int) -> str:
        """Research result."""
        lines = [cls.header('📊', f'Research: {topic}')]
        lines.append(f"\n*Findings* ({sources} sources analysed):\n")
        
        for finding in findings[:5]:
            lines.append(cls.bullet(finding))
        
        if len(findings) > 5:
            lines.append(f"\n... and {len(findings) - 5} more findings")
        
        return cls.truncate("\n".join(lines))

    @classmethod
    def compliance_result(cls, item: str, status: str, restrictions: list[str]) -> str:
        """Compliance check result."""
        status_emoji = {"clear": "✅", "restricted": "🟡", "prohibited": "🔴", "amber": "🟡"}
        emoji = status_emoji.get(status.lower(), "⚪")
        
        lines = [cls.header(emoji, f'Compliance Check: {item}')]
        lines.append(f"\nStatus: {cls.bold(status.upper())}\n")
        
        if restrictions:
            lines.append(f"*Restrictions*:")
            for r in restrictions[:5]:
                lines.append(cls.bullet(r))
        
        return cls.truncate("\n".join(lines))

    @classmethod
    def document_analysis(cls, filename: str, entities: list[str], risks: list[str], summary: str) -> str:
        """Document analysis result."""
        lines = [cls.header('📄', f'Document Analysis: {filename}')]
        lines.append(f"\n{summary}\n")
        
        if entities:
            lines.append(f"*Entities Found*:")
            for e in entities[:5]:
                lines.append(cls.bullet(e))
        
        if risks:
            lines.append(f"\n*Risks Identified*:")
            for r in risks[:5]:
                lines.append(cls.bullet(r))
        
        return cls.truncate("\n".join(lines))

    @classmethod
    def error(cls, message: str) -> str:
        """Error message."""
        return (
            f"{cls.header('⚠️', 'Unable to Process')}\n\n"
            f"{message}\n\n"
            f"_{cls.italic('Please try again or rephrase your request.')}_"
        )

    @classmethod
    def progress(cls, action: str, status: str) -> str:
        """Progress update."""
        return f"⏳ {action}... _{status}_"

    @classmethod
    def help(cls) -> str:
        """Help message."""
        return (
            f"{cls.header('ℹ️', 'How to use ARIA')}\n\n"
            f"*Screening*:\n"
            f"{cls.bullet('Screen [name] for sanctions')}\n"
            f"{cls.bullet('Check [company] compliance status')}\n\n"
            f"*Research*:\n"
            f"{cls.bullet('Research [topic/market/country]')}\n"
            f"{cls.bullet('What is the risk in [country]?')}\n\n"
            f"*Due Diligence*:\n"
            f"{cls.bullet('Run DD on [entity name]')}\n"
            f"{cls.bullet('Investigate [company]')}\n\n"
            f"*Documents*:\n"
            f"{cls.bullet('Analyse this document: [content]')}\n"
            f"{cls.bullet('Extract entities from: [content]')}\n\n"
            f"*General*:\n"
            f"{cls.bullet('Ask me anything about defence intelligence')}"
        )

    @classmethod
    def coder_progress(cls, description: str, stage: str) -> str:
        """Coder progress update."""
        stage_emoji = {
            "starting": "🔄",
            "planning": "📋",
            "writing_code": "✏️",
            "writing_tests": "🧪",
            "testing": "🔬",
            "staging": "📦",
            "deploying": "🚀",
            "completed": "✅",
            "failed": "❌",
        }
        emoji = stage_emoji.get(stage, "🔄")
        return f"{emoji} *Coding Update*: {description}"

    @classmethod
    def coder_completed(cls, r_number: int, description: str, files_changed: int) -> str:
        """Coder completion message."""
        return (
            f"{cls.header('✅', f'Coding Complete (R-F{r_number})')}\n\n"
            f"{description}\n\n"
            f"Files changed: {files_changed}\n"
            f"Auto-deployed via CI/CD"
        )

    @classmethod
    def coder_failed(cls, r_number: int, description: str, reason: str) -> str:
        """Coder failure message."""
        return (
            f"{cls.header('❌', f'Coding Failed (R-F{r_number})')}\n\n"
            f"{description}\n\n"
            f"Reason: {reason}"
        )

# R-F1012 - wire to brain
from .engine_wiring import wire_success
wire_success(module="wa_formatter", summary="WA Formatter Active", source_id="wa_formatter:R-F1012")
