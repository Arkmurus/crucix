#!/usr/bin/env python3
"""R-F1183 — ARIA's autonomous self-deploy script.

Uses the Fly Machines API directly (no flyctl dependency) so ARIA can
deploy herself fully autonomously.

Usage:
    python scripts/self_deploy.py --app aria-intel --r-number 1183 --commit-sha <sha>
    python scripts/self_deploy.py --app aria-web --r-number 1183 --commit-sha <sha>
    python scripts/self_deploy.py --app aria-wa --r-number 1183 --commit-sha <sha>
    python scripts/self_deploy.py --all --r-number 1183 --commit-sha <sha>

Requires:
    - FLY_API_TOKEN environment variable set
    - Git repo with .git/ directory (for push guard)
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

# Add the repo root to the path so we can import aria_service modules
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("self_deploy")

# The three Fly apps
APPS = {
    "aria-intel": {"health_path": "/health/live"},
    "aria-web": {"health_path": "/healthz"},
    "aria-wa": {"health_path": "/health"},
}


async def deploy_app(
    app: str,
    r_number: int,
    commit_sha: str,
    *,
    image: str | None = None,
    skip_push_guard: bool = False,
) -> bool:
    """Deploy a single app via the Machines API."""
    from aria_service.autonomous.machines_deployer import MachinesDeployer

    deployer = MachinesDeployer(
        aria_service_url=f"https://{app}.fly.dev",
        repo_path=REPO_ROOT,
    )

    try:
        result = await deployer.deploy(
            app=app,
            r_number=r_number,
            commit_sha=commit_sha,
            image=image,
            skip_push_guard=skip_push_guard,
        )

        if result.success:
            logger.info(
                "✅ R-F%d deployed to %s in %.1fs (image=%s)",
                r_number, app, result.duration_s, result.image,
            )
        else:
            logger.error(
                "❌ R-F%d deploy to %s FAILED: %s",
                r_number, app, result.error,
            )

        return result.success

    finally:
        await deployer.aclose()


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="ARIA autonomous self-deploy via Fly Machines API",
    )
    parser.add_argument(
        "--app", choices=list(APPS.keys()) + ["all"],
        default="all",
        help="Fly app to deploy (default: all)",
    )
    parser.add_argument(
        "--r-number", type=int, required=True,
        help="R-number for this deploy",
    )
    parser.add_argument(
        "--commit-sha", required=True,
        help="Full git commit SHA to deploy",
    )
    parser.add_argument(
        "--image",
        help="Pre-built image ref (optional; triggers remote build if omitted)",
    )
    parser.add_argument(
        "--skip-push-guard", action="store_true",
        help="Skip the push guard check (for testing)",
    )

    args = parser.parse_args()

    # Validate FLY_API_TOKEN
    if not os.environ.get("FLY_API_TOKEN"):
        logger.error(
            "FLY_API_TOKEN environment variable is not set. "
            "Set it before running this script."
        )
        return 1

    # Determine which apps to deploy
    apps_to_deploy = list(APPS.keys()) if args.app == "all" else [args.app]

    logger.info(
        "=== ARIA self-deploy: R-F%d (commit=%s) ===",
        args.r_number, args.commit_sha[:8],
    )
    logger.info("  apps: %s", ", ".join(apps_to_deploy))

    failures = 0
    for app in apps_to_deploy:
        ok = await deploy_app(
            app=app,
            r_number=args.r_number,
            commit_sha=args.commit_sha,
            image=args.image,
            skip_push_guard=args.skip_push_guard,
        )
        if not ok:
            failures += 1

    if failures == 0:
        logger.info(
            "=== ✅ ALL APPS DEPLOYED (R-F%d, commit=%s) ===",
            args.r_number, args.commit_sha[:8],
        )
        return 0
    else:
        logger.error(
            "=== ❌ %d/%d app(s) FAILED to deploy ===",
            failures, len(apps_to_deploy),
        )
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
