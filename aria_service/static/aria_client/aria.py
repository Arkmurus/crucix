#!/usr/bin/env python3
"""
ARIA — Autonomous Research Intelligence Agent
==============================================
Terminal client. Connects to the ARIA server for full intelligence.

USAGE:
    aria                          # Interactive mode
    aria "your question"          # Single-shot mode
    aria --setup                  # Walk through setup (get token)
    aria --status                 # Check server status
    aria --help                   # Show this help

ENVIRONMENT:
    ARIA_API_TOKEN                # Your API token (required)
    ARIA_SERVER                   # Server URL (default: https://aria-intel.fly.dev)

First time? Run:  aria --setup
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
import urllib.parse
import textwrap
import shutil
import atexit
import platform
from pathlib import Path
from typing import Optional

# ── Constants ──────────────────────────────────────────────────────────────────

VERSION = "2.1.0"
DEFAULT_SERVER = "https://aria-intel.fly.dev"
CONFIG_DIR = Path.home() / ".aria"
CONFIG_FILE = CONFIG_DIR / "config.json"
HISTORY_FILE = CONFIG_DIR / "history.txt"
MAX_HISTORY = 1000

# ── Colours / styling ─────────────────────────────────────────────────────────

if platform.system() == "Windows":
    # Enable ANSI on Windows 10+
    os.system("")  # noqa: S605, S607 — enables VT processing

_COLORS = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "green": "\033[92m",
    "cyan": "\033[96m",
    "yellow": "\033[93m",
    "red": "\033[91m",
    "magenta": "\033[95m",
    "blue": "\033[94m",
    "grey": "\033[90m",
}


def _c(code: str, text: str) -> str:
    """Wrap text in ANSI colour code."""
    return f"{_COLORS.get(code, '')}{text}{_COLORS['reset']}"


# ── Config ────────────────────────────────────────────────────────────────────


def _load_config() -> dict:
    """Load config from ~/.aria/config.json."""
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_config(cfg: dict) -> None:
    """Save config to ~/.aria/config.json."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def _get_token() -> Optional[str]:
    """Get API token: env var > config file > None."""
    token = os.environ.get("ARIA_API_TOKEN", "").strip()
    if token:
        return token
    cfg = _load_config()
    return cfg.get("api_token", "").strip() or None


def _get_server() -> str:
    """Get server URL: env var > config file > default."""
    server = os.environ.get("ARIA_SERVER", "").strip()
    if server:
        return server.rstrip("/")
    cfg = _load_config()
    return cfg.get("server", DEFAULT_SERVER).rstrip("/")


# ── HTTP helpers ──────────────────────────────────────────────────────────────


class AriaError(Exception):
    """Base ARIA client error."""

    def __init__(self, message: str, status_code: int = 0, details: str = "") -> None:
        self.status_code = status_code
        self.details = details
        super().__init__(message)


def _request(
    method: str,
    path: str,
    body: Optional[dict] = None,
    timeout: int = 120,
    headers: Optional[dict] = None,
) -> dict:
    """Make an HTTP request to the ARIA server.

    Returns parsed JSON response.
    Raises AriaError on failure with clear messaging.
    """
    server = _get_server()
    url = f"{server}{path}"
    token = _get_token()

    req_headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": f"aria-client/{VERSION}",
    }
    if token:
        req_headers["Authorization"] = f"Bearer {token}"
    if headers:
        req_headers.update(headers)

    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")

    try:
        req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        status = e.code
        detail = ""
        try:
            detail = e.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass

        if status == 401:
            raise AriaError(
                "Authentication failed (401 Unauthorized).\n"
                "  You need a valid ARIA API token.\n"
                "  Run:  aria --setup\n"
                "  Or set:  set ARIA_API_TOKEN=your_token_here",
                status_code=401,
                details=detail,
            )
        elif status == 403:
            raise AriaError(
                "Access denied (403 Forbidden). Your token may not have permission.",
                status_code=403,
                details=detail,
            )
        elif status == 429:
            raise AriaError(
                "Rate limited (429). Please wait a moment and try again.",
                status_code=429,
                details=detail,
            )
        elif status >= 500:
            raise AriaError(
                f"Server error ({status}). The ARIA server may be busy or restarting.\n"
                f"  Try again in a few seconds. Run 'aria --status' to check.",
                status_code=status,
                details=detail,
            )
        else:
            raise AriaError(
                f"HTTP {status}: {e.reason}",
                status_code=status,
                details=detail,
            )
    except urllib.error.URLError as e:
        reason = str(e.reason) if hasattr(e, "reason") else str(e)
        if "timed out" in reason.lower():
            raise AriaError(
                "Request timed out. The server may be busy processing a complex query.\n"
                "  Try again, or simplify your question.",
            )
        if "connection refused" in reason.lower() or "connection reset" in reason.lower():
            raise AriaError(
                "Could not connect to the ARIA server.\n"
                "  Check your internet connection.\n"
                f"  Server: {server}\n"
                "  Run:  aria --status",
            )
        raise AriaError(f"Connection error: {reason}")
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise AriaError(f"Invalid response from server: {e}")
    except OSError as e:
        raise AriaError(f"Network error: {e}")


def _stream_request(
    path: str,
    body: dict,
    timeout: int = 180,
) -> list[str]:
    """Make a streaming SSE request. Yields text chunks as they arrive.

    Returns list of all chunks received.
    """
    server = _get_server()
    url = f"{server}{path}"
    token = _get_token()

    req_headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "User-Agent": f"aria-client/{VERSION}",
    }
    if token:
        req_headers["Authorization"] = f"Bearer {token}"

    data = json.dumps(body).encode("utf-8")
    chunks: list[str] = []

    try:
        req = urllib.request.Request(url, data=data, headers=req_headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            buffer = ""
            while True:
                chunk = resp.read(4096)
                if not chunk:
                    break
                buffer += chunk.decode("utf-8", errors="replace")
                # Process SSE events
                while "\n\n" in buffer:
                    event, buffer = buffer.split("\n\n", 1)
                    for line in event.split("\n"):
                        if line.startswith("data: "):
                            data_str = line[6:]
                            if data_str.strip() == "[DONE]":
                                return chunks
                            try:
                                parsed = json.loads(data_str)
                                text = parsed.get("text", parsed.get("response", ""))
                                if text:
                                    chunks.append(text)
                                    # Print as we go
                                    print(text, end="", flush=True)
                            except json.JSONDecodeError:
                                # Raw text
                                if data_str.strip():
                                    chunks.append(data_str)
                                    print(data_str, end="", flush=True)
            return chunks
    except urllib.error.HTTPError as e:
        status = e.code
        detail = ""
        try:
            detail = e.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        if status == 401:
            raise AriaError(
                "Authentication failed (401 Unauthorized).\n"
                "  Run:  aria --setup",
                status_code=401,
                details=detail,
            )
        raise AriaError(f"Server error ({status})", status_code=status, details=detail)
    except urllib.error.URLError as e:
        reason = str(e.reason) if hasattr(e, "reason") else str(e)
        raise AriaError(f"Connection error during streaming: {reason}")
    except OSError as e:
        raise AriaError(f"Stream error: {e}")


# ── Server status ─────────────────────────────────────────────────────────────


def check_status() -> dict:
    """Check ARIA server health. Returns status dict."""
    try:
        result = _request("GET", "/health/live", timeout=10)
        return result
    except AriaError as e:
        return {"status": "error", "message": str(e).split("\n")[0]}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ── Chat ──────────────────────────────────────────────────────────────────────


def send_chat(message: str, session_id: str = "") -> dict:
    """Send a chat message and get response."""
    body = {
        "message": message,
        "session_id": session_id or f"client_{os.environ.get('USERNAME', 'user')}",
    }
    return _request("POST", "/api/aria/chat", body, timeout=180)


def send_chat_stream(message: str, session_id: str = "") -> list[str]:
    """Send a chat message and stream the response via SSE."""
    body = {
        "message": message,
        "session_id": session_id or f"client_{os.environ.get('USERNAME', 'user')}",
    }
    return _stream_request("/api/aria/chat/stream", body, timeout=180)


# ── Setup wizard ──────────────────────────────────────────────────────────────


def run_setup() -> None:
    """Interactive setup wizard — walks user through getting a token."""
    print()
    print(_c("bold", "  ╔══════════════════════════════════════════════════════════╗"))
    print(_c("bold", "  ║           ARIA Client Setup                            ║"))
    print(_c("bold", "  ╚══════════════════════════════════════════════════════════╝"))
    print()
    print("  To use ARIA, you need an API token.")
    print()
    print("  Option 1: Get a token from the ARIA web interface")
    print("    Open https://intel.arkmurus.com in your browser")
    print("    Log in or create an account")
    print("    Go to Settings → API Tokens → Create New Token")
    print("    Copy the token and paste it below")
    print()
    print("  Option 2: Contact your ARIA administrator")
    print("    Ask them to create a token for you")
    print()

    token = input("  Paste your API token: ").strip()
    if not token:
        print(_c("red", "  No token entered. Setup cancelled."))
        return

    # Validate the token
    print("  Validating token...", end=" ", flush=True)
    os.environ["ARIA_API_TOKEN"] = token
    try:
        status = check_status()
        if "build_rev" in status:
            print(_c("green", "✅ Valid!"))
            print(f"  Server build: {status.get('build_rev', '?')}")
        else:
            print(_c("yellow", "⚠️  Could not verify (server may be offline)"))
    except Exception:
        print(_c("yellow", "⚠️  Could not verify (server may be offline)"))

    # Save config
    cfg = _load_config()
    cfg["api_token"] = token
    server = input(f"  Server URL [{DEFAULT_SERVER}]: ").strip()
    if server:
        cfg["server"] = server
    else:
        cfg["server"] = DEFAULT_SERVER
    _save_config(cfg)

    print()
    print(_c("green", "  ✅ Setup complete!"))
    print()
    print("  You can now use ARIA:")
    print("    aria                          # Interactive mode")
    print("    aria \"your question\"          # Single question")
    print()
    print("  Your token is saved in: ~/.aria/config.json")
    print("  You can also set the ARIA_API_TOKEN environment variable.")
    print()


# ── Interactive shell ─────────────────────────────────────────────────────────


def _load_history() -> list[str]:
    """Load command history from file."""
    if HISTORY_FILE.exists():
        try:
            lines = HISTORY_FILE.read_text(encoding="utf-8").splitlines()
            return [l for l in lines if l.strip()]
        except OSError:
            pass
    return []


def _save_history(history: list[str]) -> None:
    """Save command history to file."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        HISTORY_FILE.write_text("\n".join(history[-MAX_HISTORY:]), encoding="utf-8")
    except OSError:
        pass


def _print_welcome() -> None:
    """Print the welcome banner."""
    width = min(shutil.get_terminal_size().columns, 90)
    print()
    print(_c("bold", "  ╔" + "═" * (width - 4) + "╗"))
    print(_c("bold", "  ║") + _c("green", "  █████  ██████  ██  █████       ARIA v" + VERSION).ljust(width - 2) + _c("bold", "║"))
    print(_c("bold", "  ║") + _c("green", "  ██   ██ ██   ██ ██ ██   ██      Autonomous Research Intelligence").ljust(width - 2) + _c("bold", "║"))
    print(_c("bold", "  ║") + _c("green", "  ███████ ██████  ██ ███████      Terminal Client").ljust(width - 2) + _c("bold", "║"))
    print(_c("bold", "  ║") + (" " * (width - 4)) + _c("bold", "║"))
    print(_c("bold", "  ╚" + "═" * (width - 4) + "╝"))
    print()

    # Check connection
    try:
        status = check_status()
        if "build_rev" in status:
            rev = status.get("build_rev", "?")
            print(f"  {_c('green', '✅')} Connected to ARIA server ({_c('dim', rev)})")
        else:
            print(f"  {_c('yellow', '⚠️')} Server: {status.get('message', 'unknown')}")
    except Exception:
        print(f"  {_c('yellow', '⚠️')} Could not check server status")

    print()
    print(f"  Hello. I'm ARIA — your research intelligence agent.")
    print(f"  Type {_c('cyan', 'help')} for commands, or just ask me anything.")
    print()


def _print_help() -> None:
    """Print help text."""
    print()
    print(_c("bold", "  ─── Commands ─────────────────────────────────────────────"))
    print()
    print(f"    {_c('cyan', 'help')}              Show this help")
    print(f"    {_c('cyan', 'status')}            Check ARIA server status")
    print(f"    {_c('cyan', 'history')}           Show command history")
    print(f"    {_c('cyan', 'cls')}               Clear screen")
    print(f"    {_c('cyan', 'exit')}              Quit")
    print()
    print(_c("bold", "  ─── What I can do ────────────────────────────────────────"))
    print()
    print("    Just type your question. Full ARIA intelligence:")
    print()
    print("    🌐  Research companies, people, and markets")
    print("    🔍  Search the web for current information")
    print("    📄  Analyse code and find bugs")
    print("    📊  Investigate supply chains and procurement")
    print("    📋  Review documents and contracts")
    print("    💬  Chat about any topic")
    print()
    print(_c("bold", "  ─── Examples ─────────────────────────────────────────────"))
    print()
    print('    "Research Acme Corp and their supply chain"')
    print('    "Analyse this code: def foo(): pass"')
    print('    "What are the latest defence tenders in Europe?"')
    print('    "Explain quantum computing in simple terms"')
    print()


def _print_status() -> None:
    """Print server status."""
    print()
    print(_c("bold", "  ─── ARIA Server Status ───────────────────────────────────"))
    print()
    try:
        status = check_status()
        if "build_rev" in status:
            print(f"    {_c('green', '🟢')} Server: {_c('bold', 'ONLINE')}")
            print(f"    Build:  {status.get('build_rev', '?')}")
            uptime = status.get("uptime_seconds", 0)
            if uptime:
                hours = int(uptime) // 3600
                mins = (int(uptime) % 3600) // 60
                print(f"    Uptime: {hours}h {mins}m")
            print(f"    Server: {_get_server()}")
        else:
            print(f"    {_c('red', '🔴')} Server: {_c('bold', 'OFFLINE')}")
            print(f"    Reason: {status.get('message', 'unknown')}")
    except Exception as e:
        print(f"    {_c('red', '🔴')} Server: {_c('bold', 'ERROR')}")
        print(f"    {e}")
    print()


def _print_history(history: list[str]) -> None:
    """Print command history."""
    print()
    print(_c("bold", "  ─── Command History ──────────────────────────────────────"))
    print()
    if not history:
        print("    (no commands yet)")
    else:
        for i, cmd in enumerate(history[-20:], 1):
            print(f"    {i:2d}. {cmd}")
    print()


def interactive_shell() -> None:
    """Run the interactive ARIA shell."""
    # Check token
    token = _get_token()
    if not token:
        print()
        print(_c("red", "  ❌ No API token found."))
        print()
        print("  ARIA requires authentication. Run setup first:")
        print(f"    {_c('cyan', 'aria --setup')}")
        print()
        print("  Or set the environment variable:")
        print(f"    {_c('dim', 'set ARIA_API_TOKEN=your_token_here')}")
        print()
        return

    history = _load_history()
    _print_welcome()

    while True:
        try:
            user = os.environ.get("USERNAME", "user")
            line = input(f"  {_c('green', 'aria')}@{_c('dim', user)}> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            print(_c("yellow", "  Goodbye."))
            break

        if not line:
            continue

        # Handle built-in commands
        cmd = line.lower()
        if cmd in ("exit", "quit"):
            print(_c("yellow", "  Goodbye."))
            break
        if cmd == "cls":
            # Clear screen
            os.system("cls" if platform.system() == "Windows" else "clear")  # noqa: S605, S607
            _print_welcome()
            continue
        if cmd == "help":
            _print_help()
            continue
        if cmd == "status":
            _print_status()
            continue
        if cmd == "history":
            _print_history(history)
            continue

        # Add to history
        history.append(line)
        _save_history(history)

        # Send to ARIA
        print()
        print(f"  {_c('yellow', '🧠')} ARIA is thinking...")
        print()

        try:
            # Try streaming first
            try:
                chunks = send_chat_stream(line)
                if chunks:
                    print()  # Final newline after stream
                else:
                    # Fallback to non-streaming
                    result = send_chat(line)
                    response = result.get("response") or result.get("answer") or json.dumps(result, indent=2)
                    print(_c("cyan", f"  {response}"))
            except AriaError as e:
                if "401" in str(e):
                    print(_c("red", f"  ❌ {e}"))
                elif "timeout" in str(e).lower():
                    print(_c("yellow", f"  ⏱️  {e}"))
                else:
                    print(_c("red", f"  ❌ {e}"))
            except Exception as e:
                print(_c("red", f"  ❌ Unexpected error: {e}"))

        except AriaError as e:
            print(_c("red", f"  ❌ {e}"))
        except Exception as e:
            print(_c("red", f"  ❌ Unexpected error: {e}"))

        print()


# ── CLI entry point ───────────────────────────────────────────────────────────


def main() -> None:
    """Main entry point."""
    args = sys.argv[1:]

    if not args:
        # Interactive mode
        interactive_shell()
        return

    cmd = args[0]

    if cmd in ("--help", "-h", "/?"):
        print(__doc__)
        return

    if cmd in ("--version", "-v"):
        print(f"ARIA Client v{VERSION}")
        return

    if cmd == "--setup":
        run_setup()
        return

    if cmd == "--status":
        _print_status()
        return

    if cmd == "--check":
        # Quick check — returns exit code for scripting
        token = _get_token()
        if not token:
            print("NO_TOKEN")
            sys.exit(1)
        try:
            status = check_status()
            if "build_rev" in status:
                print(f"OK {status.get('build_rev', '?')}")
            else:
                print("OFFLINE")
                sys.exit(1)
        except Exception:
            print("ERROR")
            sys.exit(1)
        return

    # Single-shot mode: send the argument as a question
    token = _get_token()
    if not token:
        print(_c("red", "❌ No API token found. Run: aria --setup"))
        sys.exit(1)

    question = " ".join(args)
    print(f"  {_c('yellow', '🧠')} ARIA is thinking...")
    print()

    try:
        result = send_chat(question)
        response = result.get("response") or result.get("answer") or json.dumps(result, indent=2)
        print(_c("cyan", f"  {response}"))
        print()
    except AriaError as e:
        print(_c("red", f"  ❌ {e}"))
        sys.exit(1)
    except Exception as e:
        print(_c("red", f"  ❌ Unexpected error: {e}"))
        sys.exit(1)


if __name__ == "__main__":
    main()
