"""
API polling engine for MotoGateway integrations.

Periodically calls an external REST API, compares the response to the
last known state, and — when new data arrives — routes it through the
standard IntegrationRunner pipeline (transform → destination).

State (last poll timestamp, last response hash) is persisted in a small
JSON file next to config.json so polls survive restarts.

The poller runs as a daemon thread managed by ApiPollManager, which is
started alongside the WatcherManager from run_dev.py / service_windows.py.
"""
import hashlib
import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import requests

logger = logging.getLogger("motorad.core.api_poll")


# ---------------------------------------------------------------------------
# API poll source config helpers
# ---------------------------------------------------------------------------

def make_api_poll_source_config(
    url: str,
    *,
    method: str = "GET",
    headers: Optional[dict] = None,
    query_params: Optional[dict] = None,
    body: Optional[dict] = None,
    auth_type: str = "none",
    auth_config: Optional[dict] = None,
    poll_interval_seconds: int = 300,
    response_format: str = "json",
    data_path: str = "",
) -> dict:
    """Build the source.config for an api_poll-type integration."""
    return {
        "url": url,
        "method": method.upper(),
        "headers": headers or {},
        "query_params": query_params or {},
        "body": body,
        "auth": {
            "type": auth_type,      # "none", "bearer", "basic", "api_key"
            "config": auth_config or {},
        },
        "poll_interval_seconds": max(30, poll_interval_seconds),
        "response_format": response_format,  # "json", "xml", "csv"
        "data_path": data_path,  # dot-path to extract from response, e.g. "data.items"
    }


# ---------------------------------------------------------------------------
# HTTP client with auth
# ---------------------------------------------------------------------------

def _apply_auth(req_kwargs: dict, auth_config: dict) -> dict:
    """Apply authentication to request kwargs."""
    auth_type = auth_config.get("type", "none")
    cfg = auth_config.get("config", {})

    if auth_type == "bearer":
        req_kwargs.setdefault("headers", {})["Authorization"] = f"Bearer {cfg.get('token', '')}"

    elif auth_type == "basic":
        req_kwargs["auth"] = (cfg.get("username", ""), cfg.get("password", ""))

    elif auth_type == "api_key":
        # API key can go in header or query param
        location = cfg.get("location", "header")  # "header" or "query"
        key_name = cfg.get("key_name", "X-API-Key")
        key_value = cfg.get("key_value", "")
        if location == "header":
            req_kwargs.setdefault("headers", {})[key_name] = key_value
        elif location == "query":
            req_kwargs.setdefault("params", {})[key_name] = key_value

    return req_kwargs


def fetch_api(source_config: dict, timeout: int = 30) -> tuple:
    """Execute one API call per source_config.

    Returns (data, error_message). data is parsed according to
    response_format; error_message is None on success.
    """
    url = source_config["url"]
    method = source_config.get("method", "GET").upper()

    kwargs = {
        "headers": dict(source_config.get("headers", {})),
        "params": dict(source_config.get("query_params", {})),
        "timeout": timeout,
    }

    body = source_config.get("body")
    if body and method in ("POST", "PUT", "PATCH"):
        kwargs["json"] = body

    kwargs = _apply_auth(kwargs, source_config.get("auth", {}))

    try:
        resp = requests.request(method, url, **kwargs)
        resp.raise_for_status()
    except requests.ConnectionError as e:
        return None, f"Connection failed: {e}"
    except requests.Timeout:
        return None, f"Request timed out after {timeout}s"
    except requests.HTTPError as e:
        return None, f"HTTP {resp.status_code}: {resp.text[:500]}"
    except requests.RequestException as e:
        return None, f"Request failed: {e}"

    # Parse response
    fmt = source_config.get("response_format", "json")
    try:
        if fmt == "json":
            data = resp.json()
        elif fmt == "xml":
            from lxml import etree
            root = etree.fromstring(resp.content)
            from .webhook import _xml_element_to_dict
            data = _xml_element_to_dict(root)
        elif fmt == "csv":
            import csv
            import io
            reader = csv.DictReader(io.StringIO(resp.text))
            data = list(reader)
        else:
            data = {"text": resp.text}
    except Exception as e:
        return None, f"Failed to parse {fmt} response: {e}"

    # Extract sub-path if configured
    data_path = source_config.get("data_path", "")
    if data_path:
        for part in data_path.split("."):
            if isinstance(data, dict) and part in data:
                data = data[part]
            else:
                return None, f"data_path '{data_path}' not found in response"

    return data, None


# ---------------------------------------------------------------------------
# Poll state persistence
# ---------------------------------------------------------------------------

def _state_file_path(config_dir: Path, integration_id: str) -> Path:
    return config_dir / "poll_state" / f"{integration_id}.json"


def load_poll_state(config_dir: Path, integration_id: str) -> dict:
    path = _state_file_path(config_dir, integration_id)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("Corrupt poll state for %s, resetting", integration_id)
    return {"last_poll": None, "last_hash": None}


def save_poll_state(config_dir: Path, integration_id: str, state: dict):
    path = _state_file_path(config_dir, integration_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _data_hash(data) -> str:
    """Deterministic hash of response data for change detection."""
    serialized = json.dumps(data, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()[:16]


# ---------------------------------------------------------------------------
# API Poll Manager
# ---------------------------------------------------------------------------

class ApiPollManager:
    """Manages polling threads for all api_poll-type integrations.

    Call start() once at app startup; call reload() when integrations
    change via the settings UI. Call stop() at shutdown.
    """

    def __init__(self, get_config: Callable, process_callback: Callable):
        """
        Args:
            get_config: callable returning the current app config dict.
            process_callback: called as process_callback(data, integration_config, app_config)
                when new data arrives. This is typically engine.process_api_data().
        """
        self.get_config = get_config
        self.process_callback = process_callback
        self._stop_event = threading.Event()
        self._poll_threads: Dict[str, threading.Thread] = {}
        self._active_integrations: Dict[str, dict] = {}

    def start(self):
        self.reload()

    def stop(self):
        self._stop_event.set()
        for t in self._poll_threads.values():
            t.join(timeout=5)
        self._poll_threads.clear()

    def reload(self):
        """Restart polling threads to match current config."""
        # Stop existing threads
        self._stop_event.set()
        for t in self._poll_threads.values():
            t.join(timeout=5)
        self._poll_threads.clear()
        self._active_integrations.clear()
        self._stop_event = threading.Event()

        cfg = self.get_config()
        for integration in cfg.get("integrations", []):
            if not integration.get("enabled", True):
                continue
            if integration.get("source", {}).get("type") != "api_poll":
                continue

            integ_id = integration["id"]
            self._active_integrations[integ_id] = integration

            t = threading.Thread(
                target=self._poll_loop,
                args=(integ_id,),
                daemon=True,
                name=f"api-poll-{integ_id}",
            )
            self._poll_threads[integ_id] = t
            t.start()
            logger.info("Started API poll thread for integration '%s' (%s)",
                        integration.get("name", integ_id), integ_id)

    def _poll_loop(self, integration_id: str):
        """Single integration poll loop — runs in its own thread."""
        from ..config import CONFIG_DIR

        while not self._stop_event.is_set():
            integration = self._active_integrations.get(integration_id)
            if not integration:
                break

            source_cfg = integration["source"]["config"]
            interval = source_cfg.get("poll_interval_seconds", 300)

            try:
                data, error = fetch_api(source_cfg)
                if error:
                    logger.warning("API poll %s failed: %s", integration_id, error)
                    self._stop_event.wait(min(interval, 60))
                    continue

                # Check for changes
                state = load_poll_state(CONFIG_DIR, integration_id)
                current_hash = _data_hash(data)

                if current_hash != state.get("last_hash"):
                    logger.info("API poll %s: new data detected", integration_id)
                    app_config = self.get_config()
                    try:
                        self.process_callback(data, integration, app_config)
                    except Exception:
                        logger.exception("API poll %s: processing failed", integration_id)

                    state["last_hash"] = current_hash

                state["last_poll"] = datetime.now().isoformat(timespec="seconds")
                save_poll_state(CONFIG_DIR, integration_id, state)

            except Exception:
                logger.exception("API poll %s: unexpected error", integration_id)

            self._stop_event.wait(interval)

    def get_status(self) -> list:
        """Return status of all active poll integrations."""
        from ..config import CONFIG_DIR

        statuses = []
        for integ_id, integ in self._active_integrations.items():
            state = load_poll_state(CONFIG_DIR, integ_id)
            statuses.append({
                "integration_id": integ_id,
                "name": integ.get("name", integ_id),
                "url": integ["source"]["config"].get("url", ""),
                "interval": integ["source"]["config"].get("poll_interval_seconds", 300),
                "last_poll": state.get("last_poll"),
                "thread_alive": integ_id in self._poll_threads and self._poll_threads[integ_id].is_alive(),
            })
        return statuses
