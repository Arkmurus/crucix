#!/usr/bin/env python3
"""ARIA Local Launcher — run ARIA on any computer.

Usage:
    python scripts/aria_local_launcher.py [--port 8000] [--dev]

This script:
  1. Checks Python version (3.13+ required)
  2. Installs dependencies if missing
  3. Starts a local Redis (or uses SQLite)
  4. Launches ARIA's FastAPI server
  5. Opens the health endpoint to confirm it's running

No Docker, no Fly.io, no cloud needed. ARIA runs entirely on your machine.
"""
from __future__ import annotations

import os
import sys
import subprocess
import argparse
import webbrowser
import time
from pathlib import Path


REQUIRED_PYTHON = (3, 13)
REPO_ROOT = Path(__file__).parent.parent
ARIA_DIR = REPO_ROOT / "aria_service"
REQUIREMENTS = ARIA_DIR / "requirements.txt"


def check_python() -> bool:
    """Check Python version is 3.13+."""
    v = sys.version_info
    if v.major < REQUIRED_PYTHON[0] or (v.major == REQUIRED_PYTHON[0] and v.minor < REQUIRED_PYTHON[1]):
        print(f"❌ Python {REQUIRED_PYTHON[0]}.{REQUIRED_PYTHON[1]}+ required (have {v.major}.{v.minor}.{v.micro})")
        print(f"   Download from: https://www.python.org/downloads/")
        return False
    print(f"✅ Python {v.major}.{v.minor}.{v.micro}")
    return True


def install_deps() -> bool:
    """Install Python dependencies."""
    if not REQUIREMENTS.exists():
        print(f"❌ requirements.txt not found at {REQUIREMENTS}")
        return False

    print("📦 Installing dependencies (this may take a few minutes)...")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", str(REQUIREMENTS)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"❌ pip install failed: {result.stderr[:500]}")
        return False
    print("✅ Dependencies installed")
    return True


def check_env() -> bool:
    """Check required environment variables and set defaults for local dev."""
    env_file = REPO_ROOT / ".env"
    if env_file.exists():
        print(f"✅ .env file found at {env_file}")
        return True

    print("⚠️  No .env file found. Creating one with local-dev defaults...")
    with open(env_file, "w") as f:
        f.write("""# ARIA Local Development Configuration
# Copy this file to .env and fill in your API keys

# LLM Provider (DeepSeek is the default)
LLM_PROVIDER=deepseek
LLM_API_KEY=your_deepseek_key_here

# Internal auth token (set anything for local dev)
ARIA_INTERNAL_TOKEN=local-dev-token

# State backend (sqlite for local dev, no Redis needed)
ARIA_STATE_BACKEND=sqlite

# Autonomous mode (disabled by default for local dev)
ARIA_AUTONOMOUS_ENABLED=0
ARIA_CODER_ENABLED=0

# Logging
LOG_LEVEL=INFO
""")
    print(f"✅ Created {env_file} — edit it to add your API keys")
    return True


def launch(port: int = 8000, dev_mode: bool = False) -> None:
    """Launch ARIA's FastAPI server."""
    os.chdir(str(REPO_ROOT))

    # Set default env vars for local dev
    os.environ.setdefault("ARIA_STATE_BACKEND", "sqlite")
    os.environ.setdefault("ARIA_INTERNAL_TOKEN", "local-dev-token")
    os.environ.setdefault("ARIA_AUTONOMOUS_ENABLED", "0")
    os.environ.setdefault("ARIA_CODER_ENABLED", "0")

    cmd = [
        sys.executable, "-m", "uvicorn",
        "aria_service.main:app",
        "--host", "0.0.0.0",
        "--port", str(port),
    ]
    if dev_mode:
        cmd.append("--reload")

    print(f"\n🚀 Starting ARIA on http://localhost:{port}")
    print(f"   Health check: http://localhost:{port}/health/live")
    print(f"   Press Ctrl+C to stop\n")

    # Open browser after a brief delay
    def _open_browser():
        time.sleep(3)
        webbrowser.open(f"http://localhost:{port}/health/live")

    import threading
    threading.Thread(target=_open_browser, daemon=True).start()

    try:
        subprocess.run(cmd)
    except KeyboardInterrupt:
        print("\n👋 ARIA stopped")


def main():
    parser = argparse.ArgumentParser(description="Run ARIA locally")
    parser.add_argument("--port", type=int, default=8000, help="Port to run on")
    parser.add_argument("--dev", action="store_true", help="Enable auto-reload for development")
    parser.add_argument("--skip-deps", action="store_true", help="Skip dependency installation")
    args = parser.parse_args()

    print("╔══════════════════════════════════════╗")
    print("║     ARIA — Local Launcher v1.0       ║")
    print("╚══════════════════════════════════════╝")
    print()

    if not check_python():
        sys.exit(1)

    if not args.skip_deps:
        install_deps()

    check_env()
    launch(port=args.port, dev_mode=args.dev)


if __name__ == "__main__":
    main()
