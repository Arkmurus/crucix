"""Capability checks for the shareable ARIA software architecture reference."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ARCHITECTURE = ROOT / "docs" / "ARIA_SOFTWARE_ARCHITECTURE.md"


def test_architecture_reference_covers_developer_system_boundaries() -> None:
    """The published reference names every deployable and core engineering concern."""
    text = ARCHITECTURE.read_text(encoding="utf-8")

    for component in ("aria-app", "aria-web", "aria-intel", "aria-wa", "aria-searxng", "aria_cli"):
        assert component in text

    for section in (
        "## 3. Repository map",
        "## 4. Runtime architecture",
        "## 5. Core request flows",
        "## 6. Data architecture",
        "## 8. Security architecture",
        "## 9. Reliability and observability",
        "## 10. Deployment architecture",
        "## 11. Development and test architecture",
        "## 12. Extension guide",
    ):
        assert section in text


def test_architecture_source_index_points_to_real_files() -> None:
    """Every primary implementation file named for developers exists in the tree."""
    source_paths = (
        "server.mjs",
        "aria_service/main.py",
        "aria_service/routes/aria.py",
        "aria_service/aria_engine.py",
        "aria_service/intel/brain_hook.py",
        "aria_service/intel/state_store.py",
        "aria_service/intel/rag_store.py",
        "aria_service/llm/factory.py",
        "services/wa-listener/aria_wa_listener.mjs",
        "aria-app/next.config.mjs",
        "aria-app/lib/api.ts",
        "fly.toml",
        "fly.web.toml",
        "services/wa-listener/fly.toml",
        "aria-app/fly.app.toml",
        "searxng/fly.toml",
        "Dockerfile.web",
        "aria_service/Dockerfile",
    )

    missing = [path for path in source_paths if not (ROOT / path).is_file()]
    assert missing == []
