"""
CRUCIX Brain + ARIA — Main Entrypoint v2
"""
import logging
import os
import threading

from flask import Flask, jsonify, request

from crucix_brain import (
    CrucixAutonomousAgent,
    CrucixMemory,
    CrucixMLEngine,
    DeepSeekClient,
    FeedbackLoop,
    ARIACognition,
    ARIAChat,
)
from crucix_brain.api_routes import register_brain_routes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("crucix.brain.main")


def create_app() -> Flask:
    app = Flask(__name__)

    memory    = CrucixMemory()
    ml_engine = CrucixMLEngine()
    deepseek  = DeepSeekClient()
    feedback  = FeedbackLoop(memory, ml_engine)

    aria_cognition = ARIACognition(deepseek, memory)
    aria_chat      = ARIAChat(aria_cognition)
    logger.info(f"ARIA online — age {aria_cognition.get_identity().get('age_days',0)} days")

    agent = CrucixAutonomousAgent(
        telegram_token   = os.getenv("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID"),
    )

    # Wire ARIA into sweep completion
    _orig_sweep = agent.run_sweep
    def aria_sweep():
        report = _orig_sweep()
        aria_cognition.update_identity(report)
        if report.get("signals_processed", 0) > 0:
            try:
                qs = aria_cognition.generate_curiosity_questions(report.get("brain_conclusions", []))
                report["aria_curiosity_questions"] = qs
            except Exception as e:
                logger.warning(f"Curiosity gen failed: {e}")
        return report
    agent.run_sweep = aria_sweep

    register_brain_routes(app, agent)

    @app.route("/api/aria/chat", methods=["POST"])
    def aria_chat_ep():
        data = request.get_json()
        msg  = data.get("message", "")
        sid  = data.get("session_id", "default")
        if not msg:
            return jsonify({"error": "message required"}), 400
        return jsonify(aria_chat.chat(msg, session_id=sid))

    @app.route("/api/aria/think", methods=["POST"])
    def aria_think():
        data = request.get_json()
        q    = data.get("question", "")
        if not q:
            return jsonify({"error": "question required"}), 400
        thought = aria_cognition.think(q, context=data.get("context", {}), fast=data.get("fast", False))
        return jsonify(thought.to_dict())

    @app.route("/api/aria/identity",  methods=["GET"])
    def aria_identity():
        return jsonify(aria_cognition.get_identity())

    @app.route("/api/aria/thoughts",  methods=["GET"])
    def aria_thoughts():
        return jsonify(aria_cognition.get_recent_thoughts(int(request.args.get("n", 10))))

    @app.route("/api/aria/curiosity", methods=["GET"])
    def aria_curiosity():
        return jsonify({"open_threads": aria_cognition.get_open_curiosity_threads()})

    @app.route("/api/aria/curiosity/resolve", methods=["POST"])
    def aria_resolve():
        data = request.get_json()
        aria_cognition.resolve_curiosity(data.get("question",""), data.get("answer",""))
        return jsonify({"status": "resolved"})

    @app.route("/api/aria/reflect",   methods=["POST"])
    def aria_reflect():
        return jsonify(aria_cognition.weekly_self_reflection())

    @app.route("/api/aria/telegram",  methods=["POST"])
    def aria_telegram():
        data = request.get_json()
        resp = aria_chat.handle_telegram_command(data.get("command","/aria"), data.get("args",""), data.get("chat_id","default"))
        return jsonify({"response": resp})

    @app.route("/health")
    def health():
        idn = aria_cognition.get_identity()
        return jsonify({"status":"operational","aria":idn.get("name"),"age_days":idn.get("age_days",0)})

    threading.Thread(target=agent.start_scheduler, daemon=True, name="sweep").start()
    logger.info("ARIA + sweep loop running")
    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=int(os.getenv("BRAIN_PORT", 5001)), debug=False)
