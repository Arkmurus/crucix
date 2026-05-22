"""R-F802 — Autonomous coder entrypoint.

Called from `aria_service/main.py` after FastAPI startup completes.

The coder is **dormant by default** — it does not start unless ALL of:
  - `ARIA_AUTONOMOUS_ENABLED=1` (master kill switch, per existing engine)
  - `ARIA_CODER_ENABLED=1`     (this engine specifically)
  - `ARIA_INTERNAL_TOKEN` is set on the host
  - Redis client is constructible

The wiring is split out so R-F802 can land the modules without the
engine starting in production. R-F803 will wire `main.py` to call
`start_aria_coder()`.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Optional

logger = logging.getLogger("aria.autonomous.coder_entrypoint")

ENABLE_VAR_MASTER = "ARIA_AUTONOMOUS_ENABLED"
ENABLE_VAR_CODER = "ARIA_CODER_ENABLED"


async def start_aria_coder(
    app_state: Any,
    aria_service_url: Optional[str] = None,
) -> Optional[list[asyncio.Task]]:
    """Start the ARIACoder + GapDetector as background tasks.

    Returns the list of started tasks (so the caller can hold references
    and cancel cleanly on shutdown), or None if startup was refused.
    """
    if os.environ.get(ENABLE_VAR_MASTER, "0") != "1":
        logger.info(
            "[coder_entrypoint] master switch %s != 1 — coder dormant",
            ENABLE_VAR_MASTER,
        )
        return None

    if os.environ.get(ENABLE_VAR_CODER, "0") != "1":
        logger.info(
            "[coder_entrypoint] %s != 1 — coder dormant (master switch is on)",
            ENABLE_VAR_CODER,
        )
        return None

    if not os.environ.get("ARIA_INTERNAL_TOKEN"):
        logger.warning(
            "[coder_entrypoint] ARIA_INTERNAL_TOKEN unset — refusing to start",
        )
        return None

    redis_client = getattr(app_state, "redis", None)
    if redis_client is None:
        logger.warning(
            "[coder_entrypoint] app_state has no .redis — refusing to start",
        )
        return None

    # Lazy imports — keep `import aria_service.autonomous` cheap
    from .gap_detector import GapDetector
    from .self_coder import ARIACoder

    url = aria_service_url or os.environ.get(
        "ARIA_SELF_URL", "http://localhost:8000",
    )

    brain_hook = None
    try:
        from ..intel.brain_hook import brain_hook as _brain_hook  # noqa: F401
        brain_hook = _brain_hook
    except ImportError as e:
        logger.info(
            "[coder_entrypoint] brain_hook not available: %s — continuing", e,
        )

    output_harvester = None
    try:
        from ..learning.output_harvester import harvest as _harvest_fn  # noqa: F401
        # The existing harvester exposes module-level functions, not a class.
        # ARIACoder.capture(...) will be called via a thin shim on
        # output_harvester.capture, so we wrap here.

        class _HarvestShim:
            async def capture(self, pair: dict) -> None:
                # Synchronous module-level harvest call; we delegate.
                try:
                    _harvest_fn(
                        user_msg=pair.get("instruction", ""),
                        response=pair.get("response", ""),
                        meta={
                            "persona": pair.get("persona", ""),
                            "source": "autonomous_coder",
                            "r_number": pair.get("r_number"),
                        },
                    )
                except Exception as e:
                    logger.warning(
                        "[coder_entrypoint] harvest shim failed: %s", e,
                    )

        output_harvester = _HarvestShim()
    except ImportError as e:
        logger.info(
            "[coder_entrypoint] output_harvester not available: %s", e,
        )

    coder = ARIACoder(
        redis_client=redis_client,
        aria_service_url=url,
        whatsapp_notifier=None,  # TODO R-F803: wire WA notifier
        brain_hook=brain_hook,
        output_harvester=output_harvester,
    )

    tasks = [
        asyncio.create_task(
            coder.gap_detector.run_forever(),
            name="aria_coder.gap_detector",
        ),
        asyncio.create_task(
            coder.run_forever(),
            name="aria_coder.self_coder",
        ),
    ]
    logger.info(
        "[coder_entrypoint] ✅ ARIA-Coder started (%d background tasks)",
        len(tasks),
    )
    return tasks
