#!/usr/bin/env python3
"""
ARIA CODER — Terminal Client
=============================
You have an ARIA Coder API token. Use it to write code, fix bugs,
refactor, review, and build software — all from the terminal.

USAGE:
    python aria.py                  # Interactive coder shell
    python aria.py "write a script" # Single command
    python aria.py --setup          # First-time setup
    python aria.py --status         # Check connection

ENVIRONMENT:
    ARIA_API_TOKEN    Your ARIA Coder API token
    ARIA_SERVER       Server URL (default: https://aria-intel.fly.dev)
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
import shutil
import platform
import subprocess
from pathlib import Path
from typing import Optional

VERSION = "3.0.0"
DEFAULT_SERVER = "https://aria-intel.fly.dev"
CONFIG_DIR = Path.home() / ".aria"
CONFIG_FILE = CONFIG_DIR / "config.json"

# ── ANSI colours ─────────────────────────────────────────────────────────────

if platform.system() == "Windows":
    os.system("")

C = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "italic": "\033[3m",
    "green": "\033[92m",
    "cyan": "\033[96m",
    "yellow": "\033[93m",
    "red": "\033[91m",
    "magenta": "\033[95m",
    "blue": "\033[94m",
    "purple": "\033[38;5;99m",
    "orange": "\033[38;5;214m",
    "grey": "\033[90m",
    "white": "\033[97m",
    "bg_dark": "\033[48;5;235m",
    "bg_purple": "\033[48;5;55m",
}


def c(code: str, text: str) -> str:
    return f"{C.get(code, '')}{text}{C['reset']}"


# ── Config ───────────────────────────────────────────────────────────────────


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


# ── HTTP ─────────────────────────────────────────────────────────────────────


class AriaError(Exception):
    def __init__(self, message: str, status_code: int = 0):
        self.status_code = status_code
        super().__init__(message)


def _request(method: str, path: str, body: Optional[dict] = None, timeout: int = 180) -> dict:
    server = _get_server()
    url = f"{server}{path}"
    token = _get_token()
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": f"aria-coder/{VERSION}",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode("utf-8") if body else None
    try:
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
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
                "Authentication failed. Your ARIA Coder token is invalid or missing.\n"
                "  Run:  python aria.py --setup\n"
                "  Or:   set ARIA_API_TOKEN=your_token_here",
                status_code=401,
            )
        elif status >= 500:
            raise AriaError(f"Server error ({status}). The coder engine may be busy.", status_code=status)
        else:
            raise AriaError(f"HTTP {status}: {e.reason}", status_code=status)
    except urllib.error.URLError as e:
        reason = str(e.reason) if hasattr(e, "reason") else str(e)
        if "timed out" in reason.lower():
            raise AriaError("Request timed out. The coder is thinking hard — try again or simplify.")
        raise AriaError(f"Connection error: {reason}")
    except (json.JSONDecodeError, OSError) as e:
        raise AriaError(f"Error: {e}")


def check_status() -> dict:
    try:
        return _request("GET", "/health/live", timeout=10)
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ── Coder API calls ──────────────────────────────────────────────────────────


def send_coder_task(task: str, code: str = "", session_id: str = "") -> dict:
    """Send a coding task to ARIA Coder."""
    body = {
        "message": task,
        "session_id": session_id or f"coder_{os.environ.get('USERNAME', 'user')}",
        "auto_tools": True,
    }
    if code:
        body["code_context"] = code
    return _request("POST", "/api/aria/chat", body, timeout=180)


def send_coder_task_stream(task: str, code: str = "", session_id: str = "") -> list[str]:
    """Send a coding task and stream the response."""
    server = _get_server()
    token = _get_token()
    url = f"{server}/api/aria/chat/stream"

    headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "User-Agent": f"aria-coder/{VERSION}",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    body = {
        "message": task,
        "session_id": session_id or f"coder_{os.environ.get('USERNAME', 'user')}",
        "auto_tools": True,
    }
    if code:
        body["code_context"] = code

    data = json.dumps(body).encode("utf-8")
    chunks: list[str] = []

    try:
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=300) as resp:
            buffer = ""
            last_data_time = time.time()
            while True:
                try:
                    chunk = resp.read(4096)
                    if not chunk:
                        break
                    last_data_time = time.time()
                    buffer += chunk.decode("utf-8", errors="replace")
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
                                        sys.stdout.write(text)
                                        sys.stdout.flush()
                                except json.JSONDecodeError:
                                    if data_str.strip():
                                        chunks.append(data_str)
                                        sys.stdout.write(data_str)
                                        sys.stdout.flush()
                except urllib.error.HTTPError:
                    raise
                except (OSError, urllib.error.URLError) as e:
                    # If we got some data already, return what we have
                    if chunks:
                        return chunks
                    raise
            return chunks
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise AriaError("Authentication failed. Your token is invalid.", 401)
        raise AriaError(f"Server error ({e.code})", e.code)
    except urllib.error.URLError as e:
        reason = str(e.reason) if hasattr(e, "reason") else str(e)
        raise AriaError(f"Connection error: {reason}")


# ── Setup wizard ─────────────────────────────────────────────────────────────


def run_setup() -> None:
    """First-time setup — get API token and save it."""
    print()
    print(c("bold", c("purple", "  ╔══════════════════════════════════════════════════╗")))
    print(c("bold", c("purple", "  ║")), c("bold", "     ARIA Coder — Setup"), c("bold", c("purple", "               ║")))
    print(c("bold", c("purple", "  ╚══════════════════════════════════════════════════╝")))
    print()
    print("  To use ARIA Coder, you need an API token.")
    print()
    print(f"  {c('cyan', 'Step 1:')} Open this link in your browser:")
    print(f"    {c('green', _get_server() + '/token')}")
    print()
    print(f"  {c('cyan', 'Step 2:')} Copy the token shown on that page")
    print()
    print(f"  {c('cyan', 'Step 3:')} Paste it below:")
    print()

    token = input(f"  {c('yellow', 'Paste your token')}> ").strip()
    if not token:
        print(c("red", "\n  No token entered. Setup cancelled."))
        return

    print(f"  {c('dim', 'Validating...')}", end=" ", flush=True)
    os.environ["ARIA_API_TOKEN"] = token
    try:
        status = check_status()
        if "build_rev" in status:
            print(c("green", "✅ Connected!"))
            print(f"  {c('dim', 'Server:')} {status.get('build_rev', '?')}")
        else:
            print(c("yellow", "⚠️  Server offline — token saved anyway"))
    except Exception:
        print(c("yellow", "⚠️  Could not verify — token saved anyway"))

    cfg = _load_config()
    cfg["api_token"] = token
    _save_config(cfg)

    print()
    print(c("green", c("bold", "  ✅ ARIA Coder is ready!")))
    print()
    print(f"  Run {c('cyan', 'python aria.py')} to start coding.")
    print()


# ── Interactive coder shell ──────────────────────────────────────────────────


def print_banner() -> None:
    """Print the ARIA Coder banner."""
    width = min(shutil.get_terminal_size().columns, 80)
    line = "─" * (width - 4)

    print()
    print(c("bold", c("purple", f"  ╔{line}╗")))
    print(c("bold", c("purple", "  ║")), end="")
    title = c("bold", c("white", " ARIA CODER "))
    subtitle = c("dim", "v" + VERSION)
    mid = title + subtitle
    print(mid.ljust(width - 3) + c("bold", c("purple", "║")))
    print(c("bold", c("purple", f"  ╚{line}╝")))
    print()

    # Check connection
    try:
        status = check_status()
        if "build_rev" in status:
            rev = status.get("build_rev", "?")
            print(f"  {c('green', '●')} Engine: {c('dim', 'online')}  {c('grey', '(' + rev + ')')}")
        else:
            print(f"  {c('red', '●')} Engine: {c('dim', 'offline')}")
    except Exception:
        print(f"  {c('yellow', '●')} Engine: {c('dim', 'checking...')}")

    print(f"  {c('purple', '█')} I write code. I fix bugs. I build things.")
    print(f"  {c('dim', '    Describe what you want, and I will code it.')}")
    print()


def print_help() -> None:
    """Print help."""
    print()
    print(c("bold", c("purple", "  ─── ARIA Coder Commands ─────────────────────────")))
    print()
    print(f"    {c('cyan', 'help')}         Show this help")
    print(f"    {c('cyan', 'status')}       Check coder engine status")
    print(f"    {c('cyan', 'token')}        Set or change your API token")
    print(f"    {c('cyan', 'clear')}        Clear the screen")
    print(f"    {c('cyan', 'exit')}         Quit")
    print()
    print(c("bold", c("purple", "  ─── What I can code ──────────────────────────────")))
    print()
    print("    Just describe what you want built:")
    print()
    print(f"    {c('green', '→')}  \"Write a Python script to rename files in a folder\"")
    print(f"    {c('green', '→')}  \"Fix this bug: {c('red', 'KeyError')} when data is None\"")
    print(f"    {c('green', '→')}  \"Build a REST API with FastAPI for a todo app\"")
    print(f"    {c('green', '→')}  \"Refactor this function to use async/await\"")
    print(f"    {c('green', '→')}  \"Add error handling and retry logic to this code\"")
    print()
    print(f"  {c('dim', 'You can also paste code directly into the prompt.')}")
    print()


def print_status() -> None:
    """Print coder engine status."""
    print()
    print(c("bold", c("purple", "  ─── Coder Engine Status ──────────────────────────")))
    print()
    try:
        status = check_status()
        if "build_rev" in status:
            print(f"    {c('green', '●')} Engine:  {c('bold', 'ONLINE')}")
            print(f"    Build:   {status.get('build_rev', '?')}")
            uptime = status.get("uptime_seconds", 0)
            if uptime:
                h, m = int(uptime) // 3600, (int(uptime) % 3600) // 60
                print(f"    Uptime:  {h}h {m}m")
            print(f"    Server:  {_get_server()}")
            token = _get_token()
            if token:
                masked = token[:6] + "..." + token[-4:] if len(token) > 10 else "set"
                print(f"    Token:   {c('green', masked)}")
            else:
                print(f"    Token:   {c('red', 'not set')}")
        else:
            print(f"    {c('red', '●')} Engine:  {c('bold', 'OFFLINE')}")
            print(f"    Reason:  {status.get('message', 'unknown')}")
    except Exception as e:
        print(f"    {c('red', '●')} Engine:  {c('bold', 'ERROR')}")
        print(f"    {e}")
    print()


def interactive_shell() -> None:
    """Run the interactive ARIA Coder shell."""
    token = _get_token()
    if not token:
        print()
        print(c("red", "  ❌ No API token found."))
        print()
        print(f"  Run {c('cyan', 'python aria.py --setup')} to set one up.")
        print()
        return

    print_banner()

    while True:
        try:
            line = input(f"  {c('purple', 'aria')}{c('dim', '@coder')}> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            print(c("yellow", "\n  Later, coder."))
            break

        if not line:
            continue

        cmd = line.lower()
        if cmd in ("exit", "quit"):
            print(c("yellow", "  Later, coder."))
            break
        if cmd in ("clear", "cls"):
            os.system("cls" if platform.system() == "Windows" else "clear")
            print_banner()
            continue
        if cmd == "help":
            print_help()
            continue
        if cmd == "status":
            print_status()
            continue
        if cmd == "token":
            print()
            print(c("bold", c("purple", "  ─── Set API Token ───────────────────────────────")))
            print()
            print(f"  Open {c('green', _get_server() + '/token')} in your browser")
            print()
            new_token = input(f"  {c('yellow', 'Paste token')}> ").strip()
            if new_token:
                cfg = _load_config()
                cfg["api_token"] = new_token
                _save_config(cfg)
                os.environ["ARIA_API_TOKEN"] = new_token
                print(c("green", "  ✅ Token updated."))
            else:
                print(c("red", "  No token entered."))
            print()
            continue

        # ── Send to ARIA Coder ──────────────────────────────────────────────
        print()
        print(f"  {c('purple', '█')} {c('bold', 'Coding...')}  {c('dim', '(press Ctrl+C to cancel)')}")
        sys.stdout.flush()

        try:
            try:
                chunks = send_coder_task_stream(line)
                if chunks:
                    print()
                else:
                    result = send_coder_task(line)
                    response = result.get("response") or result.get("answer") or json.dumps(result, indent=2)
                    print(c("cyan", response))
            except AriaError as e:
                if "401" in str(e):
                    print(c("red", f"  ❌ {e}"))
                elif "timeout" in str(e).lower():
                    print(c("yellow", f"  ⏱️  {e}"))
                else:
                    print(c("red", f"  ❌ {e}"))
            except Exception as e:
                print(c("red", f"  ❌ Error: {e}"))
        except AriaError as e:
            print(c("red", f"  ❌ {e}"))
        except Exception as e:
            print(c("red", f"  ❌ Error: {e}"))

        print()


# ── CLI entry point ──────────────────────────────────────────────────────────


def main() -> None:
    args = sys.argv[1:]

    if not args:
        interactive_shell()
        return

    cmd = args[0]

    if cmd in ("--help", "-h", "/?"):
        print(__doc__)
        return

    if cmd in ("--version", "-v"):
        print(f"ARIA Coder v{VERSION}")
        return

    if cmd == "--setup":
        run_setup()
        return

    if cmd == "--status":
        print_status()
        return

    # Single-shot mode
    token = _get_token()
    if not token:
        print(c("red", "❌ No API token. Run: python aria.py --setup"))
        sys.exit(1)

    task = " ".join(args)
    print(f"  {c('purple', '█')} {c('bold', 'Coding...')}")
    print()

    try:
        result = send_coder_task(task)
        response = result.get("response") or result.get("answer") or json.dumps(result, indent=2)
        print(c("cyan", response))
        print()
    except AriaError as e:
        print(c("red", f"  ❌ {e}"))
        sys.exit(1)
    except Exception as e:
        print(c("red", f"  ❌ Error: {e}"))
        sys.exit(1)


if __name__ == "__main__":
    main()
