"""R-F1225 — Capability tests for PowerShell Master module.

Tests:
1. Module imports correctly
2. PowerShellConfig dataclass
3. PowerShellResult dataclass
4. PowerShellErrorAnalyzer error patterns
5. PowerShellCodeMaster template generation
6. PowerShellCommandOptimizer
7. PowerShellMaster basic structure
8. FastAPI endpoint registration
9. Brain wiring
10. Module wired in main.py
"""
from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _read(path: str) -> str:
    """Read a file relative to repo root."""
    return (_REPO_ROOT / path).read_text(encoding="utf-8")


# ── Tests: Module imports ──────────────────────────────────────────────────


class TestModuleImports:
    """PowerShell Master module must import correctly."""

    def test_module_importable(self):
        """The module can be imported."""
        from aria_service.utils.powershell_master import (
            PowerShellMaster,
            PowerShellConfig,
            PowerShellResult,
            PowerShellSession,
            PowerShellErrorAnalyzer,
            PowerShellCodeMaster,
            PowerShellCommandOptimizer,
            ExecutionStatus,
            add_powershell_endpoints,
        )
        assert PowerShellMaster is not None
        assert PowerShellConfig is not None
        assert PowerShellResult is not None
        assert PowerShellErrorAnalyzer is not None
        assert PowerShellCodeMaster is not None
        assert ExecutionStatus is not None


# ── Tests: Data classes ────────────────────────────────────────────────────


class TestDataClasses:
    """Data classes must work correctly."""

    def test_powershell_config_defaults(self):
        """PowerShellConfig has sensible defaults."""
        from aria_service.utils.powershell_master import PowerShellConfig
        config = PowerShellConfig()
        assert config.execution_policy == "Bypass"
        assert config.no_profile is True
        assert config.non_interactive is True
        assert config.timeout_seconds == 300

    def test_powershell_config_to_args(self):
        """to_args generates correct command line arguments."""
        from aria_service.utils.powershell_master import PowerShellConfig
        config = PowerShellConfig()
        args = config.to_args()
        assert "-NoProfile" in args
        assert "-NonInteractive" in args
        assert "-ExecutionPolicy" in args
        assert "Bypass" in args

    def test_powershell_result_defaults(self):
        """PowerShellResult has sensible defaults."""
        from aria_service.utils.powershell_master import PowerShellResult, ExecutionStatus
        result = PowerShellResult(
            status=ExecutionStatus.SUCCESS,
            stdout="test",
            stderr="",
            exit_code=0,
            duration_ms=100.0,
            command="Get-Process",
        )
        assert result.status == ExecutionStatus.SUCCESS
        assert result.stdout == "test"
        assert result.exit_code == 0
        assert result.suggestions == []


# ── Tests: Error Analyzer ──────────────────────────────────────────────────


class TestErrorAnalyzer:
    """Error analyzer must detect known error patterns."""

    def test_detects_execution_policy(self):
        """Detects execution policy errors."""
        from aria_service.utils.powershell_master import PowerShellErrorAnalyzer
        result = PowerShellErrorAnalyzer.analyze(
            "execution of scripts is disabled on this system"
        )
        assert result["error_type"] == "execution_policy"
        assert result["category"] == "security"
        assert len(result["suggestions"]) > 0

    def test_detects_command_not_found(self):
        """Detects command not found errors."""
        from aria_service.utils.powershell_master import PowerShellErrorAnalyzer
        result = PowerShellErrorAnalyzer.analyze(
            "The term 'Get-Foo' is not recognized as a name of a cmdlet"
        )
        assert result["error_type"] == "command_not_found"

    def test_detects_access_denied(self):
        """Detects access denied errors."""
        from aria_service.utils.powershell_master import PowerShellErrorAnalyzer
        result = PowerShellErrorAnalyzer.analyze(
            "Access denied to the path C:\\Windows\\System32"
        )
        assert result["error_type"] == "access_denied"

    def test_detects_syntax_error(self):
        """Detects syntax errors."""
        from aria_service.utils.powershell_master import PowerShellErrorAnalyzer
        result = PowerShellErrorAnalyzer.analyze(
            "Unexpected token ')' in expression or statement"
        )
        assert result["error_type"] == "syntax_error"

    def test_detects_null_value(self):
        """Detects null value errors."""
        from aria_service.utils.powershell_master import PowerShellErrorAnalyzer
        result = PowerShellErrorAnalyzer.analyze(
            "Cannot call a method on a null-valued expression"
        )
        assert result["error_type"] == "null_value"

    def test_unknown_error_returns_general(self):
        """Unknown errors return general suggestions."""
        from aria_service.utils.powershell_master import PowerShellErrorAnalyzer
        result = PowerShellErrorAnalyzer.analyze(
            "Something completely unexpected happened"
        )
        assert result["error_type"] == "unknown"
        assert result["confidence"] == 0.5


# ── Tests: Code Master ─────────────────────────────────────────────────────


class TestCodeMaster:
    """Code master must generate correct PowerShell code."""

    def test_generates_get_service(self):
        """Generates get_service template."""
        from aria_service.utils.powershell_master import PowerShellCodeMaster
        code = PowerShellCodeMaster.generate_code("get_service", {"service_name": "Spooler"})
        assert "Spooler" in code
        assert "Get-Service" in code

    def test_generates_restart_service(self):
        """Generates restart_service template."""
        from aria_service.utils.powershell_master import PowerShellCodeMaster
        code = PowerShellCodeMaster.generate_code("restart_service", {"service_name": "Spooler"})
        assert "Restart-Service" in code

    def test_unsupported_task_returns_comment(self):
        """Unsupported task types return a comment."""
        from aria_service.utils.powershell_master import PowerShellCodeMaster
        code = PowerShellCodeMaster.generate_code("nonexistent", {})
        assert code.startswith("# Unsupported")


# ── Tests: Command Optimizer ───────────────────────────────────────────────


class TestCommandOptimizer:
    """Command optimizer must apply optimizations."""

    def test_optimize_adds_error_action(self):
        """Optimizer adds -ErrorAction SilentlyContinue."""
        from aria_service.utils.powershell_master import PowerShellCommandOptimizer
        optimized = PowerShellCommandOptimizer.optimize("Get-Process -Name python")
        assert "SilentlyContinue" in optimized

    def test_estimate_execution_time(self):
        """Execution time estimation returns a reasonable value."""
        from aria_service.utils.powershell_master import PowerShellCommandOptimizer
        time = PowerShellCommandOptimizer.estimate_execution_time("Get-Process")
        assert 0 < time <= 30.0


# ── Tests: PowerShellMaster structure ──────────────────────────────────────


class TestPowerShellMaster:
    """PowerShellMaster must have all required methods."""

    def test_has_required_methods(self):
        """PowerShellMaster has all required methods."""
        from aria_service.utils.powershell_master import PowerShellMaster
        methods = [
            "execute",
            "execute_file",
            "execute_base64",
            "encode_command",
            "test_powershell",
            "get_execution_policy",
            "set_execution_policy",
            "get_modules",
            "install_module",
            "get_commands",
            "create_session",
            "set_variable",
            "get_variable",
            "get_processes",
            "get_services",
            "restart_service",
            "check_file_exists",
            "read_file",
            "write_file",
            "execute_workflow",
            "format_result",
            "get_statistics",
        ]
        for method in methods:
            assert hasattr(PowerShellMaster, method), f"Missing method: {method}"

    def test_has_required_components(self):
        """PowerShellMaster has all required sub-components."""
        from aria_service.utils.powershell_master import PowerShellMaster
        ps = PowerShellMaster()
        assert ps.error_analyzer is not None
        assert ps.code_master is not None
        assert ps.optimizer is not None
        assert ps.command_history == []


# ── Tests: FastAPI endpoints ───────────────────────────────────────────────


class TestEndpoints:
    """FastAPI endpoints must be properly structured."""

    def test_add_powershell_endpoints_exists(self):
        """add_powershell_endpoints function exists."""
        from aria_service.utils.powershell_master import add_powershell_endpoints
        assert callable(add_powershell_endpoints)

    def test_endpoints_in_source(self):
        """Endpoint definitions exist in the module source."""
        source = _read("aria_service/utils/powershell_master.py")
        assert "/powershell/execute" in source
        assert "/powershell/status" in source
        assert "/powershell/commands" in source


# ── Tests: Brain wiring ────────────────────────────────────────────────────


class TestBrainWiring:
    """PowerShell Master must wire to the brain."""

    def test_wires_success_to_brain(self):
        """Execute method wires success to brain."""
        source = _read("aria_service/utils/powershell_master.py")
        assert "wire_success" in source
        assert "powershell_master" in source

    def test_wires_failure_to_brain(self):
        """Execute method wires failure to brain."""
        source = _read("aria_service/utils/powershell_master.py")
        assert "wire_failure" in source
        assert "powershell_execution_failure" in source


# ── Tests: main.py wiring ──────────────────────────────────────────────────


class TestMainPyWiring:
    """PowerShell Master must be wired in main.py."""

    def test_powershell_master_in_main(self):
        """main.py references PowerShellMaster."""
        source = _read("aria_service/main.py")
        assert "PowerShellMaster" in source
        assert "add_powershell_endpoints" in source
        assert "R-F1225" in source
