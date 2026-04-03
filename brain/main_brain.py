"""
CRUCIX Brain — Main Entrypoint
Starts the autonomous sweep loop + Flask API server in a single process.
Deploy this as a separate Render service alongside your main Crucix app.
"""
import logging
import os
import threading

from flask import Flask

from crucix_brain import CrucixAutonomousAgent
from crucix_brain.api_routes import register_brain_routes

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("crucix.brain.main")


def create_app() -> Flask:
    app = Flask(__name__)

    # Initialise the autonomous agent
    agent = CrucixAutonomousAgent(
        telegram_token   = os.getenv("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID"),
    )

    # Register API routes
    register_brain_routes(app, agent)

    # Start autonomous sweep loop in background thread
    sweep_thread = threading.Thread(
        target=agent.start_scheduler,
        daemon=True,
        name="crucix-brain-sweep",
    )
    sweep_thread.start()
    logger.info("Autonomous sweep loop started")

    @app.route("/health")
    def health():
        return {"status": "ok", "service": "crucix-brain"}

    return app


if __name__ == "__main__":
    app = create_app()
    port = int(os.getenv("BRAIN_PORT", 5001))
    logger.info(f"Crucix Brain API listening on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
