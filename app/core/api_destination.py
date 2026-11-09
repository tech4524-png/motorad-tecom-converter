"""
API destination for MotoGateway integrations.

Sends transformed data to an external REST API endpoint. Supports
configurable HTTP method, headers, authentication, and basic retry logic.

Registered as destination type "api" in the integration framework.
"""
import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

import requests

logger = logging.getLogger("motorad.core.api_destination")


def make_api_destination_config(
    url: str,
    *,
    method: str = "POST",
    headers: Optional[dict] = None,
    auth_type: str = "none",
    auth_config: Optional[dict] = None,
    content_type: str = "application/json",
    retry_count: int = 2,
    retry_delay_seconds: int = 5,
    timeout_seconds: int = 30,
) -> dict:
    """Build the destination.config for an api-type integration."""
    return {
        "url": url,
        "method": method.upper(),
        "headers": headers or {},
        "auth": {
            "type": auth_type,
            "config": auth_config or {},
        },
        "content_type": content_type,
        "retry_count": max(0, retry_count),
        "retry_delay_seconds": max(1, retry_delay_seconds),
        "timeout_seconds": max(5, timeout_seconds),
    }


def _apply_auth(kwargs: dict, auth_config: dict) -> dict:
    """Apply authentication to request kwargs."""
    auth_type = auth_config.get("type", "none")
    cfg = auth_config.get("config", {})

    if auth_type == "bearer":
        kwargs.setdefault("headers", {})["Authorization"] = f"Bearer {cfg.get('token', '')}"
    elif auth_type == "basic":
        kwargs["auth"] = (cfg.get("username", ""), cfg.get("password", ""))
    elif auth_type == "api_key":
        location = cfg.get("location", "header")
        key_name = cfg.get("key_name", "X-API-Key")
        key_value = cfg.get("key_value", "")
        if location == "header":
            kwargs.setdefault("headers", {})[key_name] = key_value
        elif location == "query":
            kwargs.setdefault("params", {})[key_name] = key_value

    return kwargs


def send_to_api(data: Any, dest_config: dict) -> dict:
    """Send data to the configured API endpoint.

    Returns a dict with:
      - ok: bool
      - status_code: int (or None on connection failure)
      - response_body: str (truncated)
      - message: str
      - attempts: int
    """
    url = dest_config["url"]
    method = dest_config.get("method", "POST").upper()
    content_type = dest_config.get("content_type", "application/json")
    timeout = dest_config.get("timeout_seconds", 30)
    retries = dest_config.get("retry_count", 2)
    retry_delay = dest_config.get("retry_delay_seconds", 5)

    headers = dict(dest_config.get("headers", {}))
    headers.setdefault("Content-Type", content_type)

    kwargs = {
        "headers": headers,
        "timeout": timeout,
    }
    kwargs = _apply_auth(kwargs, dest_config.get("auth", {}))

    # Serialize data based on content type
    if content_type == "application/json":
        if isinstance(data, bytes):
            kwargs["data"] = data
            kwargs["headers"]["Content-Type"] = "application/xml"
        elif isinstance(data, str):
            kwargs["data"] = data.encode("utf-8")
        else:
            kwargs["json"] = data
            # Remove data key if json is set
            kwargs.pop("data", None)
    elif content_type in ("application/xml", "text/xml"):
        if isinstance(data, bytes):
            kwargs["data"] = data
        elif isinstance(data, str):
            kwargs["data"] = data.encode("utf-8")
        else:
            kwargs["data"] = json.dumps(data).encode("utf-8")
    else:
        if isinstance(data, (dict, list)):
            kwargs["data"] = json.dumps(data).encode("utf-8")
        elif isinstance(data, bytes):
            kwargs["data"] = data
        else:
            kwargs["data"] = str(data).encode("utf-8")

    last_error = None
    for attempt in range(1, retries + 2):  # retries + 1 initial attempt
        try:
            resp = requests.request(method, url, **kwargs)

            if resp.status_code < 400:
                return {
                    "ok": True,
                    "status_code": resp.status_code,
                    "response_body": resp.text[:1000],
                    "message": f"Success ({resp.status_code})",
                    "attempts": attempt,
                }

            last_error = f"HTTP {resp.status_code}: {resp.text[:500]}"

            # Don't retry on client errors (4xx) except 429
            if 400 <= resp.status_code < 500 and resp.status_code != 429:
                return {
                    "ok": False,
                    "status_code": resp.status_code,
                    "response_body": resp.text[:1000],
                    "message": last_error,
                    "attempts": attempt,
                }

        except requests.ConnectionError as e:
            last_error = f"Connection failed: {e}"
        except requests.Timeout:
            last_error = f"Timed out after {timeout}s"
        except requests.RequestException as e:
            last_error = f"Request error: {e}"

        if attempt <= retries:
            logger.info("API destination %s: attempt %d failed, retrying in %ds — %s",
                        url, attempt, retry_delay, last_error)
            time.sleep(retry_delay)

    return {
        "ok": False,
        "status_code": None,
        "response_body": "",
        "message": last_error or "Unknown error",
        "attempts": retries + 1,
    }


def write_api_destination(
    output_data: Any,
    output_dir: str,
    output_name: str,
    dest_config: dict,
) -> Path:
    """Integration framework destination handler for type "api".

    Sends data to the API and writes a receipt file to output_dir
    with the response details (for audit trail / logging).

    The function signature matches the file destination handler so it
    plugs into IntegrationRunner.write_destination() seamlessly.
    """
    result = send_to_api(output_data, dest_config)

    # Write receipt file
    output_path = Path(output_dir) / output_name
    output_path.parent.mkdir(parents=True, exist_ok=True)

    receipt = {
        "sent_to": dest_config.get("url"),
        "method": dest_config.get("method", "POST"),
        "timestamp": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "result": result,
    }
    output_path.write_text(json.dumps(receipt, indent=2, ensure_ascii=False), encoding="utf-8")

    if not result["ok"]:
        raise ValueError(f"API destination failed: {result['message']}")

    logger.info("Sent data to %s — %s (attempts: %d)",
                dest_config.get("url"), result["message"], result["attempts"])

    return output_path
