"""
Tests for Phase 4: Integration Wizard UI.

Covers:
  - GET /integrations/new (wizard page)
  - POST /integrations/wizard/save for each source/transform/dest type
  - Validation errors
  - Redirect to mapping editor when transform=mapping
  - i18n key coverage

Run with: python -m pytest tests/test_phase4_wizard.py -v
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_test_config_dir = tempfile.mkdtemp(prefix="motorad_test_phase4_")
os.environ["MOTORAD_CONFIG_DIR"] = _test_config_dir


# ---------------------------------------------------------------------------
# Fixture: authenticated Flask test client
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    from app.webapp import create_app
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        c.post("/login", data={"username": "admin", "password": "Drr2029@@"})
        yield c
    # Clean up after each test
    from app import config as cfg_mod
    cfg = cfg_mod.load_config()
    cfg["integrations"] = []
    cfg_mod.save_config(cfg)


def _get_integrations():
    """Helper: read current integrations from config."""
    from app import config as cfg_mod
    return cfg_mod.load_config().get("integrations", [])


# ---------------------------------------------------------------------------
# 1. Wizard page loads
# ---------------------------------------------------------------------------

class TestWizardPage:
    """GET /integrations/new should render the wizard."""

    def test_wizard_page_loads(self, client):
        resp = client.get("/integrations/new")
        assert resp.status_code == 200
        assert b"wizard" in resp.data.lower() or b"step" in resp.data.lower()

    def test_wizard_requires_login(self):
        from app.webapp import create_app
        app = create_app()
        app.config["TESTING"] = True
        with app.test_client() as c:
            resp = c.get("/integrations/new")
            # Should redirect to login
            assert resp.status_code == 302
            assert "/login" in resp.headers.get("Location", "")


# ---------------------------------------------------------------------------
# 2. Wizard save — file source
# ---------------------------------------------------------------------------

class TestWizardSaveFileSource:
    """POST /integrations/wizard/save with source_type=file."""

    def test_create_file_to_file_passthrough(self, client):
        resp = client.post("/integrations/wizard/save", data={
            "name": "File Passthrough",
            "description": "Test integration",
            "enabled": "on",
            "source_type": "file",
            "source_file_input_dir": "/data/input",
            "source_file_pattern": "*.json",
            "transform_type": "passthrough",
            "dest_type": "file",
            "dest_file_output_dir": "/data/output",
            "dest_file_format": "json",
        })
        assert resp.status_code == 302
        assert "/integrations" in resp.headers["Location"]

        integrations = _get_integrations()
        assert len(integrations) == 1
        integ = integrations[0]
        assert integ["name"] == "File Passthrough"
        assert integ["description"] == "Test integration"
        assert integ["enabled"] is True
        assert integ["source"]["type"] == "file"
        assert integ["source"]["config"]["input_dir"] == "/data/input"
        assert integ["source"]["config"]["file_pattern"] == "*.json"
        assert integ["transform"]["type"] == "passthrough"
        assert integ["destination"]["type"] == "file"
        assert integ["destination"]["config"]["output_dir"] == "/data/output"

    def test_create_file_disabled(self, client):
        client.post("/integrations/wizard/save", data={
            "name": "Disabled Int",
            "source_type": "file",
            "source_file_input_dir": "/in",
            "transform_type": "passthrough",
            "dest_type": "file",
            "dest_file_output_dir": "/out",
        })
        integ = _get_integrations()[0]
        assert integ["enabled"] is False


# ---------------------------------------------------------------------------
# 3. Wizard save — upload source
# ---------------------------------------------------------------------------

class TestWizardSaveUploadSource:
    """POST with source_type=upload."""

    def test_create_upload_source(self, client):
        resp = client.post("/integrations/wizard/save", data={
            "name": "Upload Test",
            "enabled": "on",
            "source_type": "upload",
            "upload_format_json": "on",
            "upload_format_csv": "on",
            "transform_type": "passthrough",
            "dest_type": "file",
            "dest_file_output_dir": "/out",
            "dest_file_format": "xml",
        })
        assert resp.status_code == 302
        integ = _get_integrations()[0]
        assert integ["source"]["type"] == "upload"
        assert "json" in integ["source"]["config"]["allowed_formats"]
        assert "csv" in integ["source"]["config"]["allowed_formats"]

    def test_upload_default_format(self, client):
        """No format checkboxes → defaults to ['json']."""
        client.post("/integrations/wizard/save", data={
            "name": "Upload Default",
            "source_type": "upload",
            "transform_type": "passthrough",
            "dest_type": "file",
            "dest_file_output_dir": "/out",
        })
        integ = _get_integrations()[0]
        assert integ["source"]["config"]["allowed_formats"] == ["json"]


# ---------------------------------------------------------------------------
# 4. Wizard save — webhook source
# ---------------------------------------------------------------------------

class TestWizardSaveWebhookSource:
    """POST with source_type=webhook."""

    def test_create_webhook_source(self, client):
        resp = client.post("/integrations/wizard/save", data={
            "name": "Webhook Int",
            "enabled": "on",
            "source_type": "webhook",
            "webhook_require_signature": "on",
            "webhook_content_types": "application/json, application/xml",
            "transform_type": "passthrough",
            "dest_type": "file",
            "dest_file_output_dir": "/out",
        })
        assert resp.status_code == 302
        integ = _get_integrations()[0]
        src_cfg = integ["source"]["config"]
        assert integ["source"]["type"] == "webhook"
        assert src_cfg["webhook_path"]  # auto-generated
        assert src_cfg["webhook_secret"]  # auto-generated
        assert src_cfg["require_signature"] is True
        assert "application/json" in src_cfg["content_types"]
        assert "application/xml" in src_cfg["content_types"]


# ---------------------------------------------------------------------------
# 5. Wizard save — api_poll source
# ---------------------------------------------------------------------------

class TestWizardSaveApiPollSource:
    """POST with source_type=api_poll and various auth types."""

    def test_api_poll_no_auth(self, client):
        client.post("/integrations/wizard/save", data={
            "name": "API Poll",
            "source_type": "api_poll",
            "api_poll_url": "https://example.com/api/data",
            "api_poll_method": "GET",
            "api_poll_interval": "600",
            "api_poll_data_path": "results",
            "api_poll_auth_type": "none",
            "transform_type": "passthrough",
            "dest_type": "file",
            "dest_file_output_dir": "/out",
        })
        integ = _get_integrations()[0]
        assert integ["source"]["type"] == "api_poll"
        assert integ["source"]["config"]["url"] == "https://example.com/api/data"
        assert integ["source"]["config"]["poll_interval_seconds"] == 600

    def test_api_poll_bearer_auth(self, client):
        client.post("/integrations/wizard/save", data={
            "name": "API Bearer",
            "source_type": "api_poll",
            "api_poll_url": "https://api.example.com",
            "api_poll_method": "GET",
            "api_poll_interval": "300",
            "api_poll_auth_type": "bearer",
            "api_poll_bearer_token": "tok_123",
            "transform_type": "passthrough",
            "dest_type": "file",
            "dest_file_output_dir": "/out",
        })
        integ = _get_integrations()[0]
        auth = integ["source"]["config"].get("auth", {})
        assert auth["type"] == "bearer"
        assert auth["config"]["token"] == "tok_123"

    def test_api_poll_basic_auth(self, client):
        client.post("/integrations/wizard/save", data={
            "name": "API Basic",
            "source_type": "api_poll",
            "api_poll_url": "https://api.example.com",
            "api_poll_method": "POST",
            "api_poll_interval": "120",
            "api_poll_auth_type": "basic",
            "api_poll_basic_user": "user1",
            "api_poll_basic_pass": "pass1",
            "transform_type": "passthrough",
            "dest_type": "file",
            "dest_file_output_dir": "/out",
        })
        integ = _get_integrations()[0]
        auth = integ["source"]["config"]["auth"]
        assert auth["type"] == "basic"
        assert auth["config"]["username"] == "user1"

    def test_api_poll_apikey_auth(self, client):
        client.post("/integrations/wizard/save", data={
            "name": "API Key Auth",
            "source_type": "api_poll",
            "api_poll_url": "https://api.example.com",
            "api_poll_method": "GET",
            "api_poll_interval": "300",
            "api_poll_auth_type": "api_key",
            "api_poll_apikey_header": "X-Custom-Key",
            "api_poll_apikey_value": "secret123",
            "transform_type": "passthrough",
            "dest_type": "file",
            "dest_file_output_dir": "/out",
        })
        integ = _get_integrations()[0]
        auth = integ["source"]["config"]["auth"]
        assert auth["type"] == "api_key"
        assert auth["config"]["header"] == "X-Custom-Key"
        assert auth["config"]["value"] == "secret123"


# ---------------------------------------------------------------------------
# 6. Transform types
# ---------------------------------------------------------------------------

class TestWizardTransformTypes:
    """Test mapping and custom transform types."""

    def test_mapping_transform_redirects_to_editor(self, client):
        resp = client.post("/integrations/wizard/save", data={
            "name": "Mapping Int",
            "source_type": "file",
            "source_file_input_dir": "/in",
            "transform_type": "mapping",
            "dest_type": "file",
            "dest_file_output_dir": "/out",
        })
        assert resp.status_code == 302
        loc = resp.headers["Location"]
        assert "/mapping/editor/" in loc

        integ = _get_integrations()[0]
        assert integ["transform"]["type"] == "mapping"
        assert integ["transform"]["field_mappings"] == []

    def test_custom_transform(self, client):
        client.post("/integrations/wizard/save", data={
            "name": "Custom Int",
            "source_type": "file",
            "source_file_input_dir": "/in",
            "transform_type": "custom",
            "custom_handler": "my_handler_func",
            "dest_type": "file",
            "dest_file_output_dir": "/out",
        })
        integ = _get_integrations()[0]
        assert integ["transform"]["type"] == "custom"
        assert integ["transform"]["handler"] == "my_handler_func"


# ---------------------------------------------------------------------------
# 7. Destination — API
# ---------------------------------------------------------------------------

class TestWizardApiDestination:
    """Test api destination with various auth types."""

    def test_api_dest_bearer(self, client):
        client.post("/integrations/wizard/save", data={
            "name": "API Dest Bearer",
            "source_type": "file",
            "source_file_input_dir": "/in",
            "transform_type": "passthrough",
            "dest_type": "api",
            "dest_api_url": "https://target.example.com/ingest",
            "dest_api_method": "POST",
            "dest_api_content_type": "application/json",
            "dest_api_timeout": "60",
            "dest_api_retries": "3",
            "dest_api_auth_type": "bearer",
            "dest_api_bearer_token": "dest_tok_abc",
        })
        integ = _get_integrations()[0]
        dest_cfg = integ["destination"]["config"]
        assert dest_cfg["url"] == "https://target.example.com/ingest"
        assert dest_cfg["method"] == "POST"
        assert dest_cfg["timeout_seconds"] == 60
        assert dest_cfg["retry_count"] == 3
        auth = dest_cfg["auth"]
        assert auth["type"] == "bearer"
        assert auth["config"]["token"] == "dest_tok_abc"

    def test_api_dest_basic(self, client):
        client.post("/integrations/wizard/save", data={
            "name": "API Dest Basic",
            "source_type": "file",
            "source_file_input_dir": "/in",
            "transform_type": "passthrough",
            "dest_type": "api",
            "dest_api_url": "https://target.example.com",
            "dest_api_method": "PUT",
            "dest_api_auth_type": "basic",
            "dest_api_basic_user": "u",
            "dest_api_basic_pass": "p",
        })
        integ = _get_integrations()[0]
        auth = integ["destination"]["config"]["auth"]
        assert auth["type"] == "basic"
        assert auth["config"]["username"] == "u"

    def test_api_dest_no_auth(self, client):
        client.post("/integrations/wizard/save", data={
            "name": "API Dest No Auth",
            "source_type": "file",
            "source_file_input_dir": "/in",
            "transform_type": "passthrough",
            "dest_type": "api",
            "dest_api_url": "https://open.example.com",
            "dest_api_method": "POST",
            "dest_api_auth_type": "none",
        })
        integ = _get_integrations()[0]
        assert "auth" not in integ["destination"]["config"]


# ---------------------------------------------------------------------------
# 8. Error handling fields
# ---------------------------------------------------------------------------

class TestWizardErrorHandling:
    """Error / archive dir propagation."""

    def test_error_and_archive_dirs(self, client):
        client.post("/integrations/wizard/save", data={
            "name": "With Errors",
            "source_type": "file",
            "source_file_input_dir": "/in",
            "transform_type": "passthrough",
            "dest_type": "file",
            "dest_file_output_dir": "/out",
            "dest_file_error_dir": "/errors",
            "dest_file_archive_dir": "/archive",
        })
        integ = _get_integrations()[0]
        assert integ["error_handling"]["error_dir"] == "/errors"
        assert integ["error_handling"]["archive_dir"] == "/archive"


# ---------------------------------------------------------------------------
# 9. Validation
# ---------------------------------------------------------------------------

class TestWizardValidation:
    """Validation errors return redirect to wizard."""

    def test_missing_name_redirects_back(self, client):
        resp = client.post("/integrations/wizard/save", data={
            "name": "",
            "source_type": "file",
            "transform_type": "passthrough",
            "dest_type": "file",
        })
        assert resp.status_code == 302
        assert "/integrations/new" in resp.headers["Location"]
        # No integration created
        assert len(_get_integrations()) == 0


# ---------------------------------------------------------------------------
# 10. i18n keys
# ---------------------------------------------------------------------------

class TestWizardI18n:
    """Verify wizard i18n keys exist in both languages."""

    REQUIRED_KEYS = [
        "wizard_title",
        "wizard_subtitle",
        "wizard_step_basic",
        "wizard_step_source",
        "wizard_step_transform",
        "wizard_step_destination",
        "wizard_step_review",
        "wizard_name",
        "wizard_description",
        "wizard_prev",
        "wizard_next",
        "wizard_cancel",
        "wizard_save",
        "wizard_saved",
        "wizard_err_name_required",
        "wizard_new_integration",
    ]

    def test_hebrew_keys(self):
        from app.i18n import TRANSLATIONS
        he = TRANSLATIONS["he"]
        for key in self.REQUIRED_KEYS:
            assert key in he, f"Missing Hebrew key: {key}"

    def test_english_keys(self):
        from app.i18n import TRANSLATIONS
        en = TRANSLATIONS["en"]
        for key in self.REQUIRED_KEYS:
            assert key in en, f"Missing English key: {key}"


# ---------------------------------------------------------------------------
# 11. Integrations page has wizard link
# ---------------------------------------------------------------------------

class TestIntegrationsPageWizardLink:
    """The integrations page should have a link to the wizard."""

    def test_wizard_link_on_integrations_page(self, client):
        resp = client.get("/integrations")
        assert resp.status_code == 200
        assert b"/integrations/new" in resp.data
