"""R-F1218: Capability test — bare except: pass blocks are fixed.

Verifies:
1. performance_optimizer.py logs AST parse failures instead of swallowing them
2. terminal_ui.py uses named except instead of bare except
3. seed_compliance.py prints warning instead of silent pass
"""
import pytest
from unittest.mock import patch, MagicMock


def test_performance_optimizer_logs_parse_errors():
    """performance_optimizer logs a warning on AST parse failure."""
    with patch("aria_service.intel.performance_optimizer.logger") as mock_logger:
        from aria_service.intel.performance_optimizer import PerformanceOptimizer
        
        optimizer = PerformanceOptimizer()
        # Replace self.root with a mock that returns a bad file
        mock_root = MagicMock()
        bad_file = MagicMock()
        bad_file.name = "bad_file.py"
        bad_file.read_text.side_effect = UnicodeDecodeError("utf-8", b"", 0, 1, "bad encoding")
        mock_root.glob.return_value = [bad_file]
        optimizer.root = mock_root
        
        import asyncio
        result = asyncio.run(optimizer.optimize_imports())
        
        # Should have logged a warning about the parse failure
        warning_calls = [c for c in mock_logger.warning.call_args_list 
                        if "AST parse failed" in str(c)]
        assert warning_calls, (
            "AST parse failure was not logged as warning"
        )
        # Should still return results (graceful degradation)
        assert "findings" in result
        assert "total" in result


def test_performance_optimizer_async_logs_parse_errors():
    """performance_optimizer.optimize_async logs a warning on AST parse failure."""
    with patch("aria_service.intel.performance_optimizer.logger") as mock_logger:
        from aria_service.intel.performance_optimizer import PerformanceOptimizer
        
        optimizer = PerformanceOptimizer()
        mock_root = MagicMock()
        bad_file = MagicMock()
        bad_file.name = "bad_file.py"
        bad_file.read_text.side_effect = UnicodeDecodeError("utf-8", b"", 0, 1, "bad encoding")
        mock_root.glob.return_value = [bad_file]
        optimizer.root = mock_root
        
        import asyncio
        result = asyncio.run(optimizer.optimize_async())
        
        warning_calls = [c for c in mock_logger.warning.call_args_list 
                        if "AST parse failed" in str(c)]
        assert warning_calls, (
            "AST parse failure in optimize_async was not logged as warning"
        )
        assert "findings" in result


def test_terminal_ui_uses_named_except():
    """terminal_ui.py uses named except instead of bare except."""
    with open("aria_service/terminal_ui.py", encoding="utf-8") as f:
        content = f.read()
    
    # Check there are no bare except: blocks (except: without Exception type)
    import re
    bare_excepts = re.findall(r"^\s*except\s*:", content, re.MULTILINE)
    assert len(bare_excepts) == 0, (
        f"Found {len(bare_excepts)} bare except: blocks in terminal_ui.py"
    )


def test_seed_compliance_prints_warning():
    """seed_compliance.py prints warning instead of silent pass on fact store failure."""
    with open("scripts/seed_compliance.py", encoding="utf-8") as f:
        content = f.read()
    
    # Check there are no bare except: pass blocks
    assert "except:" not in content, (
        "seed_compliance.py still has bare except:"
    )
    assert "R-F1218" in content, (
        "seed_compliance.py missing R-F1218 warning message"
    )
