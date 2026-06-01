ARIA CLIENT v2.1
================

Connect to ARIA's main server from any Windows computer.

HOW TO USE:
  1. Double-click aria.bat
  2. When asked, open the link shown in your browser
  3. Copy the token from the webpage
  4. Paste it into the terminal
  5. Start asking questions!

That's it. No install needed. No Python knowledge required.

If you have Python installed, the experience is even better
(streaming responses, colours, command history).

WHAT YOU GET:
  - Full ARIA intelligence: research, analysis, code review, web search
  - Streaming responses (see results as they're generated)
  - Command history and tab completion (Python mode)
  - Clear error messages (no more "server may be busy")
  - Single-shot mode:  python aria.py "your question"

REQUIREMENTS:
  - Windows 10 or later
  - Internet connection
  - An ARIA access token (get one at https://aria-intel.fly.dev/token)

NO INSTALL NEEDED:
  - No Python required for basic use
  - No pip install required
  - No dependencies to download
  - Everything runs on the ARIA server

ADVANCED (Python mode):
  If you have Python 3.8+, the client automatically uses it for
  a better experience. Commands:
    python aria.py                  Interactive mode
    python aria.py "question"       Single question
    python aria.py --setup          Setup wizard
    python aria.py --status         Check server status

TROUBLESHOOTING:
  "Server unreachable" → Check your internet connection
  "Token invalid"      → Get a new token at https://aria-intel.fly.dev/token
  "Server busy"        → Wait a moment and try again
