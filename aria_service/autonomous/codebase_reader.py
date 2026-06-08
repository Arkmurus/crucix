"""R-F802 — Codebase reader for ARIACoder.

Reads source files into LLM-friendly context. Uses ARIA's existing RAG
store for semantic neighbour retrieval when context exceeds the primary
module size.

The reader is read-only on the live codebase; writes go to a per-fix
workspace directory under `/data/coder_workspace/{fix_id}/`.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger("aria.autonomous.codebase_reader")

# R-F1320: wire module health to the brain
try:
    from aria_service.intel.engine_wiring import wire_success as _ws1320
    _ws1320(
        module="autonomous.codebase_reader",
        summary="Codebase Reader active",
        source_id="autonomous:codebase_reader:R-F1320",
    )
except Exception:
    pass

DEFAULT_REPO_PATH = Path(os.environ.get("ARIA_REPO_PATH", "/app"))
PRIMARY_MODULE_MAX_CHARS = 5000
RELATED_FILE_MAX_CHARS = 2000
RAG_CHUNK_MAX_CHARS = 1000
RAG_TOP_K = 3


class CodebaseReader:
    """Provides ARIA-Coder with relevant codebase context for any gap."""

    def __init__(
        self,
        aria_service_url: str,
        repo_path: Optional[Path] = None,
    ) -> None:
        self.aria_url = aria_service_url
        self.repo_path = repo_path or DEFAULT_REPO_PATH

    async def get_context(
        self,
        module: str,
        related_files: list[str],
        rag_client: Optional[object] = None,
    ) -> str:
        """Build a context string from primary module + related + RAG.

        `rag_client` is optional — if provided, it must expose
        `async query(query: str, top_k: int) -> list[dict]`. We avoid
        hard-coupling to httpx here so the reader is testable.
        """
        parts: list[str] = []

        primary = self.read(module)
        if primary:
            parts.append(
                f"# PRIMARY MODULE: {module}\n{primary[:PRIMARY_MODULE_MAX_CHARS]}"
            )

        for filepath in related_files[:3]:
            related = self.read(filepath)
            if related:
                parts.append(
                    f"# RELATED: {filepath}\n{related[:RELATED_FILE_MAX_CHARS]}"
                )

        if rag_client is not None:
            try:
                results = await rag_client.query(  # type: ignore[attr-defined]
                    query=f"code pattern {module}",
                    top_k=RAG_TOP_K,
                )
                for r in results or []:
                    content = (r.get("content") or "")[:RAG_CHUNK_MAX_CHARS]
                    if content:
                        parts.append(f"# RAG CONTEXT:\n{content}")
            except Exception as e:
                logger.debug("[codebase_reader] RAG query failed: %s", e)

        return "\n\n".join(parts)

    def read(self, filepath: str) -> str:
        """Read a file relative to the repo root. Returns '' on missing.

        R-F1443: handle absolute paths that resolve within the repo root
        (the LLM sometimes returns /home/user/aria_service/routes/aria.py).
        """
        # R-F1443: if absolute, try to resolve within repo root
        if os.path.isabs(filepath) or filepath.startswith("/") or filepath.startswith("\\"):
            candidate = Path(filepath).resolve()
            try:
                candidate.relative_to(self.repo_path.resolve())
                path = candidate
            except ValueError:
                path = self.repo_path / filepath
        else:
            path = self.repo_path / filepath
        try:
            if path.exists() and path.is_file():
                return path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            logger.warning("[codebase_reader] read %s failed: %s", filepath, e)
        return ""

    def write_to_workspace(
        self, workspace: Path, filepath: str, content: str,
    ) -> Path:
        """Write content to a sandboxed workspace path.

        Refuses absolute paths and `..` escapes. Returns the target path
        on success; raises ValueError on path-escape attempt.
        """
        # Reject absolute paths cross-platform: os.path.isabs catches the
        # platform-native form (C:\... on Windows, /... on Unix); the
        # explicit leading-slash check catches Unix-style absolute paths
        # when the test or LLM-generated input runs on Windows.
        # R-F1443: the LLM sometimes "helpfully" returns absolute paths like
        # /home/user/aria_service/routes/aria.py. Instead of rejecting these,
        # try to resolve them relative to the workspace. If the resolved path
        # is within the workspace, accept it. This is safer than fixing the
        # LLM prompt because the LLM will keep doing this.
        if os.path.isabs(filepath) or filepath.startswith("/") or filepath.startswith("\\"):
            # Try to resolve as an absolute path within the workspace
            candidate = Path(filepath).resolve()
            try:
                candidate.relative_to(workspace.resolve())
                target = candidate
            except ValueError:
                raise ValueError(
                    f"refusing absolute path outside workspace: {filepath} → {candidate}"
                )
        else:
            target = (workspace / filepath).resolve()
        # Guard against ../.. escapes from the workspace dir
        try:
            target.relative_to(workspace.resolve())
        except ValueError:
            raise ValueError(
                f"refusing to write outside workspace: {filepath} → {target}"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target
