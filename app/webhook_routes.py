"""
Flask blueprint for webhook endpoints.

Registers a catch-all route at /webhook/<path> that dispatches incoming
POST requests to the matching integration's pipeline.

Also provides management routes (under /api/webhooks/) for the admin UI
to list, test, and view event logs.
"""
import json
import logging
import tempfile
from datetime import datetime
from pathlib import Path

from flask import Blueprint, request, jsonify, current_app

from .core.webhook import (
    verify_signature,
    parse_webhook_payload,
    log_webhook_event,
    get_webhook_events,
)
from . import config as config_module
from . import engine
from . import db

logger = logging.getLogger("motorad.webhook_routes")

webhook_bp = Blueprint("webhooks", __name__)


def _find_integration_by_webhook_path(cfg, webhook_path):
    """Find the integration config whose webhook path matches."""
    for integration in cfg.get("integrations", []):
        if not integration.get("enabled", True):
            continue
        if integration.get("source", {}).get("type") != "webhook":
            continue
        src_cfg = integration["source"]["config"]
        if src_cfg.get("webhook_path") == webhook_path:
            return integration
    return None


# ---------------------------------------------------------------------------
# Webhook receiver endpoint
# ---------------------------------------------------------------------------

@webhook_bp.route("/webhook/<path:webhook_path>", methods=["POST"])
def receive_webhook(webhook_path):
    """Catch-all webhook receiver.

    External systems POST data here. The path segment identifies which
    integration should process the payload.
    """
    cfg = config_module.load_config()
    integration = _find_integration_by_webhook_path(cfg, webhook_path)

    if integration is None:
        log_webhook_event("unknown", webhook_path, "rejected", "No matching integration")
        return jsonify({"error": "Unknown webhook endpoint"}), 404

    integ_id = integration["id"]
    integ_name = integration.get("name", integ_id)
    src_cfg = integration["source"]["config"]
    body = request.get_data()

    # Signature verification (optional)
    if src_cfg.get("require_signature"):
        sig = request.headers.get("X-Signature-256") or request.headers.get("X-Hub-Signature-256", "")
        if not verify_signature(body, src_cfg.get("webhook_secret", ""), sig):
            log_webhook_event(integ_id, webhook_path, "rejected",
                              "Invalid signature", len(body))
            return jsonify({"error": "Invalid signature"}), 401

    # Content-type check
    content_type = request.content_type or "application/json"
    allowed_types = src_cfg.get("content_types", ["application/json"])
    ct_base = content_type.split(";")[0].strip().lower()
    if allowed_types and ct_base not in allowed_types:
        log_webhook_event(integ_id, webhook_path, "rejected",
                          f"Content-Type '{ct_base}' not accepted", len(body))
        return jsonify({"error": f"Content-Type '{ct_base}' not accepted"}), 415

    # Parse payload
    data, parse_error = parse_webhook_payload(body, content_type)
    if parse_error:
        log_webhook_event(integ_id, webhook_path, "error", parse_error, len(body))
        return jsonify({"error": parse_error}), 400

    log_webhook_event(integ_id, webhook_path, "received",
                      f"Payload received ({len(body)} bytes)", len(body))

    # Process through integration pipeline
    try:
        result = engine.process_webhook_data(data, integration, cfg)

        if result.ok:
            log_webhook_event(integ_id, webhook_path, "processed",
                              f"Success: {result.message}", len(body))
            return jsonify({
                "status": "ok",
                "message": result.message,
                "output": str(result.output_path) if result.output_path else None,
            }), 200
        else:
            log_webhook_event(integ_id, webhook_path, "error",
                              result.message, len(body))
            return jsonify({"status": "error", "message": result.message}), 422

    except Exception as e:
        logger.exception("Webhook processing failed for %s", integ_name)
        log_webhook_event(integ_id, webhook_path, "error", str(e), len(body))
        return jsonify({"status": "error", "message": str(e)}), 500


# ---------------------------------------------------------------------------
# Management API (for admin UI)
# ---------------------------------------------------------------------------

@webhook_bp.route("/api/webhooks", methods=["GET"])
def list_webhooks():
    """List all webhook-type integrations with their URLs."""
    cfg = config_module.load_config()
    webhooks = []
    for integration in cfg.get("integrations", []):
        if integration.get("source", {}).get("type") != "webhook":
            continue
        src_cfg = integration["source"]["config"]
        webhooks.append({
            "id": integration["id"],
            "name": integration.get("name", ""),
            "enabled": integration.get("enabled", True),
            "webhook_path": src_cfg.get("webhook_path", ""),
            "webhook_url": f"/webhook/{src_cfg.get('webhook_path', '')}",
            "require_signature": src_cfg.get("require_signature", False),
            "content_types": src_cfg.get("content_types", []),
            "created_at": integration.get("created_at", ""),
        })
    return jsonify(webhooks)


@webhook_bp.route("/api/webhooks/<integration_id>/events", methods=["GET"])
def webhook_events(integration_id):
    """Return recent events for a specific webhook integration."""
    limit = request.args.get("limit", 50, type=int)
    events = get_webhook_events(integration_id, limit)
    return jsonify(events)


@webhook_bp.route("/api/webhooks/<integration_id>/test", methods=["POST"])
def test_webhook(integration_id):
    """Send a test payload through a webhook integration."""
    cfg = config_module.load_config()
    integration = config_module.get_integration_by_id(cfg, integration_id)

    if not integration:
        return jsonify({"error": "Integration not found"}), 404

    if integration.get("source", {}).get("type") != "webhook":
        return jsonify({"error": "Not a webhook integration"}), 400

    # Use provided test data or a sample payload
    test_data = request.get_json(silent=True) or {"test": True, "timestamp": datetime.now().isoformat()}

    try:
        result = engine.process_webhook_data(test_data, integration, cfg)
        return jsonify({
            "ok": result.ok,
            "message": result.message,
            "output": str(result.output_path) if result.output_path else None,
        })
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500
