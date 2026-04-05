"""
OEM Database v2 API Routes — for Python brain service.

When ARIA runs as independent Python service, these routes serve OEM data directly.
Until then, Node.js uses its own oem_db.mjs with the same data structure.

Usage:
    from oem_routes import register_oem_routes
    register_oem_routes(app)
"""

import logging
from flask import Blueprint, jsonify, request

logger = logging.getLogger("crucix.brain.oem")

oem_bp = Blueprint("oem", __name__, url_prefix="/api/brain/oem")


def register_oem_routes(app):
    """Register OEM API routes on Flask app."""

    from oem_database_v2 import get_oem_database

    @oem_bp.route("/search", methods=["GET"])
    def oem_search():
        capability  = request.args.get("capability", "")
        destination = request.args.get("destination", "")
        limit       = int(request.args.get("limit", "10"))
        results = get_oem_database().search_by_capability(capability, destination)
        return jsonify({"results": results[:limit], "count": len(results)})

    @oem_bp.route("/unrestricted", methods=["GET"])
    def oem_unrestricted():
        results = get_oem_database().get_unrestricted_oems()
        return jsonify({"results": results, "count": len(results)})

    @oem_bp.route("/lusophone", methods=["GET"])
    def oem_lusophone():
        results = get_oem_database().get_lusophone_specialists()
        return jsonify({"results": results, "count": len(results)})

    @oem_bp.route("/by-country/<country>", methods=["GET"])
    def oem_by_country(country):
        results = get_oem_database().get_by_country(country)
        return jsonify({"results": results, "count": len(results)})

    @oem_bp.route("/competitors/<market>", methods=["GET"])
    def oem_competitors(market):
        results = get_oem_database().get_competitors_in_market(market)
        return jsonify({"results": results, "count": len(results)})

    @oem_bp.route("/stats", methods=["GET"])
    def oem_stats():
        return jsonify(get_oem_database().stats())

    @oem_bp.route("/stale", methods=["GET"])
    def oem_stale():
        days = int(request.args.get("days", "90"))
        results = get_oem_database().get_stale_entries(days)
        return jsonify({"results": results, "count": len(results)})

    @oem_bp.route("/<oem_id>", methods=["GET"])
    def oem_detail(oem_id):
        db = get_oem_database()
        oem = db._oems.get(oem_id)
        if not oem:
            return jsonify({"error": "OEM not found"}), 404
        return jsonify(db._to_dict(oem))

    app.register_blueprint(oem_bp)
    logger.info(f"OEM v2 routes registered — {get_oem_database().count()} manufacturers")
