#!/usr/bin/env python3
"""
ARIA TUI — Professional Terminal UI for ARIA Intelligence Agent
================================================================
A Textual-based terminal app with chat interface, streaming responses,
and full ARIA intelligence.

USAGE:
    python aria_tui.py              # Launch the TUI
    python aria_tui.py --setup      # Setup wizard (get token)

REQUIRES:
    pip install textual

ENVIRONMENT:
    ARIA_API_TOKEN    Your API token
    ARIA_SERVER       Server URL (default: https://aria-intel.fly.dev)
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Optional

# ── Try importing textual — give clear error if missing ───────────────────

try:
    from textual.app import App, ComposeResult
    from textual.containers import Container, Horizontal, Vertical, ScrollableContainer
    from textual.widgets import Header, Footer, Input, Static, RichLog, Label, Button
    from textual.reactive import reactive
    from textual.screen import Screen
    from textual import events
    from textual.binding import Binding
    from textual.widget import Widget
    from textual.message import Message
except ImportError:
    print("ARIA TUI requires 'textual'. Install it with:")
    print("  pip install textual")
    print()
    print("Or use the basic client: python aria.py")
    sys.exit(1)

# ── Constants ──────────────────────────────────────────────────────────────

VERSION = "2.2.0"
DEFAULT_SERVER = "https://aria-intel.fly.dev"
CONFIG_DIR = Path.home() / ".aria"
CONFIG_FILE = CONFIG_DIR / "config.json"

# ── Config ─────────────────────────────────────────────────────────────────


def _require_http_scheme(url: str) -> None:
    """B310: refuse non-HTTP(S) URL schemes (file:, ftp:, ...) before urlopen."""
    scheme = urllib.parse.urlparse(url).scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError(f"refusing non-HTTP(S) URL scheme {scheme!r}: {url!r}")


def _load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_config(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def _get_token() -> Optional[str]:
    token = os.environ.get("ARIA_API_TOKEN", "").strip()
    if token:
        return token
    cfg = _load_config()
    return cfg.get("api_token", "").strip() or None


def _get_server() -> str:
    server = os.environ.get("ARIA_SERVER", "").strip()
    if server:
        return server.rstrip("/")
    cfg = _load_config()
    return cfg.get("server", DEFAULT_SERVER).rstrip("/")


# ── HTTP helpers ───────────────────────────────────────────────────────────


class AriaError(Exception):
    def __init__(self, message: str, status_code: int = 0):
        self.status_code = status_code
        super().__init__(message)


def _request(
    method: str,
    path: str,
    body: Optional[dict] = None,
    timeout: int = 120,
) -> dict:
    server = _get_server()
    url = f"{server}{path}"
    _require_http_scheme(url)
    token = _get_token()

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": f"aria-tui/{VERSION}",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    data = json.dumps(body).encode("utf-8") if body else None

    try:
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310 (scheme validated by _require_http_scheme above)
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        status = e.code
        detail = ""
        try:
            detail = e.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            pass
        if status == 401:
            raise AriaError(
                "Authentication failed. Run with --setup or set ARIA_API_TOKEN",
                status_code=401,
            )
        elif status >= 500:
            raise AriaError(
                f"Server error ({status}). The server may be busy.",
                status_code=status,
            )
        else:
            raise AriaError(f"HTTP {status}: {e.reason}", status_code=status)
    except urllib.error.URLError as e:
        reason = str(e.reason) if hasattr(e, "reason") else str(e)
        if "timed out" in reason.lower():
            raise AriaError("Request timed out. Try again or simplify your question.")
        raise AriaError(f"Connection error: {reason}")
    except (json.JSONDecodeError, OSError) as e:
        raise AriaError(f"Error: {e}")


def check_status() -> dict:
    try:
        return _request("GET", "/health/live", timeout=10)
    except AriaError as e:
        return {"status": "error", "message": str(e)}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ── Setup wizard (CLI) ────────────────────────────────────────────────────


def run_setup() -> None:
    """CLI setup wizard."""
    print()
    print("  ╔══════════════════════════════════════════════╗")
    print("  ║         ARIA Client Setup                    ║")
    print("  ╚══════════════════════════════════════════════╝")
    print()
    print("  To use ARIA, you need an API token.")
    print()
    print("  Open this link in your browser:")
    print(f"    {_get_server()}/token")
    print()
    token = input("  Paste your API token: ").strip()
    if not token:
        print("  No token entered. Setup cancelled.")
        return

    os.environ["ARIA_API_TOKEN"] = token
    try:
        status = check_status()
        if "build_rev" in status:
            print(f"  ✅ Valid! Server build: {status.get('build_rev', '?')}")
        else:
            print("  ⚠️  Could not verify (server may be offline)")
    except Exception:
        print("  ⚠️  Could not verify (server may be offline)")

    cfg = _load_config()
    cfg["api_token"] = token
    _save_config(cfg)

    print()
    print("  ✅ Setup complete! Launch with: python aria_tui.py")
    print()


# ── TUI Widgets ────────────────────────────────────────────────────────────


class ChatMessage(Static):
    """A single chat message bubble."""

    def __init__(self, content: str, role: str = "assistant", timestamp: str = "") -> None:
        self.role = role
        self.msg_content = content
        self.msg_time = timestamp or datetime.now().strftime("%H:%M:%S")
        super().__init__("")

    def on_mount(self) -> None:
        prefix = "  You" if self.role == "user" else "  ARIA"
        color = "rgb(108,92,231)" if self.role == "user" else "rgb(0,230,118)"
        time_color = "rgb(136,136,160)"
        self.update(
            f"[{time_color}]{self.msg_time}[/] "
            f"[{color}]{prefix}[/]\n"
            f"  {self.msg_content}"
        )


class StatusBar(Static):
    """Bottom status bar showing connection state."""

    status_text = reactive("connecting")

    def on_mount(self) -> None:
        self.update_status()

    def update_status(self) -> None:
        token = _get_token()
        if not token:
            self.status_text = "no_token"
        else:
            try:
                status = check_status()
                if "build_rev" in status:
                    self.status_text = f"online ({status.get('build_rev', '?')})"
                else:
                    self.status_text = "offline"
            except Exception:
                self.status_text = "error"

    def watch_status_text(self, value: str) -> None:
        if value == "no_token":
            self.update(
                " [rgb(255,82,82)]●[/] No API token — run with --setup or press Ctrl+T to set one"
            )
        elif value.startswith("online"):
            self.update(f" [rgb(0,230,118)]●[/] Connected — {value}")
        elif value == "offline":
            self.update(" [rgb(255,215,64)]●[/] Server offline — check connection")
        elif value == "error":
            self.update(" [rgb(255,82,82)]●[/] Connection error")
        else:
            self.update(f" [rgb(136,136,160)]●[/] {value}")


class TokenScreen(Screen):
    """Screen for entering/updating the API token."""

    def compose(self) -> ComposeResult:
        yield Header()
        yield Container(
            Static("\n\n  [bold rgb(108,92,231)]ARIA — Set API Token[/]\n", id="token-title"),
            Static(
                "  Open this link in your browser to get a token:\n"
                f"  [rgb(0,230,118)]{_get_server()}/token[/]\n\n"
                "  Then paste it below:\n",
                id="token-instructions",
            ),
            Input(placeholder="Paste your API token here...", id="token-input"),
            Button("Save", variant="primary", id="token-save"),
            Button("Cancel", id="token-cancel"),
            id="token-container",
        )
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "token-save":
            token = self.query_one("#token-input", Input).value.strip()
            if token:
                cfg = _load_config()
                cfg["api_token"] = token
                _save_config(cfg)
                os.environ["ARIA_API_TOKEN"] = token
                self.app.pop_screen()
                if hasattr(self.app, "status_bar") and self.app.status_bar:
                    self.app.status_bar.update_status()
        elif event.button.id == "token-cancel":
            self.app.pop_screen()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "token-input":
            token = event.value.strip()
            if token:
                cfg = _load_config()
                cfg["api_token"] = token
                _save_config(cfg)
                os.environ["ARIA_API_TOKEN"] = token
                self.app.pop_screen()
                if hasattr(self.app, "status_bar") and self.app.status_bar:
                    self.app.status_bar.update_status()


# ── Main ARIA TUI App ─────────────────────────────────────────────────────


class AriaTUI(App):
    """ARIA Terminal UI — professional chat interface."""

    TITLE = "ARIA — Autonomous Research Intelligence Agent"
    SUB_TITLE = f"v{VERSION}"

    CSS = """
    Screen {
        background: #0a0a0f;
    }

    #main-container {
        height: 100%;
        width: 100%;
    }

    #chat-panel {
        width: 1fr;
        height: 1fr;
        background: #0a0a0f;
        border-right: solid #1e1e2e;
    }

    #chat-log {
        height: 1fr;
        background: #0a0a0f;
        padding: 1;
    }

    #input-container {
        height: 5;
        background: #12121a;
        border-top: solid #1e1e2e;
        padding: 1 2;
    }

    #chat-input {
        width: 1fr;
        background: #0a0a0f;
        color: #e0e0e8;
        border: solid #1e1e2e;
        padding: 1 2;
    }

    #chat-input:focus {
        border: solid rgb(108,92,231);
    }

    #status-bar {
        height: 1;
        background: #12121a;
        color: #8888a0;
        padding: 0 2;
    }

    #side-panel {
        width: 28;
        height: 1fr;
        background: #12121a;
        border-left: solid #1e1e2e;
        padding: 1;
    }

    .section-title {
        color: rgb(108,92,231);
        text-style: bold;
        padding: 0 0 1 0;
    }

    .info-text {
        color: #8888a0;
        padding: 0 0 1 0;
    }

    ChatMessage {
        margin: 0 0 1 0;
    }

    #token-container {
        align: center middle;
        width: 60;
        height: auto;
        background: #12121a;
        border: solid #1e1e2e;
        padding: 2;
    }

    #token-title {
        text-align: center;
        margin-bottom: 1;
    }

    #token-instructions {
        color: #8888a0;
        margin-bottom: 1;
    }

    #token-input {
        width: 100%;
        margin-bottom: 1;
    }

    Button {
        margin: 0 1;
    }

    #token-save {
        background: rgb(108,92,231);
        color: white;
    }

    #token-cancel {
        background: #1e1e2e;
        color: #8888a0;
    }
    """

    BINDINGS = [
        Binding("ctrl+t", "show_token_screen", "Set Token"),
        Binding("ctrl+l", "clear_chat", "Clear"),
        Binding("ctrl+q", "quit", "Quit"),
        Binding("ctrl+s", "show_status", "Status"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._streaming = False
        self._session_id = f"tui_{os.environ.get('USER', 'user')}"

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(id="main-container"):
            with Horizontal():
                with Vertical(id="chat-panel"):
                    yield RichLog(id="chat-log", highlight=True, markup=True, wrap=True)
                    with Container(id="input-container"):
                        yield Input(
                            placeholder="Ask ARIA anything... (Ctrl+T for token, Ctrl+Q to quit)",
                            id="chat-input",
                        )
                with Vertical(id="side-panel"):
                    yield Static("[bold rgb(108,92,231)]Session[/]", classes="section-title")
                    yield Static(f"v{VERSION}", classes="info-text", id="session-version")
                    yield Static("", classes="info-text", id="session-server")
                    yield Static("", classes="info-text", id="session-status")
                    yield Static("", classes="info-text", id="session-tools")
                    yield Static("", classes="info-text", id="session-cost")
                    yield Static("", classes="section-title", id="tips-title")
                    yield Static(
                        "Ctrl+T  Set token\n"
                        "Ctrl+L  Clear chat\n"
                        "Ctrl+S  Check status\n"
                        "Ctrl+Q  Quit",
                        classes="info-text",
                    )
        yield StatusBar(id="status-bar")

    def on_mount(self) -> None:
        """Called when the app is mounted."""
        self.query_one("#chat-input", Input).focus()
        self._update_side_panel()
        self._add_welcome_message()

        # Check token on startup
        token = _get_token()
        if not token:
            self.call_after_refresh(self._show_token_prompt)

    def _show_token_prompt(self) -> None:
        """Show a welcome message prompting for token."""
        chat_log = self.query_one("#chat-log", RichLog)
        chat_log.write(
            "[rgb(255,215,64)]  ⚠️  No API token found.[/]\n"
            f"  Press [bold]Ctrl+T[/] to set one, or visit:\n"
            f"  [rgb(0,230,118)]  {_get_server()}/token[/]"
        )

    def _add_welcome_message(self) -> None:
        """Add the welcome banner to chat."""
        chat_log = self.query_one("#chat-log", RichLog)
        chat_log.write("")
        chat_log.write(
            "  [bold rgb(108,92,231)]╔══════════════════════════════════════╗[/]"
        )
        chat_log.write(
            "  [bold rgb(108,92,231)]║[/]  [rgb(0,230,118)]ARIA[/] — Autonomous Research Intelligence  [bold rgb(108,92,231)]║[/]"
        )
        chat_log.write(
            "  [bold rgb(108,92,231)]╚══════════════════════════════════════╝[/]"
        )
        chat_log.write("")
        chat_log.write(
            "  Hello! I'm ARIA. Ask me anything — research, analyse code,\n"
            "  investigate companies, review documents, or just chat."
        )
        chat_log.write("")

    def _update_side_panel(self) -> None:
        """Update the side panel with current info."""
        server = _get_server()
        self.query_one("#session-server", Static).update(f"Server:\n{server}")

        token = _get_token()
        if token:
            masked = token[:6] + "..." + token[-4:] if len(token) > 10 else "set"
            self.query_one("#session-status", Static).update(f"Token: {masked}")
        else:
            self.query_one("#session-status", Static).update("Token: [red]not set[/]")

    def action_show_token_screen(self) -> None:
        """Open the token settings screen."""
        self.push_screen(TokenScreen())

    def action_clear_chat(self) -> None:
        """Clear the chat log."""
        chat_log = self.query_one("#chat-log", RichLog)
        chat_log.clear()
        self._add_welcome_message()

    def action_show_status(self) -> None:
        """Show server status in chat."""
        chat_log = self.query_one("#chat-log", RichLog)
        chat_log.write("")
        chat_log.write("[bold]─── Server Status ───[/]")
        try:
            status = check_status()
            if "build_rev" in status:
                chat_log.write(
                    f"  [rgb(0,230,118)]🟢 Online[/]\n"
                    f"  Build: {status.get('build_rev', '?')}\n"
                    f"  Server: {_get_server()}"
                )
            else:
                chat_log.write(f"  [rgb(255,82,82)]🔴 Offline[/] — {status.get('message', '?')}")
        except Exception as e:
            chat_log.write(f"  [rgb(255,82,82)]🔴 Error[/] — {e}")
        chat_log.write("")

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle user input submission."""
        if event.input.id != "chat-input":
            return

        message = event.value.strip()
        if not message:
            return

        # Clear input
        self.query_one("#chat-input", Input).value = ""

        # Handle commands
        if message.startswith("/"):
            await self._handle_command(message)
            return

        # Check token
        token = _get_token()
        if not token:
            chat_log = self.query_one("#chat-log", RichLog)
            chat_log.write(
                "\n  [rgb(255,82,82)]❌ No API token.[/] Press [bold]Ctrl+T[/] to set one.\n"
            )
            return

        # Add user message to chat
        chat_log = self.query_one("#chat-log", RichLog)
        timestamp = datetime.now().strftime("%H:%M")
        chat_log.write(f"\n[{timestamp}] [bold rgb(108,92,231)]  You[/]")
        chat_log.write(f"  {message}")
        chat_log.write("")

        # Show thinking indicator
        thinking_id = chat_log.write("\n  [rgb(255,215,64)]🧠 ARIA is thinking...[/]\n")

        # Send to ARIA
        try:
            await self._send_chat(message, chat_log, thinking_id)
        except AriaError as e:
            chat_log.write(f"\n  [rgb(255,82,82)]❌ {e}[/]\n")
        except Exception as e:
            chat_log.write(f"\n  [rgb(255,82,82)]❌ Error: {e}[/]\n")

        # Scroll to bottom
        chat_log.scroll_end()

    async def _handle_command(self, command: str) -> None:
        """Handle slash commands."""
        chat_log = self.query_one("#chat-log", RichLog)
        cmd = command[1:].lower().strip()

        if cmd in ("help", "?"):
            chat_log.write("")
            chat_log.write("[bold]─── Commands ───[/]")
            chat_log.write(
                "  /help      Show this help\n"
                "  /status    Check server status\n"
                "  /token     Set or change API token\n"
                "  /clear     Clear chat\n"
                "  /exit      Quit"
            )
            chat_log.write("")
        elif cmd == "status":
            self.action_show_status()
        elif cmd == "token":
            self.action_show_token_screen()
        elif cmd in ("clear", "cls"):
            self.action_clear_chat()
        elif cmd in ("exit", "quit"):
            self.exit()
        else:
            chat_log.write(f"\n  Unknown command: {command}\n  Type /help for available commands.\n")

    async def _send_chat(self, message: str, chat_log: RichLog, thinking_id: int) -> None:
        """Send a chat message and display the streaming response."""
        server = _get_server()
        token = _get_token()
        url = f"{server}/api/aria/chat/stream"
        _require_http_scheme(url)

        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "User-Agent": f"aria-tui/{VERSION}",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"

        body = json.dumps({
            "message": message,
            "session_id": self._session_id,
        }).encode("utf-8")

        req = urllib.request.Request(url, data=body, headers=headers, method="POST")

        try:
            timestamp = datetime.now().strftime("%H:%M")
            chat_log.write(f"[{timestamp}] [bold rgb(0,230,118)]  ARIA[/]")

            response_text = ""
            buffer = ""

            with urllib.request.urlopen(req, timeout=180) as resp:  # nosec B310 (scheme validated by _require_http_scheme above)
                while True:
                    chunk = resp.read(4096)
                    if not chunk:
                        break
                    buffer += chunk.decode("utf-8", errors="replace")

                    while "\n\n" in buffer:
                        event, buffer = buffer.split("\n\n", 1)
                        for line in event.split("\n"):
                            if line.startswith("data: "):
                                data_str = line[6:]
                                if data_str.strip() == "[DONE]":
                                    break
                                try:
                                    parsed = json.loads(data_str)
                                    text = parsed.get("text", parsed.get("response", ""))
                                    if text:
                                        response_text += text
                                        # Update the message in real-time
                                        chat_log.write(f"  {response_text}")
                                except json.JSONDecodeError:
                                    if data_str.strip():
                                        response_text += data_str
                                        chat_log.write(f"  {response_text}")

            if not response_text:
                # Fallback to non-streaming
                result = _request("POST", "/api/aria/chat", {
                    "message": message,
                    "session_id": self._session_id,
                }, timeout=180)
                response_text = result.get("response") or result.get("answer") or json.dumps(result)
                chat_log.write(f"  {response_text}")

            chat_log.write("")

        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise AriaError("Authentication failed. Press Ctrl+T to set your token.", 401)
            raise AriaError(f"Server error ({e.code})", e.code)
        except urllib.error.URLError as e:
            reason = str(e.reason) if hasattr(e, "reason") else str(e)
            raise AriaError(f"Connection error: {reason}")


# ── Entry point ────────────────────────────────────────────────────────────


def main() -> None:
    """Main entry point."""
    args = sys.argv[1:]

    if "--setup" in args:
        run_setup()
        return

    if "--help" in args or "-h" in args:
        print(__doc__)
        return

    if "--version" in args or "-v" in args:
        print(f"ARIA TUI v{VERSION}")
        return

    # Check if textual is available
    try:
        import textual  # noqa: F401
    except ImportError:
        print("ARIA TUI requires 'textual'. Install it with:")
        print("  pip install textual")
        print()
        print("Or use the basic client: python aria.py")
        sys.exit(1)

    # Launch the TUI
    app = AriaTUI()
    app.run()


if __name__ == "__main__":
    main()
