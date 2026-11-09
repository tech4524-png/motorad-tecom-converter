"""
Webhook receiver for MotoGateway integrations.

Generates unique endpoint URLs per integration. When an external system
POSTs data to that URL, the payload is validated, converted to the
integration's expected format, and routed through the standard
IntegrationRunner pipeline (transform → destination).

Webhook endpoints are stored as part of the integration config under
source.config.webhook_secret / source.config.webhook_path. The Flask
blueprint registers a single catch-all route that dispatches by path.
"""
import hashlib
import hmac
import json
import logging
import secrets
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("motorad.core.webhook")


def generate_webhook_path() -> str:
    """Generate a unique, URL-safe webhook path segment."""
    return secrets.token_urlsafe(16)


def generate_webhook_secret() -> str:
    """Generate a shared secret for HMAC signature verification."""
    return secrets.token_hex(32)


def make_webhook_source_config(
    *,
    webhook_path: Optional[str] = None,
    webhook_secret: Optional[str] = None,
    content_types: Optional[list] = None,
    require_signature: bool = False,
) -> dict:
    """Build the source.config for a webhook-type integration."""
    return {
        "webhook_path": webhook_path or generate_webhook_path(),
        "webhook_secret": webhook_secret or generate_webhook_secret(),
        "content_types": content_types or ["application/json"],
        "require_signature": require_signature,
    }


def verify_signature(payload_bytes: bytes, secret: str, signature: str) -> bool:
    """Verify HMAC-SHA256 signature.

    Supports formats:
      - sha256=<hex>   (GitHub-style)
      - plain hex digest
    """
    if not signature or not secret:
        return False

    expected = hmac.new(
        secret.encode("utf-8"), payload_bytes, hashlib.sha256
    ).hexdigest()

    # Strip optional "sha256=" prefix
    if signature.startswith("sha256="):
        signature = signature[7:]

    return hmac.compare_digest(expected, signature)


def parse_webhook_payload(
    body: bytes,
    content_type: str,
) -> Tuple[Any, Optional[str]]:
    """Parse incoming webhook body based on Content-Type.

    Returns (parsed_data, error_message). error_message is None on success.
    """
    ct = (content_type or "").lower().split(";")[0].strip()

    if ct == "application/json":
        try:
            data = json.loads(body)
            return data, None
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            return None, f"Invalid JSON payload: {e}"

    elif ct in ("application/xml", "text/xml"):
        try:
            from lxml import etree
            root = etree.fromstring(body)
            # Convert XML to a simple dict structure
            data = _xml_element_to_dict(root)
            return data, None
        except Exception as e:
            return None, f"Invalid XML payload: {e}"

    elif ct == "application/x-www-form-urlencoded":
        try:
            from urllib.parse import parse_qs
            parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True)
            # Flatten single-value lists
            data = {k: v[0] if len(v) == 1 else v for k, v in parsed.items()}
            return data, None
        except Exception as e:
            return None, f"Invalid form data: {e}"

    elif ct == "text/plain":
        try:
            text = body.decode("utf-8")
            # Try JSON first
            try:
                return json.loads(text), None
            except json.JSONDecodeError:
                return {"text": text}, None
        except UnicodeDecodeError as e:
            return None, f"Cannot decode text payload: {e}"

    else:
        # Accept unknown types as raw bytes wrapped in dict
        return {"raw": body.hex(), "content_type": ct}, None


def _xml_element_to_dict(element) -> dict:
    """Recursively convert an lxml element to a dict."""
    result = {}
    # Strip namespace from tag
    tag = element.tag
    if "}" in tag:
        tag = tag.split("}", 1)[1]

    children = list(element)
    if not children:
        # Leaf node
        return {tag: element.text or ""}

    child_dict = {}
    for child in children:
        child_data = _xml_element_to_dict(child)
        for k, v in child_data.items():
            if k in child_dict:
                # Multiple children with same tag → list
                if not isinstance(child_dict[k], list):
                    child_dict[k] = [child_dict[k]]
                child_dict[k].append(v)
            else:
                child_dict[k] = v
    return {tag: child_dict}


# ---------------------------------------------------------------------------
# Webhook event log (in-memory ring buffer for debugging)
# ---------------------------------------------------------------------------

_MAX_EVENTS = 100
_webhook_events: list = []


def log_webhook_event(
    integration_id: str,
    path: str,
    status: str,
    message: str = "",
    payload_size: int = 0,
):
    """Record a webhook event for the management UI."""
    event = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "integration_id": integration_id,
        "path": path,
        "status": status,  # "received", "processed", "error", "rejected"
        "message": message,
        "payload_size": payload_size,
    }
    _webhook_events.append(event)
    if len(_webhook_events) > _MAX_EVENTS:
        _webhook_events.pop(0)


def get_webhook_events(integration_id: str = None, limit: int = 50) -> list:
    """Return recent webhook events, newest first."""
    events = _webhook_events
    if integration_id:
        events = [e for e in events if e["integration_id"] == integration_id]
    return list(reversed(events[-limit:]))
