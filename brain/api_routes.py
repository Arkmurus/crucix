"""
CRUCIX Brain API Routes
Mount these on your existing Flask app to expose brain capabilities.

Usage in your app.py:
    from crucix_brain.api_routes import register_brain_routes
    register_brain_routes(app, brain_agent)
"""
import logging
import threading
from functools import wraps
from typing import Optional

from flask import Blueprint, jsonify, request

logger = logging.getLogger("crucix.brain.api")

brain_bp = Blueprint("brain", __name__, url_prefix="/api/brain")


def register_brain_routes(app, agent):
    """
    Register all brain API routes on the Flask app.
    
    Args:
        app:   Your Flask application instance
        agent: CrucixAutonomousAgent instance
    """
    def _agent():
        return agent

    # ── Status & Monitoring ──────────────────────────────────────────────────

    @brain_bp.route("/status", methods=["GET"])
    def brain_status():
        """Admin: brain health, ML model status, memory stats."""
        return jsonify({
            "status":         "operational",
            "memory":         agent.memory.memory_stats(),
            "ml_models":      agent.ml_engine.model_status(),
            "feedback":       agent.feedback.get_feedback_metrics(),
            "source_health":  agent.feedback.get_source_reliability_report(),
            "last_run":       agent.redis.get(agent.KEY_LAST_RUN),
        })

    @brain_bp.route("/run-history", methods=["GET"])
    def run_history():
        """Last N sweep run reports."""
        n = int(request.args.get("n", 10))
        return jsonify(agent.get_run_history(n))

    # ── Leads ────────────────────────────────────────────────────────────────

    @brain_bp.route("/leads", methods=["GET"])
    def get_leads():
        """Latest generated BD leads."""
        n = int(request.args.get("n", 20))
        return jsonify(agent.get_latest_leads(n))

    @brain_bp.route("/leads/<lead_id>/outcome", methods=["POST"])
    def record_outcome(lead_id):
        """Record deal outcome for ML feedback loop."""
        data    = request.get_json()
        outcome = data.get("outcome")       # WON | LOST | NO_BID
        market  = data.get("market", "")
        notes   = data.get("notes", "")

        if outcome not in ("WON", "LOST", "NO_BID"):
            return jsonify({"error": "outcome must be WON, LOST, or NO_BID"}), 400

        result = agent.feedback.record_outcome(lead_id, outcome, market, notes)
        return jsonify(result)

    @brain_bp.route("/leads/<lead_id>/rate", methods=["POST"])
    def rate_lead(lead_id):
        """Rate brain lead quality 1-5. Feeds into recommendation quality tracking."""
        data            = request.get_json()
        rating          = int(data.get("rating", 0))
        is_false_alarm  = bool(data.get("is_false_alarm", False))
        is_real_tender  = data.get("is_real_tender")   # True/False/None

        if not 1 <= rating <= 5:
            return jsonify({"error": "rating must be 1-5"}), 400

        result = agent.feedback.record_lead_rating(lead_id, rating, is_real_tender, is_false_alarm)
        return jsonify(result)

    # ── Intelligence ─────────────────────────────────────────────────────────

    @brain_bp.route("/brief", methods=["GET"])
    def get_bd_brief():
        """Latest defence BD intelligence brief."""
        brief = agent.get_latest_bd_brief()
        if not brief:
            return jsonify({"error": "No brief generated yet. Run a sweep first."}), 404
        return jsonify(brief)

    @brain_bp.route("/counterparty-risk", methods=["POST"])
    def counterparty_risk():
        """
        Full counterparty risk assessment.
        Body: { "entity_name": str, "document_text": str, "entity_data": {} }
        """
        data        = request.get_json()
        entity_name = data.get("entity_name", "Unknown")
        doc_text    = data.get("document_text", "")
        entity_data = data.get("entity_data", {})

        # NLP keyword scoring (fast, no API cost)
        nlp_score = agent.ml_engine.score_counterparty_document(doc_text, entity_name)

        # DeepSeek deep analysis
        try:
            deepseek_score = agent.deepseek.score_counterparty_risk({
                "entity_name": entity_name,
                "nlp_pre_score": nlp_score,
                **entity_data,
            })
        except Exception as e:
            logger.error(f"DeepSeek risk scoring failed: {e}")
            deepseek_score = {"error": str(e)}

        # NLP entity extraction from document
        entities = {}
        if doc_text:
            try:
                entities = agent.deepseek.extract_entities_nlp(doc_text, "counterparty_document")
            except Exception as e:
                logger.warning(f"Entity extraction failed: {e}")

        return jsonify({
            "entity":          entity_name,
            "nlp_score":       nlp_score,
            "deepseek_score":  deepseek_score,
            "extracted_entities": entities,
        })

    @brain_bp.route("/nlp-extract", methods=["POST"])
    def nlp_extract():
        """Extract intelligence entities from a document."""
        data     = request.get_json()
        text     = data.get("text", "")
        doc_type = data.get("doc_type", "procurement")

        if not text:
            return jsonify({"error": "text is required"}), 400

        result = agent.deepseek.extract_entities_nlp(text, doc_type)
        return jsonify(result)

    @brain_bp.route("/signal", methods=["POST"])
    def push_signal():
        """Push a new intelligence signal into the brain queue."""
        signal = request.get_json()
        agent.push_signal(signal)
        return jsonify({"status": "queued", "queue": "brain_signal_queue"})

    # ── Control ───────────────────────────────────────────────────────────────

    @brain_bp.route("/sweep", methods=["POST"])
    def trigger_sweep():
        """Manually trigger an intelligence sweep (async)."""
        def _run():
            try:
                agent.run_sweep()
            except Exception as e:
                logger.error(f"Manual sweep failed: {e}")

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        return jsonify({"status": "sweep_started", "message": "Sweep running in background"})

    @brain_bp.route("/retrain", methods=["POST"])
    def force_retrain():
        """Force ML model retrain from all recorded outcomes."""
        result = agent.feedback.force_retrain()
        return jsonify(result)

    @brain_bp.route("/source-health", methods=["GET"])
    def source_health():
        """Admin view: per-source fetch reliability and current weights."""
        return jsonify(agent.feedback.get_source_reliability_report())

    @brain_bp.route("/feature-importance", methods=["GET"])
    def feature_importance():
        """ML model feature importance — what's driving predictions."""
        fi = agent.ml_engine.feature_importance()
        if not fi:
            return jsonify({"error": "ML model not yet trained"}), 404
        return jsonify(fi)

    app.register_blueprint(brain_bp)
    logger.info("Brain API routes registered at /api/brain/*")
