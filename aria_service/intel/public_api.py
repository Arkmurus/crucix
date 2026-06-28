"""R-F1011 — ARIA Public API & Documentation.

Versioned REST API with interactive documentation, authentication,
rate limiting, and usage tracking.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

logger = logging.getLogger("aria.public_api")


# API version
API_VERSION = "v1"
API_BASE = f"/api/{API_VERSION}"


# API endpoint definitions
API_ENDPOINTS = {
    # Chat & Intelligence
    "chat": {
        "path": "/chat",
        "method": "POST",
        "description": "Send a message to ARIA and get a response",
        "auth": "required",
        "rate_limit": 60,  # per minute
        "request": {
            "message": {"type": "string", "required": True, "description": "Your message to ARIA"},
            "session_id": {"type": "string", "required": False, "description": "Session ID for conversation continuity"},
            "auto_tools": {"type": "boolean", "required": False, "default": True, "description": "Enable automatic tool use"},
        },
        "response": {
            "response": {"type": "string", "description": "ARIA's response"},
            "session_id": {"type": "string", "description": "Session ID for follow-up messages"},
            "tool_used": {"type": "boolean", "description": "Whether a tool was invoked"},
        },
        "example": {
            "request": '{"message": "Screen company ABC Corp for sanctions", "session_id": "user_123"}',
            "response": '{"response": "I found ABC Corp on the OFAC SDN list...", "session_id": "user_123", "tool_used": true}',
        },
    },
    "research": {
        "path": "/research",
        "method": "POST",
        "description": "Perform deep research on a topic",
        "auth": "required",
        "rate_limit": 10,
        "request": {
            "topic": {"type": "string", "required": True, "description": "Research topic or question"},
            "depth": {"type": "string", "required": False, "default": "standard", "enum": ["quick", "standard", "deep"]},
        },
        "response": {
            "findings": {"type": "array", "description": "List of research findings"},
            "sources": {"type": "array", "description": "Sources used"},
            "confidence": {"type": "number", "description": "Overall confidence score"},
        },
    },
    "sanctions_screen": {
        "path": "/sanctions/screen",
        "method": "POST",
        "description": "Screen a name against global sanctions lists",
        "auth": "required",
        "rate_limit": 30,
        "request": {
            "name": {"type": "string", "required": True, "description": "Name to screen"},
            "threshold": {"type": "number", "required": False, "default": 0.78, "description": "Match threshold (0-1)"},
        },
        "response": {
            "matches": {"type": "array", "description": "Sanctions matches found"},
            "match_count": {"type": "integer", "description": "Number of matches"},
        },
    },
    "due_diligence": {
        "path": "/dd",
        "method": "POST",
        "description": "Run a full due diligence report on an entity",
        "auth": "required",
        "rate_limit": 5,
        "request": {
            "entity_name": {"type": "string", "required": True, "description": "Entity to investigate"},
            "jurisdiction": {"type": "string", "required": False, "description": "Jurisdiction ISO2 code"},
        },
        "response": {
            "report_id": {"type": "string", "description": "Report ID for retrieval"},
            "status": {"type": "string", "description": "Report status"},
        },
    },
    "compliance_check": {
        "path": "/compliance/check",
        "method": "POST",
        "description": "Check compliance for an export/transaction",
        "auth": "required",
        "rate_limit": 20,
        "request": {
            "item": {"type": "string", "required": True, "description": "Item to check"},
            "destination": {"type": "string", "required": True, "description": "Destination country ISO2"},
            "end_user": {"type": "string", "required": False, "description": "End user name"},
        },
        "response": {
            "status": {"type": "string", "description": "Compliance status"},
            "restrictions": {"type": "array", "description": "Applicable restrictions"},
        },
    },
    "document_analyze": {
        "path": "/document/analyze",
        "method": "POST",
        "description": "Analyze a document for intelligence",
        "auth": "required",
        "rate_limit": 10,
        "request": {
            "content": {"type": "string", "required": True, "description": "Document content"},
            "analysis_type": {"type": "string", "required": False, "default": "full", "enum": ["full", "entities", "risks"]},
        },
        "response": {
            "entities": {"type": "array", "description": "Extracted entities"},
            "risks": {"type": "array", "description": "Identified risks"},
            "summary": {"type": "string", "description": "Document summary"},
        },
    },
    "health": {
        "path": "/health",
        "method": "GET",
        "description": "Check API health and status",
        "auth": "none",
        "rate_limit": 0,
        "response": {
            "status": {"type": "string", "description": "Service status"},
            "version": {"type": "string", "description": "API version"},
            "uptime": {"type": "number", "description": "Service uptime in seconds"},
        },
    },
}


def get_api_documentation() -> dict[str, Any]:
    """Get the full API documentation."""
    return {
        "api_name": "ARIA Intelligence API",
        "version": API_VERSION,
        "base_url": f"https://aria-intel.fly.dev{API_BASE}",
        "authentication": "Bearer token in Authorization header",
        "rate_limiting": "Per-endpoint rate limits. 429 Too Many Requests on exceed.",
        "endpoints": API_ENDPOINTS,
    }


def get_openapi_spec() -> dict[str, Any]:
    """Generate OpenAPI 3.0 specification."""
    paths = {}
    for name, ep in API_ENDPOINTS.items():
        method = ep["method"].lower()
        path = ep["path"]
        
        path_item = {
            method: {
                "summary": ep["description"],
                "operationId": name,
                "parameters": [],
                "responses": {
                    "200": {
                        "description": "Successful response",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {k: {"type": v.get("type", "string")} for k, v in ep.get("response", {}).items()},
                                }
                            }
                        },
                    },
                    "401": {"description": "Unauthorized — invalid or missing API key"},
                    "429": {"description": "Too Many Requests — rate limit exceeded"},
                },
            }
        }
        
        if ep.get("auth") == "required":
            path_item[method]["security"] = [{"BearerAuth": []}]
        
        if "request" in ep:
            path_item[method]["requestBody"] = {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {k: {"type": v.get("type", "string"), "description": v.get("description", "")} for k, v in ep["request"].items()},
                            "required": [k for k, v in ep["request"].items() if v.get("required")],
                        }
                    }
                },
            }
        
        paths[path] = path_item
    
    return {
        "openapi": "3.0.0",
        "info": {
            "title": "ARIA Intelligence API",
            "version": API_VERSION,
            "description": "State-of-the-art defence intelligence, due diligence, and compliance API. Screen entities against global sanctions lists, run deep research, analyze documents, and check export compliance.",
        },
        "servers": [{"url": f"https://aria-intel.fly.dev{API_BASE}", "description": "Production"}],
        "paths": paths,
        "components": {
            "securitySchemes": {
                "BearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "API key",
                }
            }
        },
    }

# R-F1011 - wire to brain
from .engine_wiring import wire_success, wire_failure
wire_success(module="public_api", summary="Public Api Active", source_id="public_api:R-F1011")
