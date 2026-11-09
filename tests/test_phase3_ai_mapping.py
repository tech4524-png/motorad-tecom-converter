"""
Tests for Phase 3: Schema parser, AI mapper, and mapping web routes.

Run with: python -m pytest tests/test_phase3_ai_mapping.py -v
"""
import json
import io
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Set config dir to temp so tests don't touch real config
_test_config_dir = tempfile.mkdtemp(prefix="motorad_test_phase3_")
os.environ["MOTORAD_CONFIG_DIR"] = _test_config_dir


# ===========================================================================
# 1. Schema parser tests
# ===========================================================================

class TestSchemaParserJSON:
    """Tests for JSON schema extraction."""

    def test_flat_object(self):
        from app.core.schema_parser import extract_schema_from_data
        data = {"name": "Acme", "total": 100.5, "active": True}
        fields = extract_schema_from_data(data)
        paths = {f["path"] for f in fields}
        assert paths == {"name", "total", "active"}

    def test_nested_object(self):
        from app.core.schema_parser import extract_schema_from_data
        data = {"header": {"invoice_number": "INV-001", "date": "2026-01-15"}}
        fields = extract_schema_from_data(data)
        paths = {f["path"] for f in fields}
        assert "header.invoice_number" in paths
        assert "header.date" in paths

    def test_array_of_objects(self):
        from app.core.schema_parser import extract_schema_from_data
        data = {"lines": [{"item": "A", "qty": 2}, {"item": "B", "qty": 3}]}
        fields = extract_schema_from_data(data)
        paths = {f["path"] for f in fields}
        assert "lines[].item" in paths
        assert "lines[].qty" in paths

    def test_array_of_primitives(self):
        from app.core.schema_parser import extract_schema_from_data
        data = {"tags": ["red", "blue", "green"]}
        fields = extract_schema_from_data(data)
        paths = {f["path"] for f in fields}
        assert "tags[]" in paths

    def test_deeply_nested(self):
        from app.core.schema_parser import extract_schema_from_data
        data = {"a": {"b": {"c": {"d": "deep_value"}}}}
        fields = extract_schema_from_data(data)
        assert any(f["path"] == "a.b.c.d" for f in fields)

    def test_empty_dict(self):
        from app.core.schema_parser import extract_schema_from_data
        fields = extract_schema_from_data({})
        assert fields == []

    def test_root_list(self):
        from app.core.schema_parser import extract_schema_from_data
        data = [{"id": 1, "name": "x"}, {"id": 2, "name": "y"}]
        fields = extract_schema_from_data(data)
        paths = {f["path"] for f in fields}
        assert "[].id" in paths
        assert "[].name" in paths


class TestSchemaParserTypes:
    """Tests for type detection."""

    def test_type_string(self):
        from app.core.schema_parser import _detect_type
        assert _detect_type("hello") == "string"

    def test_type_integer(self):
        from app.core.schema_parser import _detect_type
        assert _detect_type(42) == "integer"

    def test_type_float(self):
        from app.core.schema_parser import _detect_type
        assert _detect_type(3.14) == "number"

    def test_type_boolean(self):
        from app.core.schema_parser import _detect_type
        assert _detect_type(True) == "boolean"

    def test_type_null(self):
        from app.core.schema_parser import _detect_type
        assert _detect_type(None) == "null"

    def test_type_date_iso(self):
        from app.core.schema_parser import _detect_type
        assert _detect_type("2026-01-15") == "date"

    def test_type_date_eu(self):
        from app.core.schema_parser import _detect_type
        assert _detect_type("15/01/2026") == "date"

    def test_type_numeric_string(self):
        from app.core.schema_parser import _detect_type
        assert _detect_type("123.45") == "number"

    def test_type_dict(self):
        from app.core.schema_parser import _detect_type
        assert _detect_type({"a": 1}) == "object"

    def test_type_list(self):
        from app.core.schema_parser import _detect_type
        assert _detect_type([1, 2]) == "array"


class TestSchemaParserFile:
    """Tests for file-based schema extraction."""

    def test_json_file(self):
        from app.core.schema_parser import extract_schema_from_file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"invoice": {"number": "INV-1", "amount": 100}}, f)
            f.flush()
            fields, err = extract_schema_from_file(f.name)
        os.unlink(f.name)
        assert err is None
        paths = {fld["path"] for fld in fields}
        assert "invoice.number" in paths
        assert "invoice.amount" in paths

    def test_csv_file(self):
        from app.core.schema_parser import extract_schema_from_file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
            f.write("item_code,description,qty,price\n")
            f.write("ABC-123,Widget,5,10.00\n")
            f.write("DEF-456,Gadget,2,20.00\n")
            f.flush()
            fields, err = extract_schema_from_file(f.name)
        os.unlink(f.name)
        assert err is None
        paths = {fld["path"] for fld in fields}
        assert "item_code" in paths
        assert "qty" in paths

    def test_missing_file(self):
        from app.core.schema_parser import extract_schema_from_file
        fields, err = extract_schema_from_file("/nonexistent/file.json")
        assert fields == []
        assert "not found" in err.lower()

    def test_unsupported_format(self):
        from app.core.schema_parser import extract_schema_from_file
        with tempfile.NamedTemporaryFile(suffix=".xyz", delete=False) as f:
            f.write(b"garbage")
            f.flush()
            fields, err = extract_schema_from_file(f.name)
        os.unlink(f.name)
        assert fields == []
        assert "Unsupported" in err


class TestSchemaParserRaw:
    """Tests for raw text schema extraction."""

    def test_raw_json(self):
        from app.core.schema_parser import extract_schema_from_raw
        raw = '{"customer": {"name": "Test", "id": 42}}'
        fields, err = extract_schema_from_raw(raw, "application/json")
        assert err is None
        paths = {f["path"] for f in fields}
        assert "customer.name" in paths
        assert "customer.id" in paths

    def test_raw_json_default_content_type(self):
        from app.core.schema_parser import extract_schema_from_raw
        raw = '{"x": 1}'
        fields, err = extract_schema_from_raw(raw, "")
        assert err is None
        assert len(fields) == 1

    def test_raw_invalid_json(self):
        from app.core.schema_parser import extract_schema_from_raw
        fields, err = extract_schema_from_raw("{bad json", "application/json")
        assert fields == []
        assert "Invalid JSON" in err

    def test_raw_csv(self):
        from app.core.schema_parser import extract_schema_from_raw
        raw = "name,age\nAlice,30\nBob,25"
        fields, err = extract_schema_from_raw(raw, "text/csv")
        assert err is None
        paths = {f["path"] for f in fields}
        assert "name" in paths
        assert "age" in paths

    def test_raw_empty_csv(self):
        from app.core.schema_parser import extract_schema_from_raw
        fields, err = extract_schema_from_raw("", "text/csv")
        assert "empty" in err.lower() or "error" in err.lower()

    def test_raw_unsupported_type(self):
        from app.core.schema_parser import extract_schema_from_raw
        fields, err = extract_schema_from_raw("data", "application/octet-stream")
        assert fields == []
        assert "Unsupported" in err


class TestSchemaParserXML:
    """Tests for XML schema extraction (requires lxml)."""

    def test_simple_xml(self):
        from app.core.schema_parser import extract_schema_from_raw
        xml = """<Invoice>
            <InvoiceNumber>INV-001</InvoiceNumber>
            <Date>2026-01-15</Date>
            <Total>500.00</Total>
        </Invoice>"""
        fields, err = extract_schema_from_raw(xml, "application/xml")
        assert err is None
        paths = {f["path"] for f in fields}
        assert "Invoice.InvoiceNumber" in paths
        assert "Invoice.Date" in paths
        assert "Invoice.Total" in paths

    def test_xml_with_attributes(self):
        from app.core.schema_parser import extract_schema_from_raw
        xml = '<Item code="ABC" qty="5">Widget</Item>'
        fields, err = extract_schema_from_raw(xml, "application/xml")
        assert err is None
        paths = {f["path"] for f in fields}
        assert "Item" in paths  # text content
        assert "Item@code" in paths
        assert "Item@qty" in paths

    def test_xml_repeated_elements(self):
        from app.core.schema_parser import extract_schema_from_raw
        xml = """<Order>
            <Line><Item>A</Item></Line>
            <Line><Item>B</Item></Line>
        </Order>"""
        fields, err = extract_schema_from_raw(xml, "text/xml")
        assert err is None
        paths = {f["path"] for f in fields}
        # Repeated <Line> → array notation
        assert any("Line[]" in p for p in paths)


# ===========================================================================
# 2. AI Mapper tests
# ===========================================================================

class TestAiMapperConfig:
    """Tests for AI configuration extraction."""

    def test_ai_disabled(self):
        from app.core.ai_mapper import get_ai_config
        cfg = {"ai": {"provider": "none", "api_key": "", "model": ""}}
        result = get_ai_config(cfg)
        assert result["enabled"] is False

    def test_ai_enabled_openai(self):
        from app.core.ai_mapper import get_ai_config
        cfg = {"ai": {"provider": "openai", "api_key": "sk-test", "model": "gpt-4o"}}
        result = get_ai_config(cfg)
        assert result["enabled"] is True
        assert result["provider"] == "openai"
        assert result["model"] == "gpt-4o"

    def test_ai_enabled_anthropic(self):
        from app.core.ai_mapper import get_ai_config
        cfg = {"ai": {"provider": "anthropic", "api_key": "sk-ant-test", "model": ""}}
        result = get_ai_config(cfg)
        assert result["enabled"] is True
        # model is empty string because get_ai_config uses cfg value as-is;
        # default is only used when key is missing entirely
        assert result["model"] == "" or result["model"] == "claude-sonnet-4-20250514"

    def test_ai_default_model_when_missing(self):
        from app.core.ai_mapper import get_ai_config
        cfg = {"ai": {"provider": "anthropic", "api_key": "sk-ant-test"}}
        result = get_ai_config(cfg)
        assert result["enabled"] is True
        assert result["model"] == "claude-sonnet-4-20250514"

    def test_ai_no_key(self):
        from app.core.ai_mapper import get_ai_config
        cfg = {"ai": {"provider": "openai", "api_key": "", "model": "gpt-4o"}}
        result = get_ai_config(cfg)
        assert result["enabled"] is False

    def test_ai_missing_section(self):
        from app.core.ai_mapper import get_ai_config
        cfg = {}
        result = get_ai_config(cfg)
        assert result["enabled"] is False
        assert result["provider"] == "none"


class TestAiMapperParsing:
    """Tests for LLM response parsing."""

    def test_parse_clean_json(self):
        from app.core.ai_mapper import _parse_mapping_response
        raw = json.dumps([{
            "source_path": "invoice.number",
            "target_path": "InvoiceNumber",
            "transform": None,
            "confidence": 0.95,
            "reasoning": "Direct match",
        }])
        mappings, err = _parse_mapping_response(raw)
        assert err is None
        assert len(mappings) == 1
        assert mappings[0]["source_path"] == "invoice.number"
        assert mappings[0]["confidence"] == 0.95

    def test_parse_markdown_fenced(self):
        from app.core.ai_mapper import _parse_mapping_response
        raw = '```json\n[{"source_path":"a","target_path":"b","confidence":0.8}]\n```'
        mappings, err = _parse_mapping_response(raw)
        assert err is None
        assert len(mappings) == 1

    def test_parse_strips_invalid_entries(self):
        from app.core.ai_mapper import _parse_mapping_response
        raw = json.dumps([
            {"source_path": "a", "target_path": "b", "confidence": 0.9},
            {"source_path": "", "target_path": "c"},  # missing source
            {"source_path": "d"},  # missing target
            "not a dict",
        ])
        mappings, err = _parse_mapping_response(raw)
        assert err is None
        assert len(mappings) == 1

    def test_parse_clamps_confidence(self):
        from app.core.ai_mapper import _parse_mapping_response
        raw = json.dumps([
            {"source_path": "a", "target_path": "b", "confidence": 1.5},
            {"source_path": "c", "target_path": "d", "confidence": -0.3},
        ])
        mappings, _ = _parse_mapping_response(raw)
        assert mappings[0]["confidence"] == 1.0
        assert mappings[1]["confidence"] == 0.0

    def test_parse_not_array(self):
        from app.core.ai_mapper import _parse_mapping_response
        raw = '{"source_path": "a", "target_path": "b"}'
        mappings, err = _parse_mapping_response(raw)
        assert mappings == []
        assert "not a JSON array" in err

    def test_parse_invalid_json(self):
        from app.core.ai_mapper import _parse_mapping_response
        mappings, err = _parse_mapping_response("this is not json at all")
        assert mappings == []
        assert "Failed to parse" in err


class TestAiMapperSuggest:
    """Tests for suggest_mappings with mocked LLM calls."""

    def _make_config(self, provider="openai"):
        return {
            "ai": {
                "provider": provider,
                "api_key": "sk-test-key",
                "model": "gpt-4o" if provider == "openai" else "claude-sonnet-4-20250514",
            }
        }

    def _source_schema(self):
        return [
            {"path": "invoice_number", "type": "string", "sample": "INV-001"},
            {"path": "total_amount", "type": "number", "sample": 500.0},
        ]

    def _target_schema(self):
        return [
            {"path": "InvoiceNumber", "type": "string", "sample": ""},
            {"path": "TotalAmount", "type": "number", "sample": ""},
        ]

    def test_suggest_disabled_ai(self):
        from app.core.ai_mapper import suggest_mappings
        cfg = {"ai": {"provider": "none"}}
        mappings, err = suggest_mappings(self._source_schema(), self._target_schema(), cfg)
        assert mappings == []
        assert "not configured" in err

    @patch("app.core.ai_mapper._call_openai")
    def test_suggest_openai_success(self, mock_call):
        from app.core.ai_mapper import suggest_mappings
        mock_call.return_value = (json.dumps([
            {"source_path": "invoice_number", "target_path": "InvoiceNumber", "transform": None, "confidence": 0.95, "reasoning": "Name match"},
            {"source_path": "total_amount", "target_path": "TotalAmount", "transform": "to_decimal", "confidence": 0.9, "reasoning": "Amount field"},
        ]), None)

        mappings, err = suggest_mappings(self._source_schema(), self._target_schema(), self._make_config("openai"))
        assert err is None
        assert len(mappings) == 2
        assert mappings[0]["source_path"] == "invoice_number"
        mock_call.assert_called_once()

    @patch("app.core.ai_mapper._call_anthropic")
    def test_suggest_anthropic_success(self, mock_call):
        from app.core.ai_mapper import suggest_mappings
        mock_call.return_value = (json.dumps([
            {"source_path": "invoice_number", "target_path": "InvoiceNumber", "confidence": 0.9},
        ]), None)

        mappings, err = suggest_mappings(self._source_schema(), self._target_schema(), self._make_config("anthropic"))
        assert err is None
        assert len(mappings) == 1
        mock_call.assert_called_once()

    @patch("app.core.ai_mapper._call_openai")
    def test_suggest_api_error(self, mock_call):
        from app.core.ai_mapper import suggest_mappings
        mock_call.return_value = ("", "OpenAI API error: rate limited")

        mappings, err = suggest_mappings(self._source_schema(), self._target_schema(), self._make_config("openai"))
        assert mappings == []
        assert "rate limited" in err

    @patch("app.core.ai_mapper._call_openai")
    def test_suggest_bad_response(self, mock_call):
        from app.core.ai_mapper import suggest_mappings
        mock_call.return_value = ("This is not JSON", None)

        mappings, err = suggest_mappings(self._source_schema(), self._target_schema(), self._make_config("openai"))
        assert mappings == []
        assert "Failed to parse" in err

    def test_suggest_unsupported_provider(self):
        from app.core.ai_mapper import suggest_mappings
        cfg = {"ai": {"provider": "google", "api_key": "key", "model": "gemini"}}
        # get_ai_config won't enable unknown providers
        mappings, err = suggest_mappings(self._source_schema(), self._target_schema(), cfg)
        assert mappings == []


class TestAiMapperTransformConfig:
    """Tests for mappings_to_transform_config."""

    def test_basic_conversion(self):
        from app.core.ai_mapper import mappings_to_transform_config
        mappings = [
            {"source_path": "a", "target_path": "b", "transform": None, "confidence": 0.9, "reasoning": "test"},
            {"source_path": "c", "target_path": "d", "transform": "upper", "confidence": 0.8, "reasoning": "test"},
        ]
        result = mappings_to_transform_config(mappings)
        assert result["type"] == "mapping"
        assert len(result["field_mappings"]) == 2
        assert result["field_mappings"][1]["transform"] == "upper"
        # AI metadata should be stripped
        assert "confidence" not in result["field_mappings"][0]
        assert "reasoning" not in result["field_mappings"][0]
        assert result["lookup_tables"] == {}
        assert result["constants"] == {}
        assert result["defaults"] == {}


class TestAiMapperValidation:
    """Tests for validate_mapping."""

    def test_valid_mapping(self):
        from app.core.ai_mapper import validate_mapping
        source = [{"path": "a", "type": "string"}]
        target = [{"path": "b", "type": "string"}]
        warnings = validate_mapping({"source_path": "a", "target_path": "b"}, source, target)
        assert warnings == []

    def test_unknown_source(self):
        from app.core.ai_mapper import validate_mapping
        source = [{"path": "a", "type": "string"}]
        target = [{"path": "b", "type": "string"}]
        warnings = validate_mapping({"source_path": "x", "target_path": "b"}, source, target)
        assert any("Source" in w for w in warnings)

    def test_unknown_target(self):
        from app.core.ai_mapper import validate_mapping
        source = [{"path": "a", "type": "string"}]
        target = [{"path": "b", "type": "string"}]
        warnings = validate_mapping({"source_path": "a", "target_path": "z"}, source, target)
        assert any("Target" in w for w in warnings)


# ===========================================================================
# 3. Web route tests
# ===========================================================================

@pytest.fixture
def app_client():
    """Flask test client with a test integration pre-configured."""
    from app.webapp import create_app
    from app import config as cfg_mod

    app = create_app()
    app.config["TESTING"] = True

    # Inject a test integration into config
    test_cfg = cfg_mod.load_config()
    test_cfg["integrations"] = [{
        "id": "test-integ-1",
        "name": "Test Integration",
        "enabled": True,
        "source": {"type": "webhook", "webhook_path": "/wh/test", "content_types": ["application/json"]},
        "transform": {"type": "mapping", "field_mappings": [], "lookup_tables": {}, "constants": {}, "defaults": {}},
        "destination": {"type": "file", "output_dir": "/tmp/out"},
    }]
    test_cfg["ai"] = {"provider": "openai", "api_key": "sk-test", "model": "gpt-4o"}
    cfg_mod.save_config(test_cfg)

    with app.test_client() as client:
        # Login first
        client.post("/login", data={"username": "admin", "password": "Drr2029@@"})
        yield client


class TestMappingAnalyzeRoute:
    """Tests for POST /mapping/analyze."""

    def test_analyze_pasted_json(self, app_client):
        resp = app_client.post("/mapping/analyze", data={
            "side": "source",
            "pasted_data": '{"invoice": {"number": "INV-001", "amount": 100}}',
            "content_type": "application/json",
        })
        data = json.loads(resp.data)
        assert data["ok"] is True
        paths = {f["path"] for f in data["fields"]}
        assert "invoice.number" in paths
        assert "invoice.amount" in paths

    def test_analyze_uploaded_json(self, app_client):
        sample = json.dumps({"customer": "Acme", "total": 500})
        resp = app_client.post("/mapping/analyze", data={
            "side": "target",
            "sample_file": (io.BytesIO(sample.encode()), "sample.json"),
        }, content_type="multipart/form-data")
        data = json.loads(resp.data)
        assert data["ok"] is True
        paths = {f["path"] for f in data["fields"]}
        assert "customer" in paths

    def test_analyze_uploaded_csv(self, app_client):
        csv_data = "code,name,price\nA1,Widget,10\nA2,Gadget,20"
        resp = app_client.post("/mapping/analyze", data={
            "side": "source",
            "sample_file": (io.BytesIO(csv_data.encode()), "items.csv"),
        }, content_type="multipart/form-data")
        data = json.loads(resp.data)
        assert data["ok"] is True
        paths = {f["path"] for f in data["fields"]}
        assert "code" in paths
        assert "price" in paths

    def test_analyze_no_data(self, app_client):
        resp = app_client.post("/mapping/analyze", data={"side": "source"})
        data = json.loads(resp.data)
        # Should return error or empty fields
        assert not data.get("ok") or data.get("fields") == []


class TestMappingAiSuggestRoute:
    """Tests for POST /mapping/ai-suggest."""

    @patch("app.core.ai_mapper._call_openai")
    def test_suggest_success(self, mock_call, app_client):
        mock_call.return_value = (json.dumps([
            {"source_path": "num", "target_path": "Number", "confidence": 0.9, "reasoning": "test"},
        ]), None)

        resp = app_client.post("/mapping/ai-suggest",
            data=json.dumps({
                "source_schema": [{"path": "num", "type": "string", "sample": "1"}],
                "target_schema": [{"path": "Number", "type": "string", "sample": ""}],
            }),
            content_type="application/json",
        )
        data = json.loads(resp.data)
        assert data["ok"] is True
        assert len(data["mappings"]) == 1

    def test_suggest_missing_schemas(self, app_client):
        resp = app_client.post("/mapping/ai-suggest",
            data=json.dumps({"source_schema": [], "target_schema": []}),
            content_type="application/json",
        )
        data = json.loads(resp.data)
        assert data["ok"] is False

    @patch("app.core.ai_mapper._call_openai")
    def test_suggest_api_failure(self, mock_call, app_client):
        mock_call.return_value = ("", "API error: timeout")
        resp = app_client.post("/mapping/ai-suggest",
            data=json.dumps({
                "source_schema": [{"path": "a", "type": "string"}],
                "target_schema": [{"path": "b", "type": "string"}],
            }),
            content_type="application/json",
        )
        data = json.loads(resp.data)
        assert data["ok"] is False
        assert "timeout" in data["error"]


class TestMappingSaveRoute:
    """Tests for POST /mapping/save."""

    def test_save_mappings(self, app_client):
        resp = app_client.post("/mapping/save",
            data=json.dumps({
                "integration_id": "test-integ-1",
                "mappings": [
                    {"source_path": "a", "target_path": "b", "transform": None, "confidence": 0.9},
                    {"source_path": "c", "target_path": "d", "transform": "upper", "confidence": 0.8},
                ],
            }),
            content_type="application/json",
        )
        data = json.loads(resp.data)
        assert data["ok"] is True

        # Verify saved to config
        from app import config as cfg_mod
        cfg = cfg_mod.load_config()
        integ = cfg_mod.get_integration_by_id(cfg, "test-integ-1")
        assert len(integ["transform"]["field_mappings"]) == 2

    def test_save_missing_integration_id(self, app_client):
        resp = app_client.post("/mapping/save",
            data=json.dumps({"mappings": []}),
            content_type="application/json",
        )
        data = json.loads(resp.data)
        assert data["ok"] is False

    def test_save_unknown_integration(self, app_client):
        resp = app_client.post("/mapping/save",
            data=json.dumps({"integration_id": "nonexistent", "mappings": []}),
            content_type="application/json",
        )
        data = json.loads(resp.data)
        assert data["ok"] is False


class TestMappingEditorRoute:
    """Tests for GET /mapping/editor/<id>."""

    def test_editor_page_loads(self, app_client):
        resp = app_client.get("/mapping/editor/test-integ-1")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "mapping" in html.lower()

    def test_editor_unknown_integration(self, app_client):
        resp = app_client.get("/mapping/editor/nonexistent")
        # Should redirect (302) to integrations page
        assert resp.status_code == 302


class TestSettingsAI:
    """Tests for AI settings in settings page."""

    def test_settings_page_has_ai_section(self, app_client):
        resp = app_client.get("/settings")
        html = resp.data.decode()
        assert "ai_provider" in html

    def test_save_ai_settings(self, app_client):
        resp = app_client.post("/settings", data={
            "name1": "Test Co",
            "street1": "",
            "postal_code": "",
            "city": "",
            "country_code": "IL",
            "country_name": "Israel",
            "tax_registration_number": "",
            "contact_name": "",
            "contact_phone": "",
            "contact_email": "",
            "message_type": "Invoice",
            "message_function": "Original",
            "tax_treatment_code": "NetInvoiceLevel",
            "maker_code": "*",
            "bank_name": "",
            "bank_account": "",
            "bank_code_number": "",
            "iban": "",
            "swift_bic": "",
            "ai_provider": "anthropic",
            "ai_api_key": "sk-ant-new-key-123",
            "ai_model": "claude-sonnet-4-20250514",
        }, follow_redirects=True)
        assert resp.status_code == 200

        # Verify saved
        from app import config as cfg_mod
        cfg = cfg_mod.load_config()
        assert cfg["ai"]["provider"] == "anthropic"
        assert cfg["ai"]["api_key"] == "sk-ant-new-key-123"

    def test_save_ai_settings_masked_key_not_overwritten(self, app_client):
        """If user doesn't change key (sends ••••••••), keep existing."""
        from app import config as cfg_mod
        cfg = cfg_mod.load_config()
        cfg["ai"]["api_key"] = "original-secret-key"
        cfg_mod.save_config(cfg)

        app_client.post("/settings", data={
            "name1": "Test Co",
            "street1": "",
            "postal_code": "",
            "city": "",
            "country_code": "IL",
            "country_name": "Israel",
            "tax_registration_number": "",
            "contact_name": "",
            "contact_phone": "",
            "contact_email": "",
            "message_type": "Invoice",
            "message_function": "Original",
            "tax_treatment_code": "NetInvoiceLevel",
            "maker_code": "*",
            "bank_name": "",
            "bank_account": "",
            "bank_code_number": "",
            "iban": "",
            "swift_bic": "",
            "ai_provider": "openai",
            "ai_api_key": "••••••••",
            "ai_model": "gpt-4o",
        }, follow_redirects=True)

        cfg = cfg_mod.load_config()
        assert cfg["ai"]["api_key"] == "original-secret-key"
