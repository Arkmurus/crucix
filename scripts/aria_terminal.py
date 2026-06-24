"""
ARIA Terminal — State-of-the-art CLI with built-in bridge comms.

R-F1894: A multi-panel terminal UI for ARIA operations with:
  - Persistent Claude↔ARIA bridge channel (zero operator engagement)
  - Split-pane layout: bridge chat | status | logs | command input
  - Dark theme with color accents (pink/red primary, green status, blue info)
  - Real-time updates via async polling
  - Windows Terminal / PowerShell optimized

Usage:
    python scripts/aria_terminal.py

Requires: rich, httpx (both in .venv)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ── Rich terminal UI ──────────────────────────────────────────────────────
from rich.layout import Layout
from rich.panel import Panel
from rich.text import Text
from rich.table import Table
from rich.live import Live
from rich.console import Console, Group
from rich.style import Style
from rich.align import Align
from rich.columns import Columns
from rich import box
from rich.markdown import Markdown

# ── Color scheme (from UI design analysis) ────────────────────────────────
# Background: #000000 (pure black)
# Text hierarchy: #C0C0C0 (primary), #A0A0A0 (secondary), #808080 (tertiary)
# Accent pink: #F06080 (primary accent)
# Accent green: #40B060 (status/success)
# Accent blue: #8090C0 (info/link)
# Accent orange: #D07050 (warning)
# Accent dark red: #D05060 (error)

STYLE_BG = "on #000000"
STYLE_PRIMARY = Style(color="#C0C0C0", bgcolor="#000000")
STYLE_SECONDARY = Style(color="#A0A0A0", bgcolor="#000000")
STYLE_TERTIARY = Style(color="#808080", bgcolor="#000000")
STYLE_ACCENT = Style(color="#F06080", bgcolor="#000000", bold=True)
STYLE_GREEN = Style(color="#40B060", bgcolor="#000000", bold=True)
STYLE_BLUE = Style(color="#8090C0", bgcolor="#000000")
STYLE_ORANGE = Style(color="#D07050", bgcolor="#000000")
STYLE_ERROR = Style(color="#D05060", bgcolor="#000000", bold=True)
STYLE_PANEL_BORDER = Style(color="#505050", bgcolor="#000000")
STYLE_PANEL_TITLE = Style(color="#F06080", bgcolor="#000000", bold=True)

console = Console()

# ── Bridge comms ──────────────────────────────────────────────────────────

class BridgeComms:
    """Persistent Claude↔ARIA communication channel.
    
    Polls the agent bridge for new messages and displays them in real-time.
    Zero operator engagement — messages flow automatically.
    """
    
    def __init__(self) -> None:
        self._messages: list[dict] = []
        self._last_check: float = 0
        self._check_interval: float = 5.0  # seconds between polls
        self._bridge_file = self._find_bridge_file()
        self._seen_ids: set[str] = set()
    
    def _find_bridge_file(self) -> str | None:
        """Find the bridge reply file."""
        repo_root = Path(__file__).parent.parent
        bridge_file = repo_root / "_bridge_reply.txt"
        if bridge_file.exists():
            return str(bridge_file)
        return None
    
    async def poll(self) -> list[dict]:
        """Poll for new bridge messages. Returns new messages since last poll."""
        now = time.time()
        if now - self._last_check < self._check_interval:
            return []
        self._last_check = now
        
        new_messages: list[dict] = []
        
        # Check bridge reply file
        if self._bridge_file:
            try:
                content = Path(self._bridge_file).read_text(encoding="utf-8").strip()
                if content:
                    # Parse as JSON if possible, otherwise treat as plain text
                    try:
                        msg = json.loads(content)
                        if isinstance(msg, dict):
                            msg_id = msg.get("id", str(hash(content)))
                            if msg_id not in self._seen_ids:
                                self._seen_ids.add(msg_id)
                                new_messages.append(msg)
                    except json.JSONDecodeError:
                        msg_id = str(hash(content))
                        if msg_id not in self._seen_ids:
                            self._seen_ids.add(msg_id)
                            new_messages.append({
                                "type": "bridge_message",
                                "content": content,
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            })
            except Exception:
                pass
        
        # Check agent bridge inbox
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    "http://localhost:8080/api/bridge/inbox",
                    headers={"User-Agent": "ARIA-Terminal/1.0"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    for msg in data if isinstance(data, list) else [data]:
                        msg_id = msg.get("id", str(hash(str(msg))))
                        if msg_id not in self._seen_ids:
                            self._seen_ids.add(msg_id)
                            new_messages.append(msg)
        except Exception:
            pass
        
        self._messages.extend(new_messages)
        return new_messages
    
    def get_messages(self, limit: int = 50) -> list[dict]:
        """Get recent messages."""
        return self._messages[-limit:]
    
    async def send(self, content: str, msg_type: str = "reply") -> bool:
        """Send a message to Claude via the bridge."""
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    "http://localhost:8080/api/bridge/send",
                    json={"content": content, "type": msg_type},
                    headers={"User-Agent": "ARIA-Terminal/1.0"},
                )
                return resp.status_code == 200
        except Exception:
            return False


# ── System Monitor ────────────────────────────────────────────────────────

class SystemMonitor:
    """Monitors ARIA system health and status."""
    
    def __init__(self) -> None:
        self._last_health: dict = {}
        self._last_composite: dict = {}
        self._last_gates: dict = {}
    
    async def poll_health(self) -> dict:
        """Poll ARIA health endpoints."""
        result = {
            "health": None,
            "composite": None,
            "gates": None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10.0) as client:
                # Health
                try:
                    r = await client.get("https://aria-intel.fly.dev/health/live")
                    if r.status_code == 200:
                        result["health"] = r.json()
                except Exception:
                    pass
                
                # Composite
                try:
                    r = await client.get("https://aria-intel.fly.dev/health/composite")
                    if r.status_code == 200:
                        result["composite"] = r.json()
                except Exception:
                    pass
        except Exception:
            pass
        
        self._last_health = result.get("health") or self._last_health
        self._last_composite = result.get("composite") or self._last_composite
        return result


# ── Terminal UI ───────────────────────────────────────────────────────────

class AriaTerminal:
    """Multi-panel terminal UI for ARIA operations."""
    
    def __init__(self) -> None:
        self.bridge = BridgeComms()
        self.monitor = SystemMonitor()
        self._running = True
        self._command_history: list[str] = []
        self._log_entries: list[str] = []
        self._start_time = time.time()
    
    def _build_header(self) -> Panel:
        """Build the top header bar."""
        title = Text(" ARIA TERMINAL ", style=STYLE_ACCENT)
        subtitle = Text(" v2.0 — Bridge Comms Active ", style=STYLE_TERTIARY)
        header = Text.assemble(title, subtitle)
        return Panel(
            header,
            style=STYLE_PANEL_BORDER,
            box=box.HEAVY,
            border_style=STYLE_ACCENT,
        )
    
    def _build_bridge_panel(self) -> Panel:
        """Build the bridge communications panel — the main chat area."""
        messages = self.bridge.get_messages(limit=20)
        
        if not messages:
            content = Text(
                " Waiting for bridge messages...\n\n"
                " The Claude↔ARIA channel is active.\n"
                " Messages will appear here automatically.\n",
                style=STYLE_TERTIARY,
            )
        else:
            lines: list[Text | str] = []
            for msg in messages[-15:]:
                ts = msg.get("timestamp", "")[-8:] if msg.get("timestamp") else ""
                sender = msg.get("type", "unknown").replace("_", " ").title()
                body = msg.get("content", str(msg))[:200]
                
                if "claude" in sender.lower() or sender == "Guidance":
                    sender_style = STYLE_ACCENT
                elif "aria" in sender.lower():
                    sender_style = STYLE_GREEN
                else:
                    sender_style = STYLE_BLUE
                
                line = Text.assemble(
                    (f" [{ts}] ", STYLE_TERTIARY),
                    (f"{sender}: ", sender_style),
                    (body, STYLE_PRIMARY),
                )
                lines.append(line)
                lines.append(Text(""))
            
            content = Group(*lines) if lines else Text(" No messages", style=STYLE_TERTIARY)
        
        return Panel(
            content,
            title=Text(" BRIDGE COMMS ", style=STYLE_PANEL_TITLE),
            border_style=STYLE_PANEL_BORDER,
            box=box.ROUNDED,
            height=20,
        )
    
    def _build_status_panel(self) -> Panel:
        """Build the system status panel."""
        health = self.monitor._last_health
        composite = self.monitor._last_composite
        
        table = Table(show_header=False, box=box.SIMPLE, padding=(0, 1))
        table.add_column("Key", style=STYLE_TERTIARY)
        table.add_column("Value", style=STYLE_PRIMARY)
        
        # Uptime
        uptime = time.time() - self._start_time
        uptime_str = f"{int(uptime // 3600)}h {int((uptime % 3600) // 60)}m {int(uptime % 60)}s"
        table.add_row("Uptime", uptime_str)
        
        # Health status
        if health:
            status = health.get("status", "unknown")
            status_style = STYLE_GREEN if status == "operational" else STYLE_ERROR
            table.add_row("Status", Text(status, style=status_style))
            table.add_row("Build", health.get("build_rev", "?")[:12])
        
        # Composite score
        if composite:
            cs = composite.get("composite_score", 0)
            cs_style = STYLE_GREEN if cs >= 0.71 else STYLE_ORANGE
            table.add_row("Composite", Text(f"{cs:.1%}", style=cs_style))
            
            mastery = composite.get("signals", {}).get("mastery", 0)
            verification = composite.get("signals", {}).get("verification", 0)
            table.add_row("Mastery", Text(f"{mastery:.1%}", style=STYLE_BLUE))
            table.add_row("Verification", Text(f"{verification:.1%}", style=STYLE_BLUE))
        
        # Bridge status
        bridge_msgs = len(self.bridge._messages)
        table.add_row("Bridge Msgs", Text(str(bridge_msgs), style=STYLE_ACCENT))
        
        return Panel(
            table,
            title=Text(" SYSTEM STATUS ", style=STYLE_PANEL_TITLE),
            border_style=STYLE_PANEL_BORDER,
            box=box.ROUNDED,
        )
    
    def _build_log_panel(self) -> Panel:
        """Build the activity log panel."""
        if not self._log_entries:
            content = Text(" No recent activity", style=STYLE_TERTIARY)
        else:
            lines = []
            for entry in self._log_entries[-10:]:
                lines.append(Text(entry, style=STYLE_SECONDARY))
            content = Group(*lines)
        
        return Panel(
            content,
            title=Text(" ACTIVITY LOG ", style=STYLE_PANEL_TITLE),
            border_style=STYLE_PANEL_BORDER,
            box=box.ROUNDED,
            height=8,
        )
    
    def _build_command_bar(self) -> Panel:
        """Build the command input bar."""
        cmd_text = Text.assemble(
            (" > ", STYLE_ACCENT),
            ("Type a command or wait for bridge messages...", STYLE_TERTIARY),
        )
        return Panel(
            cmd_text,
            style=STYLE_PANEL_BORDER,
            box=box.HEAVY,
            border_style=STYLE_ACCENT,
            height=3,
        )
    
    def _build_footer(self) -> Panel:
        """Build the footer with key bindings."""
        footer = Text.assemble(
            (" [Ctrl+C] Quit  ", STYLE_TERTIARY),
            (" [F5] Refresh  ", STYLE_TERTIARY),
            (" [↑/↓] History  ", STYLE_TERTIARY),
            (" [Enter] Send  ", STYLE_TERTIARY),
            (" Bridge: ", STYLE_TERTIARY),
            ("● LIVE ", STYLE_GREEN),
        )
        return Panel(
            footer,
            style=STYLE_PANEL_BORDER,
            box=box.SIMPLE,
            border_style=STYLE_TERTIARY,
        )
    
    def _build_layout(self) -> Layout:
        """Build the full terminal layout."""
        layout = Layout()
        
        # Split into header, main, footer
        layout.split(
            Layout(name="header", size=3),
            Layout(name="main"),
            Layout(name="footer", size=3),
        )
        
        # Split main into left (bridge) and right (status + logs)
        layout["main"].split_row(
            Layout(name="bridge", ratio=3),
            Layout(name="sidebar", ratio=1),
        )
        
        # Split sidebar into status and logs
        layout["sidebar"].split(
            Layout(name="status"),
            Layout(name="logs"),
        )
        
        # Add command bar below bridge
        layout["main"]["bridge"].split(
            Layout(name="chat"),
            Layout(name="command", size=3),
        )
        
        return layout
    
    async def refresh(self) -> Layout:
        """Refresh all panels and return the layout."""
        layout = self._build_layout()
        
        layout["header"].update(self._build_header())
        layout["main"]["bridge"]["chat"].update(self._build_bridge_panel())
        layout["main"]["bridge"]["command"].update(self._build_command_bar())
        layout["main"]["sidebar"]["status"].update(self._build_status_panel())
        layout["main"]["sidebar"]["logs"].update(self._build_log_panel())
        layout["footer"].update(self._build_footer())
        
        return layout
    
    async def run(self) -> None:
        """Run the terminal UI."""
        # Log startup
        self._log_entries.append(f"[{datetime.now():%H:%M:%S}] ARIA Terminal started")
        self._log_entries.append(f"[{datetime.now():%H:%M:%S}] Bridge comms initialized")
        
        with Live(
            await self.refresh(),
            console=console,
            refresh_per_second=4,
            screen=True,
        ) as live:
            poll_count = 0
            while self._running:
                try:
                    # Poll bridge for new messages
                    new_msgs = await self.bridge.poll()
                    for msg in new_msgs:
                        sender = msg.get("type", "message").replace("_", " ").title()
                        content = msg.get("content", str(msg))[:80]
                        self._log_entries.append(
                            f"[{datetime.now():%H:%M:%S}] Bridge: {sender} — {content}"
                        )
                    
                    # Poll health every 30 seconds
                    poll_count += 1
                    if poll_count % 6 == 0:  # every ~30s at 5s intervals
                        await self.monitor.poll_health()
                        self._log_entries.append(
                            f"[{datetime.now():%H:%M:%S}] Health check updated"
                        )
                    
                    # Update display
                    live.update(await self.refresh())
                    
                    await asyncio.sleep(5)
                    
                except KeyboardInterrupt:
                    self._running = False
                    break
                except Exception as e:
                    self._log_entries.append(
                        f"[{datetime.now():%H:%M:%S}] Error: {e}"
                    )
                    await asyncio.sleep(5)


async def main() -> None:
    """Entry point."""
    terminal = AriaTerminal()
    try:
        await terminal.run()
    except KeyboardInterrupt:
        console.print("\n[bold #F06080]ARIA Terminal shutting down...[/]")


if __name__ == "__main__":
    asyncio.run(main())
