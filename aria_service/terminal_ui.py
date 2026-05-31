#!/usr/bin/env python3
"""
ARIA Terminal UI - Professional Claude Code Style Interface
Complete with: Rich formatting, syntax highlighting, auto-completion, history, session management
"""

import os
import sys
import json
import asyncio
import threading
import time
try:
    import readline  # noqa: F401 — not available on Windows
    import rlcompleter  # noqa: F401
except ImportError:
    pass
import re
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple, Callable
from dataclasses import dataclass, field
from enum import Enum
import subprocess
import tempfile

# Rich library imports (install: pip install rich prompt-toolkit pygments)
try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.syntax import Syntax
    from rich.markdown import Markdown
    from rich.progress import Progress, SpinnerColumn, TextColumn
    from rich.layout import Layout
    from rich.live import Live
    from rich.text import Text
    from rich import print as rprint
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False
    print("⚠️  Run: pip install rich prompt-toolkit pygments")

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
    from prompt_toolkit.completion import WordCompleter
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.styles import Style
    from prompt_toolkit.formatted_text import HTML
    PROMPT_TOOLKIT_AVAILABLE = True
except ImportError:
    PROMPT_TOOLKIT_AVAILABLE = False
    
try:
    import pygments
    from pygments import highlight
    from pygments.lexers import get_lexer_by_name, guess_lexer
    from pygments.formatters import TerminalFormatter
    PYGMENTS_AVAILABLE = True
except ImportError:
    PYGMENTS_AVAILABLE = False

# ============================================================================
# ANSI COLOR CODES (Fallback when rich is unavailable)
# ============================================================================

class Colors:
    """ANSI color codes for terminal output"""
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    ITALIC = "\033[3m"
    
    # Foreground
    BLACK = "\033[30m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"
    
    # Bright
    BRIGHT_BLACK = "\033[90m"
    BRIGHT_RED = "\033[91m"
    BRIGHT_GREEN = "\033[92m"
    BRIGHT_YELLOW = "\033[93m"
    BRIGHT_BLUE = "\033[94m"
    BRIGHT_MAGENTA = "\033[95m"
    BRIGHT_CYAN = "\033[96m"
    BRIGHT_WHITE = "\033[97m"
    
    # Background
    BG_RED = "\033[41m"
    BG_GREEN = "\033[42m"
    BG_YELLOW = "\033[43m"
    BG_BLUE = "\033[44m"
    BG_MAGENTA = "\033[45m"
    BG_CYAN = "\033[46m"

# ============================================================================
# CONFIGURATION
# ============================================================================

@dataclass
class TerminalConfig:
    """Terminal UI configuration"""
    theme: str = "dark"  # dark, light, claude
    show_timestamps: bool = True
    show_cost: bool = True
    show_tokens: bool = True
    auto_save_session: bool = True
    max_history: int = 1000
    syntax_highlight: bool = True
    compact_mode: bool = False
    no_color: bool = False
    
    @classmethod
    def from_env(cls):
        return cls(
            theme=os.environ.get("ARIA_THEME", "dark"),
            show_timestamps=os.environ.get("ARIA_SHOW_TIMESTAMPS", "true").lower() == "true",
            show_cost=os.environ.get("ARIA_SHOW_COST", "true").lower() == "true",
            show_tokens=os.environ.get("ARIA_SHOW_TOKENS", "true").lower() == "true",
            auto_save_session=os.environ.get("ARIA_AUTO_SAVE", "true").lower() == "true",
            compact_mode=os.environ.get("ARIA_COMPACT", "false").lower() == "true",
            no_color=os.environ.get("NO_COLOR", "false").lower() == "true" or os.environ.get("ARIA_NO_COLOR", "false").lower() == "true",
        )

# ============================================================================
# MESSAGE TYPES
# ============================================================================

class MessageRole(Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"
    ERROR = "error"

@dataclass
class Message:
    """Chat message"""
    role: MessageRole
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    tool_calls: Optional[List[Dict]] = None
    tool_results: Optional[List[Any]] = None
    tokens: Optional[int] = None
    cost: Optional[float] = None
    
@dataclass
class Session:
    """Chat session"""
    id: str
    name: str
    messages: List[Message]
    created_at: datetime
    updated_at: datetime
    total_tokens: int = 0
    total_cost: float = 0.0

# ============================================================================
# SESSION MANAGER
# ============================================================================

class SessionManager:
    """Manages chat sessions with persistence"""
    
    def __init__(self, sessions_dir: Path = Path.home() / ".aria" / "sessions"):
        self.sessions_dir = sessions_dir
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.current_session: Optional[Session] = None
        self._load_sessions()
        
    def _load_sessions(self):
        """Load existing sessions from disk"""
        self.sessions: Dict[str, Session] = {}
        for session_file in self.sessions_dir.glob("*.json"):
            try:
                with open(session_file, 'r') as f:
                    data = json.load(f)
                    session = self._deserialize_session(data)
                    self.sessions[session.id] = session
            except Exception:
                pass
                
    def _serialize_session(self, session: Session) -> Dict:
        """Convert session to JSON-serializable dict"""
        return {
            "id": session.id,
            "name": session.name,
            "messages": [
                {
                    "role": m.role.value,
                    "content": m.content,
                    "timestamp": m.timestamp.isoformat(),
                    "tokens": m.tokens,
                    "cost": m.cost
                }
                for m in session.messages
            ],
            "created_at": session.created_at.isoformat(),
            "updated_at": session.updated_at.isoformat(),
            "total_tokens": session.total_tokens,
            "total_cost": session.total_cost
        }
        
    def _deserialize_session(self, data: Dict) -> Session:
        """Convert dict back to Session"""
        return Session(
            id=data["id"],
            name=data["name"],
            messages=[
                Message(
                    role=MessageRole(m["role"]),
                    content=m["content"],
                    timestamp=datetime.fromisoformat(m["timestamp"]),
                    tokens=m.get("tokens"),
                    cost=m.get("cost")
                )
                for m in data["messages"]
            ],
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            total_tokens=data.get("total_tokens", 0),
            total_cost=data.get("total_cost", 0.0)
        )
        
    def create_session(self, name: str = None) -> Session:
        """Create a new session"""
        session_id = datetime.now().strftime("%Y%m%d_%H%M%S%f")
        if not name:
            name = f"Session {session_id}"
            
        session = Session(
            id=session_id,
            name=name,
            messages=[],
            created_at=datetime.now(),
            updated_at=datetime.now()
        )
        self.sessions[session_id] = session
        self.current_session = session
        self._save_session(session)
        return session
        
    def _save_session(self, session: Session):
        """Save session to disk"""
        session_file = self.sessions_dir / f"{session.id}.json"
        with open(session_file, 'w') as f:
            json.dump(self._serialize_session(session), f, indent=2)
            
    def add_message(self, message: Message):
        """Add message to current session"""
        if not self.current_session:
            self.create_session()
            
        self.current_session.messages.append(message)
        self.current_session.updated_at = datetime.now()
        
        if message.tokens:
            self.current_session.total_tokens += message.tokens
        if message.cost:
            self.current_session.total_cost += message.cost
            
        self._save_session(self.current_session)
        
    def get_sessions(self) -> List[Session]:
        """Get all sessions"""
        return list(self.sessions.values())
        
    def load_session(self, session_id: str) -> Optional[Session]:
        """Load a specific session"""
        if session_id in self.sessions:
            self.current_session = self.sessions[session_id]
            return self.current_session
        return None
        
    def delete_session(self, session_id: str):
        """Delete a session"""
        if session_id in self.sessions:
            session_file = self.sessions_dir / f"{session_id}.json"
            session_file.unlink(missing_ok=True)
            del self.sessions[session_id]

# ============================================================================
# TOOL COMPLETER
# ============================================================================

class AriaCompleter:
    """Auto-completion for ARIA commands"""
    
    COMMANDS = [
        "/help", "/exit", "/quit", "/reset", "/clear", "/status",
        "/session", "/sessions", "/load", "/save", "/delete",
        "/cost", "/tokens", "/history", "/export", "/theme",
        "/model", "/compact", "/verbose"
    ]
    
    def __init__(self):
        if PROMPT_TOOLKIT_AVAILABLE:
            self.completer = WordCompleter(self.COMMANDS, ignore_case=True)
        else:
            self.completer = None
        
    def get_completer(self):
        return self.completer

# ============================================================================
# RENDERER
# ============================================================================

class AriaRenderer:
    """Handles output rendering with proper formatting"""
    
    def __init__(self, config: TerminalConfig):
        self.config = config
        self.console = Console() if RICH_AVAILABLE and not config.no_color else None
        
    def print_banner(self):
        """Print ARIA banner"""
        banner = f"""
{Colors.BRIGHT_CYAN}╔═══════════════════════════════════════════════════════════════════╗
║                                                                           ║
║   █████╗ ██████╗ ██╗ █████╗     ██████╗ ██████╗ ██████╗ ███████╗██████╗   ║
║  ██╔══██╗██╔══██╗██║██╔══██╗    ██╔══██╗██╔══██╗██╔══██╗██╔════╝██╔══██╗  ║
║  ███████║██████╔╝██║███████║    ██████╔╝██████╔╝██████╔╝█████╗  ██║  ██║  ║
║  ██╔══██║██╔══██╗██║██╔══██║    ██╔══██╗██╔══██╗██╔══██╗██╔══╝  ██║  ██║  ║
║  ██║  ██║██║  ██║██║██║  ██║    ██║  ██║██║  ██║██████╔╝███████╗██████╔╝  ║
║  ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝╚═╝  ╚═╝    ╚═╝  ╚═╝╚═╝  ╚═╝╚═════╝ ╚══════╝╚═════╝   ║
║                                                                           ║
║                    Autonomous Research Intelligence Agent                  ║
║                                v1.0.0                                      ║
╚═══════════════════════════════════════════════════════════════════╝{Colors.RESET}
"""
        print(banner)
        
    def print_help(self):
        """Print help panel"""
        help_text = f"""
{Colors.BRIGHT_CYAN}📖 ARIA Commands{Colors.RESET}
{Colors.DIM}────────────────────────────────────────────────────────────────────{Colors.RESET}

{Colors.BRIGHT_GREEN}/help{Colors.RESET}              Show this help message
{Colors.BRIGHT_GREEN}/clear{Colors.RESET}             Clear the terminal screen
{Colors.BRIGHT_GREEN}/reset{Colors.RESET}             Reset current conversation
{Colors.BRIGHT_GREEN}/status{Colors.RESET}            Show session status
{Colors.BRIGHT_GREEN}/cost{Colors.RESET}              Show token usage and cost
{Colors.BRIGHT_GREEN}/history{Colors.RESET}           Show conversation history
{Colors.BRIGHT_GREEN}/export{Colors.RESET}            Export conversation to file
{Colors.BRIGHT_GREEN}/session{Colors.RESET}           Manage sessions (/session new, list, load)
{Colors.BRIGHT_GREEN}/model{Colors.RESET}             Switch LLM model
{Colors.BRIGHT_GREEN}/theme{Colors.RESET}             Change color theme
{Colors.BRIGHT_GREEN}/exit{Colors.RESET}              Exit ARIA

{Colors.DIM}────────────────────────────────────────────────────────────────────{Colors.RESET}

{Colors.BRIGHT_YELLOW}💡 Tips:{Colors.RESET}
  • Tab completion available for commands
  • Use ↑/↓ for command history
  • Ctrl+C to interrupt generation
  • Type any question to start chatting

{Colors.BRIGHT_CYAN}🔧 Tools Available:{Colors.RESET}
  📁 File Operations  |  🔍 Search & Grep  |  💻 Run Commands
  🌐 Web Research    |  📊 DD Reports     |  📄 Document Analysis
{Colors.RESET}
"""
        print(help_text)
        
    def format_message(self, message: Message) -> str:
        """Format a single message"""
        timestamp = ""
        if self.config.show_timestamps:
            timestamp = f"{Colors.DIM}{message.timestamp.strftime('%H:%M:%S')}{Colors.RESET} "
            
        if message.role == MessageRole.USER:
            return f"\n{timestamp}{Colors.BRIGHT_GREEN}you{Colors.RESET} › {message.content}"
            
        elif message.role == MessageRole.ASSISTANT:
            header = f"{timestamp}{Colors.BRIGHT_CYAN}aria{Colors.RESET} ›"
            
            # Format code blocks
            content = message.content
            if PYGMENTS_AVAILABLE and self.config.syntax_highlight:
                content = self._highlight_code_blocks(content)
                
            return f"\n{header}\n{content}"
            
        elif message.role == MessageRole.TOOL:
            return f"{timestamp}{Colors.BRIGHT_YELLOW}🔧 tool{Colors.RESET} › {message.content[:200]}..."
            
        elif message.role == MessageRole.ERROR:
            return f"{timestamp}{Colors.BRIGHT_RED}⚠️ error{Colors.RESET} › {message.content}"
            
        return f"{timestamp}{message.content}"
        
    def _highlight_code_blocks(self, text: str) -> str:
        """Highlight code blocks in markdown"""
        pattern = r'```(\w*)\n(.*?)```'
        
        def replace_block(match):
            lang = match.group(1) or 'text'
            code = match.group(2)
            try:
                lexer = get_lexer_by_name(lang)
                highlighted = highlight(code, lexer, TerminalFormatter())
                return f"\n{highlighted}\n"
            except:
                return f"\n```{lang}\n{code}\n```\n"
                
        return re.sub(pattern, replace_block, text, flags=re.DOTALL)
        
    def print_status(self, session: Session, current_model: str):
        """Print status panel"""
        status_text = f"""
{Colors.BRIGHT_CYAN}📊 Session Status{Colors.RESET}
{Colors.DIM}────────────────────────────────────────────────────────────────────{Colors.RESET}

{Colors.BRIGHT_GREEN}Session:{Colors.RESET}     {session.name}
{Colors.BRIGHT_GREEN}Messages:{Colors.RESET}    {len(session.messages)}
{Colors.BRIGHT_GREEN}Model:{Colors.RESET}       {current_model}
{Colors.BRIGHT_GREEN}Tokens:{Colors.RESET}      {session.total_tokens:,}
{Colors.BRIGHT_GREEN}Cost:{Colors.RESET}        ${session.total_cost:.4f}

{Colors.DIM}────────────────────────────────────────────────────────────────────{Colors.RESET}
"""
        print(status_text)
        
    def print_cost_summary(self, session: Session):
        """Print cost summary"""
        cost_text = f"""
{Colors.BRIGHT_YELLOW}💰 Cost Summary{Colors.RESET}
{Colors.DIM}────────────────────────────────────────────────────────────────────{Colors.RESET}

{Colors.BRIGHT_GREEN}Total Tokens:{Colors.RESET}     {session.total_tokens:,}
{Colors.BRIGHT_GREEN}Total Cost:{Colors.RESET}       ${session.total_cost:.4f}
{Colors.BRIGHT_GREEN}Avg per message:{Colors.RESET}  ${session.total_cost / max(1, len(session.messages)):.4f}

{Colors.DIM}────────────────────────────────────────────────────────────────────{Colors.RESET}
"""
        print(cost_text)
        
    def print_history(self, session: Session, limit: int = 20):
        """Print conversation history"""
        messages = session.messages[-limit:]
        
        print(f"\n{Colors.BRIGHT_CYAN}📜 Conversation History{Colors.RESET}")
        print(f"{Colors.DIM}────────────────────────────────────────────────────────────────────{Colors.RESET}\n")
        
        for msg in messages:
            print(self.format_message(msg))
            
        print(f"\n{Colors.DIM}────────────────────────────────────────────────────────────────────{Colors.RESET}")
        
    def print_tool_progress(self, tool_name: str, status: str):
        """Print tool execution progress"""
        icons = {
            "start": "🔄",
            "running": "⚙️",
            "complete": "✅",
            "error": "❌"
        }
        icon = icons.get(status, "•")
        
        if self.config.compact_mode:
            print(f"{Colors.DIM}{icon} {tool_name}{Colors.RESET}", end=" ")
        else:
            print(f"\n{Colors.BRIGHT_YELLOW}{icon} {tool_name}{Colors.RESET} › {status}")
            
    def clear_screen(self):
        """Clear terminal screen"""
        os.system('cls' if os.name == 'nt' else 'clear')
        
    def print_error(self, error: str):
        """Print error message"""
        print(f"\n{Colors.BRIGHT_RED}❌ Error:{Colors.RESET} {error}")
        
    def print_success(self, message: str):
        """Print success message"""
        print(f"\n{Colors.BRIGHT_GREEN}✅ {message}{Colors.RESET}")
        
    def print_info(self, message: str):
        """Print info message"""
        print(f"\n{Colors.BRIGHT_CYAN}ℹ️ {message}{Colors.RESET}")

# ============================================================================
# PROGRESS ANIMATION
# ============================================================================

class ProgressSpinner:
    """Simple spinner for long operations"""
    
    def __init__(self, message: str = "Processing"):
        self.message = message
        self.spinner = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']
        self.running = False
        self.thread = None
        
    def _spin(self):
        idx = 0
        while self.running:
            sys.stdout.write(f"\r{Colors.BRIGHT_CYAN}{self.spinner[idx]} {self.message}...{Colors.RESET}")
            sys.stdout.flush()
            idx = (idx + 1) % len(self.spinner)
            time.sleep(0.1)
        sys.stdout.write("\r" + " " * (len(self.message) + 10) + "\r")
        sys.stdout.flush()
        
    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._spin, daemon=True)
        self.thread.start()
        
    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=0.5)

# ============================================================================
# MARKDOWN RENDERER
# ============================================================================

class MarkdownRenderer:
    """Renders markdown content nicely"""
    
    @staticmethod
    def render(text: str) -> str:
        """Render markdown to terminal"""
        if RICH_AVAILABLE:
            return str(Markdown(text))
        
        # Simple markdown fallback
        lines = []
        for line in text.split('\n'):
            # Headers
            if line.startswith('# '):
                lines.append(f"{Colors.BOLD}{Colors.BRIGHT_CYAN}{line[2:]}{Colors.RESET}")
            elif line.startswith('## '):
                lines.append(f"{Colors.BRIGHT_CYAN}{line[3:]}{Colors.RESET}")
            elif line.startswith('### '):
                lines.append(f"{Colors.CYAN}{line[4:]}{Colors.RESET}")
            # Bold
            elif '**' in line:
                line = re.sub(r'\*\*(.+?)\*\*', f'{Colors.BOLD}\\1{Colors.RESET}', line)
                lines.append(line)
            # Code
            elif line.startswith('```'):
                continue
            elif line.strip() and not line.startswith('```'):
                lines.append(line)
            else:
                lines.append(line)
                
        return '\n'.join(lines)

# ============================================================================
# COMMAND PARSER
# ============================================================================

class CommandParser:
    """Parses and executes ARIA commands"""
    
    def __init__(self, ui: 'AriaTerminalUI'):
        self.ui = ui
        
    def execute(self, command: str) -> bool:
        """Execute a command, return True if should continue"""
        cmd = command.lower().strip()
        
        if cmd == "/help":
            self.ui.renderer.print_help()
        elif cmd == "/clear":
            self.ui.renderer.clear_screen()
            self.ui.renderer.print_banner()
        elif cmd == "/reset":
            self.ui.session_manager.create_session()
            self.ui.renderer.print_success("Conversation reset")
        elif cmd == "/status":
            self.ui.renderer.print_status(
                self.ui.session_manager.current_session,
                self.ui.current_model
            )
        elif cmd == "/cost":
            self.ui.renderer.print_cost_summary(self.ui.session_manager.current_session)
        elif cmd == "/history":
            self.ui.renderer.print_history(self.ui.session_manager.current_session)
        elif cmd == "/sessions":
            self._list_sessions()
        elif cmd.startswith("/session"):
            self._handle_session(cmd)
        elif cmd.startswith("/model"):
            self._handle_model(cmd)
        elif cmd.startswith("/theme"):
            self._handle_theme(cmd)
        elif cmd == "/export":
            self._export_session()
        elif cmd in ["/exit", "/quit"]:
            return False
        else:
            self.ui.renderer.print_error(f"Unknown command: {command}")
            
        return True
        
    def _list_sessions(self):
        """List all sessions"""
        sessions = self.ui.session_manager.get_sessions()
        print(f"\n{Colors.BRIGHT_CYAN}📋 Sessions{Colors.RESET}")
        print(f"{Colors.DIM}────────────────────────────────────────────────────────────────────{Colors.RESET}")
        for session in sessions[-10:]:
            current = " ✓" if session.id == self.ui.session_manager.current_session.id else ""
            print(f"  {session.id} - {session.name} ({len(session.messages)} msgs){current}")
        print()
        
    def _handle_session(self, cmd: str):
        """Handle session subcommands"""
        parts = cmd.split()
        if len(parts) < 2:
            self.ui.renderer.print_error("Usage: /session [new|load|list|delete]")
            return
            
        subcmd = parts[1]
        
        if subcmd == "new":
            name = parts[2] if len(parts) > 2 else None
            self.ui.session_manager.create_session(name)
            self.ui.renderer.print_success(f"Created new session: {self.ui.session_manager.current_session.name}")
            
        elif subcmd == "list":
            self._list_sessions()
            
        elif subcmd == "load":
            if len(parts) < 3:
                self.ui.renderer.print_error("Usage: /session load <session_id>")
                return
            session = self.ui.session_manager.load_session(parts[2])
            if session:
                self.ui.renderer.print_success(f"Loaded session: {session.name}")
            else:
                self.ui.renderer.print_error(f"Session not found: {parts[2]}")
                
        elif subcmd == "delete":
            if len(parts) < 3:
                self.ui.renderer.print_error("Usage: /session delete <session_id>")
                return
            self.ui.session_manager.delete_session(parts[2])
            self.ui.renderer.print_success(f"Deleted session: {parts[2]}")
            
    def _handle_model(self, cmd: str):
        """Handle model switching"""
        parts = cmd.split()
        if len(parts) < 2:
            self.ui.renderer.print_info(f"Current model: {self.ui.current_model}")
            return
            
        new_model = parts[1]
        self.ui.current_model = new_model
        self.ui.renderer.print_success(f"Switched to model: {new_model}")
        
    def _handle_theme(self, cmd: str):
        """Handle theme switching"""
        parts = cmd.split()
        if len(parts) < 2:
            self.ui.renderer.print_info(f"Current theme: {self.ui.config.theme}")
            return
            
        theme = parts[1]
        if theme in ["dark", "light", "claude"]:
            self.ui.config.theme = theme
            self.ui.renderer.print_success(f"Switched to theme: {theme}")
        else:
            self.ui.renderer.print_error(f"Unknown theme: {theme}")
            
    def _export_session(self):
        """Export current session to file"""
        session = self.ui.session_manager.current_session
        export_dir = Path.home() / "Desktop" / "aria_exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        
        export_file = export_dir / f"aria_session_{session.id}.txt"
        
        with open(export_file, 'w') as f:
            f.write(f"ARIA Session: {session.name}\n")
            f.write(f"Created: {session.created_at}\n")
            f.write(f"Messages: {len(session.messages)}\n")
            f.write("=" * 60 + "\n\n")
            
            for msg in session.messages:
                f.write(f"[{msg.timestamp.strftime('%H:%M:%S')}] {msg.role.value.upper()}:\n")
                f.write(f"{msg.content}\n")
                f.write("-" * 40 + "\n")
                
        self.ui.renderer.print_success(f"Session exported to: {export_file}")

# ============================================================================
# MAIN TERMINAL UI
# ============================================================================

class AriaTerminalUI:
    """Main terminal UI class"""
    
    def __init__(self, config: Optional[TerminalConfig] = None):
        self.config = config or TerminalConfig.from_env()
        self.renderer = AriaRenderer(self.config)
        self.session_manager = SessionManager()
        self.completer = AriaCompleter()
        self.command_parser = CommandParser(self)
        self.current_model = os.environ.get("ARIA_MODEL", "deepseek/deepseek-chat")
        
        # Initialize prompt session if available
        if PROMPT_TOOLKIT_AVAILABLE:
            history_file = Path.home() / ".aria" / "history.txt"
            history_file.parent.mkdir(parents=True, exist_ok=True)
            
            self.prompt_session = PromptSession(
                history=FileHistory(str(history_file)),
                auto_suggest=AutoSuggestFromHistory(),
                completer=self.completer.get_completer(),
                style=Style.from_dict({
                    'prompt': 'ansicyan bold',
                })
            )
        else:
            self.prompt_session = None
            
        # Create initial session
        self.session_manager.create_session()
        
    def get_prompt(self) -> str:
        """Get the prompt string"""
        if self.config.compact_mode:
            return f"{Colors.BRIGHT_GREEN}aria>{Colors.RESET} "
        return f"\n{Colors.BRIGHT_GREEN}you{Colors.RESET} › "
        
    async def send_message(self, content: str):
        """Send a message to ARIA"""
        # Add user message
        user_msg = Message(role=MessageRole.USER, content=content)
        self.session_manager.add_message(user_msg)
        
        # Show user message
        print(self.renderer.format_message(user_msg))
        
        # Show thinking indicator
        spinner = ProgressSpinner("ARIA is thinking")
        spinner.start()
        
        try:
            # Call the ARIA service API
            import httpx
            aria_url = os.environ.get("ARIA_SERVICE_URL", "http://localhost:8000")
            api_key = os.environ.get("ARIA_API_KEY", "")
            
            headers = {"Content-Type": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            
            payload = {
                "message": content,
                "session_id": self.session_manager.current_session.id,
                "stream": False,
            }
            
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    f"{aria_url}/api/aria/chat",
                    json=payload,
                    headers=headers,
                )
                response.raise_for_status()
                result = response.json()
            
            response_content = result.get("response", "")
            tokens_used = result.get("tokens", 0)
            cost = result.get("cost", 0.0)
            
            # Add assistant response
            assistant_msg = Message(
                role=MessageRole.ASSISTANT,
                content=response_content,
                tokens=tokens_used or None,
                cost=cost or None,
            )
            self.session_manager.add_message(assistant_msg)
            
            # Show response
            spinner.stop()
            print(self.renderer.format_message(assistant_msg))
            
            # Show cost if enabled
            if self.config.show_cost and assistant_msg.cost:
                print(f"{Colors.DIM}  └─ 💰 ${assistant_msg.cost:.6f} | {assistant_msg.tokens} tokens{Colors.RESET}")
                
        except ImportError:
            # httpx not installed — fall back to simulated response
            spinner.stop()
            await asyncio.sleep(0.5)
            response_content = f"I received your message: {content[:200]}\n\n(Install httpx for live API: pip install httpx)"
            assistant_msg = Message(role=MessageRole.ASSISTANT, content=response_content, tokens=0, cost=0.0)
            self.session_manager.add_message(assistant_msg)
            print(self.renderer.format_message(assistant_msg))
            print(f"{Colors.DIM}  └─ ⚠️  Install httpx for live ARIA API calls{Colors.RESET}")
                
        except Exception as e:
            spinner.stop()
            error_msg = Message(role=MessageRole.ERROR, content=str(e))
            self.session_manager.add_message(error_msg)
            print(self.renderer.format_message(error_msg))
            
    def run(self):
        """Run the terminal UI"""
        self.renderer.clear_screen()
        self.renderer.print_banner()
        self.renderer.print_help()
        
        # Show status bar
        print(f"{Colors.DIM}{'─' * 60}{Colors.RESET}")
        print(f"{Colors.BRIGHT_BLACK}Model: {self.current_model} | Session: {self.session_manager.current_session.name}{Colors.RESET}")
        print(f"{Colors.DIM}{'─' * 60}{Colors.RESET}\n")
        
        # Main loop
        while True:
            try:
                # Get user input
                if self.prompt_session:
                    user_input = self.prompt_session.prompt(self.get_prompt())
                else:
                    user_input = input(self.get_prompt())
                    
                user_input = user_input.strip()
                
                if not user_input:
                    continue
                    
                # Check if it's a command
                if user_input.startswith("/"):
                    should_continue = self.command_parser.execute(user_input)
                    if not should_continue:
                        break
                    continue
                    
                # Send as message
                asyncio.run(self.send_message(user_input))
                
            except KeyboardInterrupt:
                print(f"\n{Colors.DIM}Use /exit to quit{Colors.RESET}")
                continue
            except EOFError:
                break
                
        # Clean exit
        print(f"\n{Colors.BRIGHT_CYAN}Goodbye! 👋{Colors.RESET}")
        
    def stop(self):
        """Stop the UI"""
        self.session_manager._save_session(self.session_manager.current_session)

# ============================================================================
# ENTRY POINT
# ============================================================================

def main():
    """Main entry point"""
    # Parse command line arguments
    import argparse
    parser = argparse.ArgumentParser(description="ARIA Terminal UI")
    parser.add_argument("--theme", choices=["dark", "light", "claude"], help="Color theme")
    parser.add_argument("--compact", action="store_true", help="Compact mode")
    parser.add_argument("--no-color", action="store_true", help="Disable colors")
    parser.add_argument("--session", help="Load specific session")
    
    args = parser.parse_args()
    
    # Create config
    config = TerminalConfig.from_env()
    if args.theme:
        config.theme = args.theme
    if args.compact:
        config.compact_mode = True
    if args.no_color:
        config.no_color = True
        
    # Run UI
    ui = AriaTerminalUI(config)
    
    if args.session:
        ui.session_manager.load_session(args.session)
        
    try:
        ui.run()
    except Exception as e:
        print(f"Fatal error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()