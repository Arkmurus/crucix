ARIA CLIENT v2.1
================

Connect to ARIA's main server from any Windows computer.

QUICK START:
  1. Open cmd in this folder
  2. Run:  python aria.py --setup
  3. Paste your API token when prompted
  4. Type:  python aria.py
  5. Ask ARIA anything

Or just double-click aria.bat (requires Python).

WHAT YOU GET:
  - Full ARIA intelligence: research, analysis, code review, web search
  - Streaming responses (see results as they're generated)
  - Command history and tab completion
  - Clear error messages (no more "server may be busy")
  - Single-shot mode:  python aria.py "your question"

REQUIREMENTS:
  - Python 3.8+ (recommended)
  - Internet connection
  - ARIA API token (get one at https://intel.arkmurus.com)

NO INSTALL NEEDED:
  - No pip install required (uses only Python standard library)
  - No dependencies to download
  - Everything runs on the ARIA server

COMMANDS:
  python aria.py                  Interactive mode
  python aria.py "question"       Single question
  python aria.py --setup          Setup wizard (get token)
  python aria.py --status         Check server status
  python aria.py --help           Show help

ENVIRONMENT VARIABLES:
  ARIA_API_TOKEN    Your API token (or use --setup to save it)
  ARIA_SERVER       Server URL (default: https://aria-intel.fly.dev)

TROUBLESHOOTING:
  "401 Unauthorized" → Run: python aria.py --setup
  "Connection error" → Check your internet and run: python aria.py --status
  "Server error"     → The server may be restarting. Try again in a moment.
