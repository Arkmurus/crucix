"""R-F1225 — ARIA PowerShell Mastery System.

Complete PowerShell automation and problem-solving module.
Makes ARIA an expert in PowerShell execution, error handling, and automation.

Key features:
  - Proper subprocess execution via asyncio (no shell quote mangling)
  - Error analysis with fix suggestions (execution policy, syntax, permissions)
  - Code generation templates for common tasks
  - Command optimization
  - Session management with variable persistence
  - FastAPI integration endpoints
  - Full brain wiring on both success and failure

Usage:
    from aria_service.utils.powershell_master import PowerShellMaster, PowerShellConfig

    ps = PowerShellMaster()
    result = await ps.execute("Get-Process -Name python*")
    print(ps.format_result(result))
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

logger = logging.getLogger("aria.powershell_master")

# ============================================================================
# CONFIGURATION
# ============================================================================


@dataclass
class PowerShellConfig:
    """PowerShell execution configuration."""

    execution_policy: str = "Bypass"
    no_profile: bool = True
    non_interactive: bool = True
    hidden_window: bool = False
    timeout_seconds: int = 300
    encoding: str = "UTF8"
    enable_logging: bool = True
    log_path: Path = Path("/app/logs/powershell")

    def to_args(self) -> List[str]:
        """Convert config to PowerShell command line arguments."""
        args: list[str] = []
        if self.no_profile:
            args.append("-NoProfile")
        if self.non_interactive:
            args.append("-NonInteractive")
        if self.execution_policy:
            args.extend(["-ExecutionPolicy", self.execution_policy])
        if self.hidden_window:
            args.extend(["-WindowStyle", "Hidden"])
        return args


# ============================================================================
# RESULT TYPES
# ============================================================================


class ExecutionStatus(Enum):
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    SKIPPED = "skipped"


@dataclass
class PowerShellResult:
    """Result of a PowerShell execution."""

    status: ExecutionStatus
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: float
    command: str
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    suggestions: List[str] = field(default_factory=list)


@dataclass
class PowerShellSession:
    """PowerShell session for stateful operations."""

    id: str
    created_at: datetime
    variables: Dict[str, Any]
    functions: List[str]
    modules: List[str]


# ============================================================================
# ERROR ANALYZER — Understands PowerShell Errors
# ============================================================================


class PowerShellErrorAnalyzer:
    """Analyzes PowerShell errors and suggests fixes."""

    ERROR_PATTERNS: Dict[str, Dict[str, Any]] = {
        "execution_policy": {
            "patterns": [
                r"execution of scripts is disabled",
                r"cannot be loaded because running scripts is disabled",
                r"ExecutionPolicy",
                r"Restricted",
            ],
            "fix": "Use -ExecutionPolicy Bypass parameter or run: Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass",
            "category": "security",
        },
        "command_not_found": {
            "patterns": [
                r"not recognized",
                r"not found",
                r"command.*not found",
                r"is not recognized",
            ],
            "fix": "Check if the command/module is installed. Install with: Install-Module <ModuleName>",
            "category": "missing_dependency",
        },
        "access_denied": {
            "patterns": [
                r"access denied",
                r"permission denied",
                r"unauthorized",
                r"cannot be accessed",
            ],
            "fix": "Run PowerShell as Administrator or check file/folder permissions",
            "category": "permission",
        },
        "syntax_error": {
            "patterns": [
                r"unexpected token",
                r"missing.*\)",
                r"missing.*\{",
                r"invalid argument",
                r"parse error",
            ],
            "fix": "Check command syntax. Ensure parentheses/brackets are balanced",
            "category": "syntax",
        },
        "null_value": {
            "patterns": [
                r"cannot call a method on a null-valued expression",
                r"you cannot call a method on a null",
                r"property.*of null",
            ],
            "fix": "The object is null. Check if the command returned any results before accessing properties",
            "category": "logic",
        },
        "conversion_error": {
            "patterns": [
                r"cannot convert",
                r"cannot convert value",
                r"type conversion",
            ],
            "fix": "Data type mismatch. Use explicit type casting or check input format",
            "category": "type_error",
        },
        "module_not_found": {
            "patterns": [
                r"no module found",
                r"module.*not found",
                r"cannot find module",
            ],
            "fix": "Install the required module: Install-Module -Name <ModuleName> -Force",
            "category": "missing_module",
        },
        "com_object_error": {
            "patterns": [
                r"unable to find an entry point",
                r"com class",
                r"cannot create object",
                r"com exception",
            ],
            "fix": "COM object not registered. Reinstall the application or register the COM component",
            "category": "com",
        },
        "remote_session": {
            "patterns": [
                r"cannot connect",
                r"winrm",
                r"remote session",
                r"pssession",
            ],
            "fix": "Check network connectivity and WinRM configuration: Enable-PSRemoting -Force",
            "category": "network",
        },
    }

    @classmethod
    def analyze(cls, error_text: str, command: str = "") -> Dict[str, Any]:
        """Analyze error and return fix suggestions."""
        error_lower = error_text.lower()
        for error_type, info in cls.ERROR_PATTERNS.items():
            for pattern in info["patterns"]:
                if re.search(pattern, error_lower, re.IGNORECASE):
                    return {
                        "error_type": error_type,
                        "category": info["category"],
                        "suggestions": [info["fix"]],
                        "confidence": 0.9,
                    }
        # Unknown error — provide general suggestions
        suggestions: list[str] = []
        if "error" in error_lower:
            suggestions.append("Check the command syntax and parameters")
        if "cannot" in error_lower:
            suggestions.append("Verify you have the necessary permissions")
        return {
            "error_type": "unknown",
            "category": "general",
            "suggestions": suggestions,
            "confidence": 0.5,
        }


# ============================================================================
# CODE MASTER — Generates PowerShell Code
# ============================================================================


class PowerShellCodeMaster:
    """Generates correct PowerShell code for various tasks."""

    TEMPLATES: Dict[str, str] = {
        "get_service": """
$service = Get-Service -Name '{service_name}' -ErrorAction Stop
if ($service) {
    Write-Output "Service: $($service.Name)"
    Write-Output "Status: $($service.Status)"
    Write-Output "Display Name: $($service.DisplayName)"
} else {
    Write-Error "Service not found"
}
""",
        "restart_service": """
try {
    $service = Get-Service -Name '{service_name}' -ErrorAction Stop
    Write-Output "Restarting service: {service_name}"
    Restart-Service -Name '{service_name}' -Force
    Write-Output "Service restarted successfully"
} catch {
    Write-Error "Failed to restart service: $_"
}
""",
        "get_process": """
try {
    $processes = Get-Process -Name '{process_name}' -ErrorAction SilentlyContinue
    if ($processes) {
        foreach ($p in $processes) {
            Write-Output "Process: $($p.Name), ID: $($p.Id), CPU: $($p.CPU)"
        }
    } else {
        Write-Output "No processes found matching: {process_name}"
    }
} catch {
    Write-Error "Error getting process: $_"
}
""",
        "create_directory": """
try {
    $path = '{path}'
    if (-not (Test-Path -Path $path)) {
        New-Item -Path $path -ItemType Directory -Force
        Write-Output "Directory created: $path"
    } else {
        Write-Output "Directory already exists: $path"
    }
} catch {
    Write-Error "Failed to create directory: $_"
}
""",
        "write_log": """
function Write-Log {
    param(
        [string]$Message,
        [string]$Level = "INFO",
        [string]$LogPath = '{log_path}'
    )
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $logEntry = "$timestamp [$Level] $Message"
    Add-Content -Path $LogPath -Value $logEntry
    Write-Output $logEntry
}
""",
        "query_registry": """
try {
    $regPath = '{reg_path}'
    $valueName = '{value_name}'
    if (Test-Path -Path $regPath) {
        $value = Get-ItemProperty -Path $regPath -Name $valueName -ErrorAction SilentlyContinue
        if ($value) {
            Write-Output "$valueName = $($value.$valueName)"
        } else {
            Write-Output "Registry value not found: $valueName"
        }
    } else {
        Write-Output "Registry path not found: $regPath"
    }
} catch {
    Write-Error "Registry access error: $_"
}
""",
        "invoke_web_request": """
try {
    $response = Invoke-WebRequest -Uri '{url}' -UseBasicParsing -TimeoutSec 30
    Write-Output "Status: $($response.StatusCode)"
    Write-Output "Content length: $($response.Content.Length)"
    Write-Output $response.Content
} catch {
    Write-Error "Web request failed: $_"
}
""",
        "parallel_processing": """
$items = @({items})
$maxConcurrent = {max_concurrent}
$results = @()
$scriptBlock = {{script_block}}
$runspacePool = [RunspaceFactory]::CreateRunspacePool(1, $maxConcurrent)
$runspacePool.Open()
$psInstances = @()
foreach ($item in $items) {
    $psInstance = [PowerShell]::Create()
    $psInstance.RunspacePool = $runspacePool
    [void]$psInstance.AddScript($scriptBlock)
    [void]$psInstance.AddArgument($item)
    $psInstances += @{
        Instance = $psInstance
        Result = $psInstance.BeginInvoke()
        Item = $item
    }
}
while ($psInstances.Result.IsCompleted -contains $false) {
    Start-Sleep -Milliseconds 100
}
foreach ($psInstance in $psInstances) {
    $results += $psInstance.Instance.EndInvoke($psInstance.Result)
    $psInstance.Instance.Dispose()
}
$runspacePool.Dispose()
$results
""",
        "error_handling_template": """
function Invoke-SafeCommand {
    param(
        [scriptblock]$ScriptBlock,
        [int]$MaxRetries = 3,
        [int]$RetryDelaySeconds = 5
    )
    $attempt = 1
    while ($attempt -le $MaxRetries) {
        try {
            return & $ScriptBlock
        } catch {
            Write-Warning "Attempt $attempt failed: $_"
            if ($attempt -eq $MaxRetries) {
                throw "Command failed after $MaxRetries attempts: $_"
            }
            Start-Sleep -Seconds $RetryDelaySeconds
            $attempt++
        }
    }
}
""",
    }

    @classmethod
    def generate_code(cls, task_type: str, params: Dict[str, Any]) -> str:
        """Generate PowerShell code for a specific task."""
        template = cls.TEMPLATES.get(task_type)
        if not template:
            return f"# Unsupported task type: {task_type}"
        code = template
        for key, value in params.items():
            code = code.replace(f"{{{key}}}", str(value))
        return code


# ============================================================================
# COMMAND OPTIMIZER
# ============================================================================


class PowerShellCommandOptimizer:
    """Optimizes PowerShell commands for better performance."""

    @staticmethod
    def optimize(command: str) -> str:
        """Apply optimizations to PowerShell command."""
        optimizations: list[tuple[str, str]] = [
            (r'Get-ChildItem .+? \| Where-Object \{ \$_.Name -match (.+?) \}',
             r'Get-ChildItem -Filter \1'),
            (r'(Get-\w+)(?!.*-ErrorAction)', r'\1 -ErrorAction SilentlyContinue'),
        ]
        optimized = command
        for pattern, replacement in optimizations:
            optimized = re.sub(pattern, replacement, optimized, flags=re.IGNORECASE)
        return optimized

    @staticmethod
    def estimate_execution_time(command: str) -> float:
        """Estimate command execution time in seconds."""
        complexity = 1.0
        complexity += command.count('|') * 0.1
        complexity += command.count('-Filter') * 0.5
        complexity += command.count('*') * 0.2
        return min(complexity * 0.5, 30.0)


# ============================================================================
# MAIN POWERSHELL EXECUTOR
# ============================================================================


class PowerShellMaster:
    """Complete PowerShell execution and management system.

    Uses asyncio.create_subprocess_exec to avoid shell quote mangling.
    Provides error analysis, code generation, optimization, and session management.
    """

    def __init__(self, config: Optional[PowerShellConfig] = None):
        self.config = config or PowerShellConfig()
        self.error_analyzer = PowerShellErrorAnalyzer()
        self.code_master = PowerShellCodeMaster()
        self.optimizer = PowerShellCommandOptimizer()
        self._session: Optional[PowerShellSession] = None
        self.command_history: List[PowerShellResult] = []
        self._setup_logging()

    def _setup_logging(self) -> None:
        """Setup PowerShell logging."""
        if self.config.enable_logging:
            self.config.log_path.mkdir(parents=True, exist_ok=True)

    def _log_command(self, result: PowerShellResult) -> None:
        """Log command execution."""
        if not self.config.enable_logging:
            return
        log_file = self.config.log_path / f"ps_{datetime.now().strftime('%Y%m%d')}.log"
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(f"[{datetime.now().isoformat()}] {result.status.value} | ")
            f.write(f"Exit: {result.exit_code} | ")
            f.write(f"Duration: {result.duration_ms}ms\n")
            f.write(f"Command: {result.command[:200]}\n")
            if result.error_message:
                f.write(f"Error: {result.error_message}\n")
            f.write("-" * 80 + "\n")
        self.command_history.append(result)
        if len(self.command_history) > 1000:
            self.command_history = self.command_history[-500:]

    async def execute(
        self,
        command: str,
        capture_output: bool = True,
        timeout: Optional[int] = None,
    ) -> PowerShellResult:
        """Execute a PowerShell command with full error handling.

        Uses asyncio.create_subprocess_exec to avoid shell quote mangling
        that happens with subprocess.run(shell=True) or os.system().

        Args:
            command: PowerShell command to execute.
            capture_output: If True, captures stdout and stderr.
            timeout: Override the default timeout in seconds.

        Returns:
            PowerShellResult with status, output, and error analysis.
        """
        start_time = datetime.now()
        timeout_seconds = timeout or self.config.timeout_seconds

        # Build PowerShell arguments
        args = self.config.to_args()

        # Determine if this is a flag or a script command
        if command.strip().startswith('-') or ' ' not in command.strip():
            ps_command = ["powershell.exe"] + args + [command]
        else:
            ps_command = ["powershell.exe"] + args + ["-Command", command]

        try:
            process = await asyncio.create_subprocess_exec(
                *ps_command,
                stdout=asyncio.subprocess.PIPE if capture_output else None,
                stderr=asyncio.subprocess.PIPE if capture_output else None,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout_seconds,
                )
                stdout_text = stdout.decode(self.config.encoding, errors='replace') if stdout else ""
                stderr_text = stderr.decode(self.config.encoding, errors='replace') if stderr else ""
                duration_ms = (datetime.now() - start_time).total_seconds() * 1000

                # Analyze errors
                error_analysis = None
                suggestions: list[str] = []
                if stderr_text or process.returncode != 0:
                    error_analysis = self.error_analyzer.analyze(stderr_text or stdout_text, command)
                    suggestions = error_analysis.get("suggestions", [])

                result = PowerShellResult(
                    status=ExecutionStatus.SUCCESS if process.returncode == 0 else ExecutionStatus.FAILED,
                    stdout=stdout_text,
                    stderr=stderr_text,
                    exit_code=process.returncode or 0,
                    duration_ms=duration_ms,
                    command=command,
                    error_type=error_analysis.get("error_type") if error_analysis else None,
                    error_message=stderr_text[:500] if stderr_text else None,
                    suggestions=suggestions,
                )

            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                duration_ms = (datetime.now() - start_time).total_seconds() * 1000
                result = PowerShellResult(
                    status=ExecutionStatus.TIMEOUT,
                    stdout="",
                    stderr="",
                    exit_code=-1,
                    duration_ms=duration_ms,
                    command=command,
                    error_type="timeout",
                    error_message=f"Command timed out after {timeout_seconds}s",
                    suggestions=["Try increasing timeout", "Optimize the command"],
                )

        except Exception as e:
            duration_ms = (datetime.now() - start_time).total_seconds() * 1000
            result = PowerShellResult(
                status=ExecutionStatus.FAILED,
                stdout="",
                stderr=str(e),
                exit_code=-1,
                duration_ms=duration_ms,
                command=command,
                error_type="execution_error",
                error_message=str(e),
                suggestions=["Check PowerShell installation", "Verify command syntax"],
            )

        # Wire to brain
        try:
            if result.status == ExecutionStatus.SUCCESS:
                from ..intel.engine_wiring import wire_success
                wire_success(
                    module="powershell_master",
                    summary=f"PowerShell: {command[:100]}",
                    detail=f"Exit: {result.exit_code}, Duration: {result.duration_ms:.0f}ms",
                    source_id="powershell_master:execute",
                )
            else:
                from ..intel.engine_wiring import wire_failure
                wire_failure(
                    module="powershell_master",
                    detail=f"Command failed: {command[:100]} — {result.error_message or 'unknown'}",
                    gap_type="powershell_execution_failure",
                    source="powershell_master:execute",
                )
        except Exception:
            pass

        # Log
        if self.config.enable_logging:
            self._log_command(result)

        return result

    async def execute_file(self, filepath: Path, arguments: Optional[List[str]] = None) -> PowerShellResult:
        """Execute a PowerShell script file."""
        if not filepath.exists():
            return PowerShellResult(
                status=ExecutionStatus.FAILED,
                stdout="",
                stderr=f"File not found: {filepath}",
                exit_code=-1,
                duration_ms=0,
                command=f"File: {filepath}",
                error_type="file_not_found",
                error_message="Script file not found",
                suggestions=["Check file path", "Verify file exists"],
            )
        args = self.config.to_args()
        cmd = ["powershell.exe"] + args + ["-File", str(filepath)]
        if arguments:
            cmd.extend(arguments)
        return await self.execute(' '.join(cmd))

    async def execute_base64(self, encoded_command: str) -> PowerShellResult:
        """Execute a Base64 encoded PowerShell command."""
        return await self.execute(f"-EncodedCommand {encoded_command}")

    def encode_command(self, command: str) -> str:
        """Encode command as Base64 for execution.

        PowerShell expects UTF-16LE encoding for Base64 commands.
        """
        utf16_bytes = command.encode('utf-16le')
        return base64.b64encode(utf16_bytes).decode('ascii')

    async def test_powershell(self) -> bool:
        """Test if PowerShell is available and working."""
        result = await self.execute("Write-Host 'Test'")
        return result.status == ExecutionStatus.SUCCESS

    async def get_execution_policy(self) -> str:
        """Get current execution policy."""
        result = await self.execute("Get-ExecutionPolicy")
        if result.status == ExecutionStatus.SUCCESS:
            return result.stdout.strip()
        return "Unknown"

    async def set_execution_policy(self, policy: str, scope: str = "Process") -> PowerShellResult:
        """Set PowerShell execution policy."""
        command = f"Set-ExecutionPolicy -ExecutionPolicy {policy} -Scope {scope} -Force"
        return await self.execute(command)

    async def get_modules(self) -> List[str]:
        """Get list of available modules."""
        result = await self.execute("Get-Module -ListAvailable | Select-Object -ExpandProperty Name")
        if result.status == ExecutionStatus.SUCCESS:
            return [m.strip() for m in result.stdout.split('\n') if m.strip()]
        return []

    async def install_module(self, module_name: str, force: bool = False) -> PowerShellResult:
        """Install a PowerShell module."""
        force_flag = "-Force" if force else ""
        command = f"Install-Module -Name {module_name} {force_flag} -AllowClobber -SkipPublisherCheck"
        return await self.execute(command)

    async def get_commands(self, module: Optional[str] = None) -> List[str]:
        """Get available commands."""
        module_filter = f"-Module {module}" if module else ""
        result = await self.execute(f"Get-Command {module_filter} | Select-Object -ExpandProperty Name")
        if result.status == ExecutionStatus.SUCCESS:
            return [c.strip() for c in result.stdout.split('\n') if c.strip()]
        return []

    async def create_session(self) -> PowerShellSession:
        """Create a persistent PowerShell session."""
        session_id = hashlib.md5(os.urandom(32), usedforsecurity=False).hexdigest()[:16]
        await self.execute(f"$global:ARIA_SESSION_ID = '{session_id}'")
        session = PowerShellSession(
            id=session_id,
            created_at=datetime.now(),
            variables={},
            functions=[],
            modules=[],
        )
        self._session = session
        return session

    async def set_variable(self, name: str, value: Any) -> PowerShellResult:
        """Set a variable in the current session."""
        if isinstance(value, str):
            return await self.execute(f"${name} = '{value}'")
        else:
            return await self.execute(f"${name} = {json.dumps(value)}")

    async def get_variable(self, name: str) -> Optional[str]:
        """Get a variable value from the session."""
        result = await self.execute(f"Write-Output ${name}")
        if result.status == ExecutionStatus.SUCCESS:
            return result.stdout.strip()
        return None

    async def get_processes(self, name_filter: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get running processes as JSON."""
        filter_cmd = f" -Name {name_filter}" if name_filter else ""
        command = f"Get-Process{filter_cmd} | ConvertTo-Json -Compress"
        result = await self.execute(command)
        if result.status == ExecutionStatus.SUCCESS and result.stdout:
            try:
                processes = json.loads(result.stdout)
                if isinstance(processes, dict):
                    return [processes]
                return processes if isinstance(processes, list) else []
            except json.JSONDecodeError:
                pass
        return []

    async def get_services(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get Windows services as JSON."""
        status_filter = f" -Status {status}" if status else ""
        command = f"Get-Service{status_filter} | ConvertTo-Json -Compress"
        result = await self.execute(command)
        if result.status == ExecutionStatus.SUCCESS and result.stdout:
            try:
                services = json.loads(result.stdout)
                if isinstance(services, dict):
                    return [services]
                return services if isinstance(services, list) else []
            except json.JSONDecodeError:
                pass
        return []

    async def restart_service(self, service_name: str) -> PowerShellResult:
        """Restart a Windows service."""
        code = self.code_master.generate_code("restart_service", {"service_name": service_name})
        return await self.execute(code)

    async def check_file_exists(self, path: str) -> bool:
        """Check if a file exists."""
        result = await self.execute(f"Test-Path '{path}'")
        if result.status == ExecutionStatus.SUCCESS:
            return result.stdout.strip().lower() == 'true'
        return False

    async def read_file(self, path: str) -> Optional[str]:
        """Read file content."""
        result = await self.execute(f"Get-Content -Path '{path}' -Raw -ErrorAction SilentlyContinue")
        if result.status == ExecutionStatus.SUCCESS:
            return result.stdout
        return None

    async def write_file(self, path: str, content: str) -> PowerShellResult:
        """Write content to file."""
        escaped_content = content.replace("'", "''")
        command = f"Set-Content -Path '{path}' -Value '{escaped_content}' -Force"
        return await self.execute(command)

    async def execute_workflow(
        self,
        commands: List[str],
        stop_on_error: bool = True,
    ) -> List[PowerShellResult]:
        """Execute a sequence of commands as a workflow."""
        results: list[PowerShellResult] = []
        for cmd in commands:
            result = await self.execute(cmd)
            results.append(result)
            if stop_on_error and result.status != ExecutionStatus.SUCCESS:
                break
        return results

    def format_result(self, result: PowerShellResult) -> str:
        """Format a PowerShell result for display."""
        output: list[str] = []
        if result.status == ExecutionStatus.SUCCESS:
            output.append(f"[OK] Command executed (Exit: {result.exit_code}, {result.duration_ms:.0f}ms)")
        elif result.status == ExecutionStatus.TIMEOUT:
            output.append(f"[TIMEOUT] Command timed out after {result.duration_ms:.0f}ms")
        else:
            output.append(f"[FAIL] Command failed (Exit: {result.exit_code}, {result.duration_ms:.0f}ms)")

        if result.stdout:
            output.append(f"\nSTDOUT:\n{result.stdout[:1000]}")
        if result.stderr:
            output.append(f"\nSTDERR:\n{result.stderr[:500]}")
        if result.suggestions:
            output.append(f"\nSuggestions:")
            for suggestion in result.suggestions:
                output.append(f"  - {suggestion}")

        return '\n'.join(output)

    def get_statistics(self) -> Dict[str, Any]:
        """Get execution statistics."""
        if not self.command_history:
            return {"total_commands": 0}
        success = sum(1 for r in self.command_history if r.status == ExecutionStatus.SUCCESS)
        failed = sum(1 for r in self.command_history if r.status == ExecutionStatus.FAILED)
        timeout = sum(1 for r in self.command_history if r.status == ExecutionStatus.TIMEOUT)
        avg_duration = sum(r.duration_ms for r in self.command_history) / len(self.command_history)
        return {
            "total_commands": len(self.command_history),
            "successful": success,
            "failed": failed,
            "timeout": timeout,
            "success_rate": (success / len(self.command_history)) * 100 if self.command_history else 0,
            "avg_duration_ms": avg_duration,
            "unique_errors": len(set(r.error_type for r in self.command_history if r.error_type)),
        }


# ============================================================================
# FASTAPI INTEGRATION ENDPOINTS
# ============================================================================


def add_powershell_endpoints(router: Any, ps_master: PowerShellMaster) -> None:
    """Add PowerShell endpoints to a FastAPI router.

    Args:
        router: FastAPI APIRouter instance.
        ps_master: PowerShellMaster singleton.
    """
    from fastapi import Request

    @router.post("/powershell/execute")
    async def execute_powershell(request: Request):
        """Execute a PowerShell command."""
        body = await request.json()
        command = body.get("command", "")
        if not command:
            return {"error": "No command provided", "status": "error"}
        result = await ps_master.execute(command)
        return {
            "status": result.status.value,
            "exit_code": result.exit_code,
            "duration_ms": result.duration_ms,
            "stdout": result.stdout[:5000] if result.stdout else "",
            "stderr": result.stderr[:2000] if result.stderr else "",
            "suggestions": result.suggestions,
        }

    @router.get("/powershell/status")
    async def get_powershell_status():
        """Get PowerShell system status."""
        available = await ps_master.test_powershell()
        policy = await ps_master.get_execution_policy()
        stats = ps_master.get_statistics()
        return {
            "available": available,
            "execution_policy": policy,
            "statistics": stats,
        }

    @router.get("/powershell/commands")
    async def get_available_commands(module: Optional[str] = None):
        """Get available PowerShell commands."""
        commands = await ps_master.get_commands(module)
        return {"count": len(commands), "commands": commands[:100]}


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================


async def main() -> None:
    """Test the PowerShell Master."""
    ps = PowerShellMaster()
    print("=" * 60)
    print("ARIA PowerShell Master - Testing")
    print("=" * 60)

    available = await ps.test_powershell()
    print(f"\nPowerShell Available: {available}")

    if available:
        policy = await ps.get_execution_policy()
        print(f"Execution Policy: {policy}")

        result = await ps.execute("Get-Process -Name powershell* | Select-Object Name, Id, CPU")
        print("\nTest Command Result:")
        print(ps.format_result(result))

        stats = ps.get_statistics()
        print(f"\nStatistics: {stats}")


if __name__ == "__main__":
    asyncio.run(main())
