"""
Tests for Phase 2: Webhook receiver, API polling, API destination,
and the integrations management UI route.

Run with: python -m pytest tests/test_phase2_integrations.py -v
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Set config dir to temp so tests don't touch real config
_test_config_dir = tempfile.mkdtemp(prefix="motorad_test_phase2_")
os.environ["MOTORAD_CONFIG_DIR"] = _test_config_dir


# ---------------------------------------------------------------------------
# 1. Webhook utilities
# ---------------------------------------------------------------------------

class TestWebhookUtilities:
    """Tests for core/webhook.py helpers."""

    def test_generate_webhook_path_unique(self):
        from app.core.webhook import generate_webhook_path
        paths = {generate_webhook_path() for _ in range(50)}
        assert len(paths) == 50

    def test_generate_webhook_secret_length(self):
        from app.core.webhook import generate_webhook_secret
        secret = generate_webhook_secret()
        assert len(secret) == 64  # 32 bytes hex = 64 chars

    def test_make_webhook_source_config(self):
        from app.core.webhook import make_webhook_source_config
        cfg = make_webhook_source_config(require_signature=True)
        assert cfg["webhook_path"]
        assert cfg["webhook_secret"]
        assert cfg["require_signature"] is True
        assert "application/json" in cfg["content_types"]

    def test_verify_signature_valid(self):
        import hmac, hashlib
        from app.core.webhook import verify_signature
        secret = "test_secret"
        payload = b'{"key": "value"}'
        sig = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
        assert verify_signature(payload, secret, sig)

    def test_verify_signature_with_prefix(self):
        import hmac, hashlib
        from app.core.webhook import verify_signature
        secret = "test_secret"
        payload = b'{"key": "value"}'
        sig = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
        assert verify_signature(payload, secret, sig)

    def test_verify_signature_invalid(self):
        from app.core.webhook import verify_signature
        assert not verify_signature(b"data", "secret", "wrong_signature")

    def test_parse_webhook_payload_json(self):
        from app.core.webhook import parse_webhook_payload
        data, err = parse_webhook_payload(b'{"a": 1}', "application/json")
        assert err is None
        assert data == {"a": 1}

    def test_parse_webhook_payload_invalid_json(self):
        from app.core.webhook import parse_webhook_payload
        data, err = parse_webhook_payload(b"not json", "application/json")
        assert data is None
        assert "Invalid JSON" in err

    def test_webhook_event_log(self):
        from app.core.webhook import log_webhook_event, get_webhook_events
        # Use a unique integration_id to avoid cross-test pollution
        test_id = "test_integ_unique_123"
        log_webhook_event(test_id, "/webhook/test", "received", "Test message")
        events = get_webhook_events(test_id)
        assert len(events) >= 1
        assert events[0]["integration_id"] == test_id
        assert events[0]["status"] == "received"


# ---------------------------------------------------------------------------
# 2. API poll source config and fetch
# ---------------------------------------------------------------------------

class TestApiPollConfig:
    """Tests for core/api_poll.py config and state helpers."""

    def test_make_api_poll_source_config(self):
        from app.core.api_poll import make_api_poll_source_config
        cfg = make_api_poll_source_config(
            url="https://api.example.com/data",
            method="POST",
            poll_interval_seconds=60,
            data_path="items.records",
        )
        assert cfg["url"] == "https://api.example.com/data"
        assert cfg["method"] == "POST"
        assert cfg["poll_interval_seconds"] == 60
        assert cfg["data_path"] == "items.records"
        assert cfg["auth"]["type"] == "none"

    def test_make_api_poll_min_interval(self):
        from app.core.api_poll import make_api_poll_source_config
        cfg = make_api_poll_source_config(url="http://x", poll_interval_seconds=5)
        assert cfg["poll_interval_seconds"] == 30  # minimum enforced

    def test_poll_state_persistence(self):
        from app.core.api_poll import save_poll_state, load_poll_state
        state_dir = Path(_test_config_dir)
        save_poll_state(state_dir, "test_poll_1", {
            "last_poll": "2026-01-01T00:00:00",
            "last_hash": "abc123",
        })
        loaded = load_poll_state(state_dir, "test_poll_1")
        assert loaded["last_poll"] == "2026-01-01T00:00:00"
        assert loaded["last_hash"] == "abc123"

    def test_poll_state_missing_returns_defaults(self):
        from app.core.api_poll import load_poll_state
        state = load_poll_state(Path(_test_config_dir), "nonexistent")
        assert state["last_poll"] is None
        assert state["last_hash"] is None


class TestApiPollFetch:
    """Tests for fetch_api() HTTP client."""

    @patch("app.core.api_poll.requests.request")
    def test_fetch_api_json_success(self, mock_req):
        from app.core.api_poll import fetch_api
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"items": [1, 2, 3]}
        mock_resp.raise_for_status.return_value = None
        mock_req.return_value = mock_resp

        data, err = fetch_api({"url": "http://test", "method": "GET", "response_format": "json"})
        assert err is None
        assert data == {"items": [1, 2, 3]}

    @patch("app.core.api_poll.requests.request")
    def test_fetch_api_with_data_path(self, mock_req):
        from app.core.api_poll import fetch_api
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": {"items": [10, 20]}}
        mock_resp.raise_for_status.return_value = None
        mock_req.return_value = mock_resp

        data, err = fetch_api({
            "url": "http://test", "method": "GET",
            "response_format": "json", "data_path": "data.items",
        })
        assert err is None
        assert data == [10, 20]

    @patch("app.core.api_poll.requests.request")
    def test_fetch_api_connection_error(self, mock_req):
        import requests as req_lib
        from app.core.api_poll import fetch_api
        mock_req.side_effect = req_lib.ConnectionError("refused")
        data, err = fetch_api({"url": "http://test", "method": "GET"})
        assert data is None
        assert "Connection failed" in err

    @patch("app.core.api_poll.requests.request")
    def test_fetch_api_bearer_auth(self, mock_req):
        from app.core.api_poll import fetch_api
        mock_resp = MagicMock()
        mock_resp.json.return_value = {}
        mock_resp.raise_for_status.return_value = None
        mock_req.return_value = mock_resp

        fetch_api({
            "url": "http://test", "method": "GET", "response_format": "json",
            "auth": {"type": "bearer", "config": {"token": "mytoken123"}},
        })
        call_kwargs = mock_req.call_args[1]
        assert call_kwargs["headers"]["Authorization"] == "Bearer mytoken123"


# ---------------------------------------------------------------------------
# 3. API destination
# ---------------------------------------------------------------------------

class TestApiDestination:
    """Tests for core/api_destination.py."""

    @patch("app.core.api_destination.requests.request")
    def test_write_api_destination_success(self, mock_req):
        from app.core.api_destination import write_api_destination
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = '{"ok": true}'
        mock_resp.raise_for_status.return_value = None
        mock_req.return_value = mock_resp

        result = write_api_destination(
            output_data={"order": "123"},
            output_dir=_test_config_dir,
            output_name="test",
            dest_config={
                "url": "https://target.example.com/api",
                "method": "POST",
                "content_type": "application/json",
            },
        )
        assert result is not None
        mock_req.assert_called_once()
        call_kwargs = mock_req.call_args
        assert call_kwargs[0][0] == "POST"

    @patch("app.core.api_destination.requests.request")
    def test_write_api_destination_retry_on_failure(self, mock_req):
        import requests as req_lib
        from app.core.api_destination import write_api_destination
        # First call fails, second succeeds
        fail_resp = MagicMock()
        fail_resp.raise_for_status.side_effect = req_lib.HTTPError("500")
        fail_resp.status_code = 500
        fail_resp.text = "Server Error"

        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.text = '{"ok": true}'
        ok_resp.raise_for_status.return_value = None

        mock_req.side_effect = [fail_resp, ok_resp]

        result = write_api_destination(
            output_data={"order": "123"},
            output_dir=_test_config_dir,
            output_name="test",
            dest_config={
                "url": "https://target.example.com/api",
                "method": "POST",
                "content_type": "application/json",
                "retry_count": 1,
                "retry_delay_seconds": 0,
            },
        )
        assert mock_req.call_count == 2


# ---------------------------------------------------------------------------
# 4. Phase 2 handler registration
# ---------------------------------------------------------------------------

class TestPhase2Registration:
    """Tests for builtin/__init__.py Phase 2 handler registration."""

    def test_webhook_source_registered(self):
        from app.builtin import register_all
        from app.core.integration import SOURCE_TYPES
        register_all()
        assert "webhook" in SOURCE_TYPES

    def test_api_poll_source_registered(self):
        from app.core.integration import SOURCE_TYPES
        assert "api_poll" in SOURCE_TYPES

    def test_api_destination_registered(self):
        from app.core.integration import DESTINATION_TYPES
        assert "api" in DESTINATION_TYPES

    def test_passthrough_transform_type_valid(self):
        from app.core.integration import TRANSFORM_TYPES
        assert "passthrough" in TRANSFORM_TYPES


# ---------------------------------------------------------------------------
# 5. Integration config CRUD (webhook/api_poll specific)
# ---------------------------------------------------------------------------

class TestIntegrationCRUD:
    """Tests for config.py integration CRUD with Phase 2 types."""

    def setup_method(self):
        from app import config as cfg_mod
        cfg = cfg_mod.load_config()
        cfg["integrations"] = []
        cfg_mod.save_config(cfg)

    def test_add_webhook_integration(self):
        from app import config as cfg_mod
        from app.core.webhook import make_webhook_source_config
        cfg = cfg_mod.load_config()
        integ = {
            "id": "wh_test_1",
            "name": "Test WH",
            "enabled": True,
            "source": {"type": "webhook", "config": make_webhook_source_config()},
            "transform": {"type": "passthrough"},
            "destination": {"type": "file", "config": {"output_dir": "/tmp", "format": "json"}},
        }
        ok, err = cfg_mod.add_integration(cfg, integ)
        assert ok, err
        assert len(cfg_mod.get_integrations(cfg)) == 1

    def test_add_api_poll_integration(self):
        from app import config as cfg_mod
        from app.core.api_poll import make_api_poll_source_config
        cfg = cfg_mod.load_config()
        integ = {
            "id": "ap_test_1",
            "name": "Test AP",
            "enabled": True,
            "source": {"type": "api_poll", "config": make_api_poll_source_config(url="http://x")},
            "transform": {"type": "passthrough"},
            "destination": {"type": "file", "config": {"output_dir": "/tmp", "format": "json"}},
        }
        ok, err = cfg_mod.add_integration(cfg, integ)
        assert ok, err

    def test_add_api_dest_integration(self):
        from app import config as cfg_mod
        cfg = cfg_mod.load_config()
        integ = {
            "id": "ad_test_1",
            "name": "Test AD",
            "enabled": True,
            "source": {"type": "webhook", "config": {"webhook_path": "x", "webhook_secret": "y", "content_types": ["application/json"], "require_signature": False}},
            "transform": {"type": "passthrough"},
            "destination": {"type": "api", "config": {"url": "http://target", "method": "POST"}},
        }
        ok, err = cfg_mod.add_integration(cfg, integ)
        assert ok, err

    def test_delete_integration(self):
        from app import config as cfg_mod
        cfg = cfg_mod.load_config()
        integ = {
            "id": "del_test",
            "name": "To Delete",
            "enabled": True,
            "source": {"type": "webhook", "config": {"webhook_path": "x", "webhook_secret": "y", "content_types": [], "require_signature": False}},
            "transform": {"type": "passthrough"},
            "destination": {"type": "file", "config": {"output_dir": "/tmp", "format": "json"}},
        }
        cfg_mod.add_integration(cfg, integ)
        ok, err = cfg_mod.delete_integration(cfg, "del_test")
        assert ok, err
        assert len(cfg_mod.get_integrations(cfg)) == 0


# ---------------------------------------------------------------------------
# 6. Integrations web UI route
# ---------------------------------------------------------------------------

class TestIntegrationsRoute:
    """Tests for the /integrations Flask route."""

    @pytest.fixture
    def client(self):
        from app.webapp import create_app
        app = create_app()
        app.config["TESTING"] = True
        with app.test_client() as c:
            c.post("/login", data={"username": "admin", "password": "Drr2029@@"})
            yield c
        # Clean up integrations after each test
        from app import config as cfg_mod
        cfg = cfg_mod.load_config()
        cfg["integrations"] = []
        cfg_mod.save_config(cfg)

    def test_get_integrations_page(self, client):
        resp = client.get("/integrations")
        assert resp.status_code == 200

    def test_get_integrations_tabs(self, client):
        for tab in ["webhooks", "api_poll", "api_dest"]:
            resp = client.get(f"/integrations?tab={tab}")
            assert resp.status_code == 200

    def test_add_webhook_via_form(self, client):
        resp = client.post("/integrations", data={
            "action": "add_webhook",
            "name": "My Webhook",
            "output_dir": "/tmp/wh",
            "output_format": "json",
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b"My Webhook" in resp.data

    def test_add_api_poll_via_form(self, client):
        resp = client.post("/integrations?tab=api_poll", data={
            "action": "add_api_poll",
            "name": "My Poll",
            "url": "https://api.example.com",
            "poll_interval_seconds": "120",
            "output_dir": "/tmp/ap",
            "output_format": "json",
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b"My Poll" in resp.data

    def test_delete_integration_via_form(self, client):
        # First add one
        client.post("/integrations", data={
            "action": "add_webhook",
            "name": "To Delete",
            "output_dir": "/tmp",
            "output_format": "json",
        })
        from app import config as cfg_mod
        cfg = cfg_mod.load_config()
        integ_id = cfg["integrations"][0]["id"]

        resp = client.post("/integrations", data={
            "action": "delete_integration",
            "integration_id": integ_id,
        }, follow_redirects=True)
        assert resp.status_code == 200

        cfg = cfg_mod.load_config()
        assert len(cfg["integrations"]) == 0

    def test_update_webhook_via_form(self, client):
        # Add a webhook
        client.post("/integrations", data={
            "action": "add_webhook",
            "name": "Original Name",
            "output_dir": "/tmp",
            "output_format": "json",
        })
        from app import config as cfg_mod
        cfg = cfg_mod.load_config()
        integ_id = cfg["integrations"][0]["id"]

        # Update it
        resp = client.post("/integrations", data={
            "action": "update_webhook",
            "integration_id": integ_id,
            "name": "Updated Name",
            "webhook_secret": "new_secret",
            "output_dir": "/tmp/new",
            "output_format": "xml",
            "error_dir": "/tmp/err",
            "enabled": "on",
        }, follow_redirects=True)
        assert resp.status_code == 200

        cfg = cfg_mod.load_config()
        integ = cfg_mod.get_integration_by_id(cfg, integ_id)
        assert integ["name"] == "Updated Name"
        assert integ["destination"]["config"]["format"] == "xml"
