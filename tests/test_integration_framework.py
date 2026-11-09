"""
Tests for the Phase 1 integration framework.

Covers:
  1. Core integration model (config creation, validation)
  2. Transform engine (field mappings, lookups, transforms)
  3. Source manager (CSV/JSON reading, format detection)
  4. Destination manager (JSON/XML/CSV writing)
  5. Builtin registration (TECOM + OrdRsp custom transforms)
  6. Backward compatibility (engine.process_file unchanged behavior)

Run with: python -m pytest tests/test_integration_framework.py -v
"""
import csv
import json
import os
import sys
import tempfile
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Set config dir to temp so tests don't touch real config
_test_config_dir = tempfile.mkdtemp(prefix="motorad_test_")
os.environ["MOTORAD_CONFIG_DIR"] = _test_config_dir


# ---------------------------------------------------------------------------
# 1. Core integration model
# ---------------------------------------------------------------------------

class TestIntegrationModel:
    """Tests for core/integration.py config creation and validation."""

    def test_make_integration_config_valid(self):
        from app.core.integration import make_integration_config
        config = make_integration_config(
            name="Test Integration",
            source_type="file",
            source_config={"format": "csv"},
            transform_config={
                "type": "mapping",
                "field_mappings": [
                    {"source_path": "name", "target_path": "Name"},
                ],
            },
            destination_type="file",
            destination_config={"format": "json"},
        )
        assert config["name"] == "Test Integration"
        assert config["source"]["type"] == "file"
        assert config["destination"]["type"] == "file"
        assert config["transform"]["type"] == "mapping"
        assert config["id"].startswith("int-")

    def test_make_integration_config_invalid_source(self):
        from app.core.integration import make_integration_config
        with pytest.raises(ValueError, match="Unknown source type"):
            make_integration_config(
                name="Bad",
                source_type="ftp",
                source_config={},
                transform_config={"type": "mapping", "field_mappings": []},
                destination_type="file",
                destination_config={},
            )

    def test_validate_integration_config_missing_keys(self):
        from app.core.integration import validate_integration_config
        errors = validate_integration_config({})
        assert len(errors) >= 4  # missing id, name, source, transform, destination

    def test_validate_integration_config_valid(self):
        from app.core.integration import make_integration_config, validate_integration_config
        config = make_integration_config(
            name="Valid",
            source_type="file",
            source_config={},
            transform_config={
                "type": "mapping",
                "field_mappings": [{"source_path": "a", "target_path": "b"}],
            },
            destination_type="file",
            destination_config={},
        )
        errors = validate_integration_config(config)
        assert errors == []

    def test_validate_mapping_requires_field_mappings(self):
        from app.core.integration import validate_integration_config
        config = {
            "id": "test",
            "name": "Test",
            "source": {"type": "file", "config": {}},
            "transform": {"type": "mapping"},  # no field_mappings
            "destination": {"type": "file", "config": {}},
        }
        errors = validate_integration_config(config)
        assert any("field_mappings" in e for e in errors)

    def test_new_integration_id_uniqueness(self):
        from app.core.integration import new_integration_id
        ids = {new_integration_id() for _ in range(100)}
        assert len(ids) == 100  # all unique


# ---------------------------------------------------------------------------
# 2. Transform engine
# ---------------------------------------------------------------------------

class TestTransformEngine:
    """Tests for core/transform.py."""

    def test_simple_field_mapping(self):
        from app.core.transform import apply_mappings

        data = {"first_name": "David", "last_name": "Raz"}
        config = {
            "type": "mapping",
            "field_mappings": [
                {"source_path": "first_name", "target_path": "name.first"},
                {"source_path": "last_name", "target_path": "name.last"},
            ],
        }
        result = apply_mappings(data, config, {})
        assert result == {"name": {"first": "David", "last": "Raz"}}

    def test_constants(self):
        from app.core.transform import apply_mappings

        config = {
            "type": "mapping",
            "field_mappings": [],
            "constants": {"header.version": "1.0", "header.type": "invoice"},
        }
        result = apply_mappings({}, config, {})
        assert result["header"]["version"] == "1.0"
        assert result["header"]["type"] == "invoice"

    def test_defaults_applied_when_missing(self):
        from app.core.transform import apply_mappings

        data = {"name": "Test"}
        config = {
            "type": "mapping",
            "field_mappings": [
                {"source_path": "name", "target_path": "name"},
                {"source_path": "country", "target_path": "country"},
            ],
            "defaults": {"country": "IL"},
        }
        result = apply_mappings(data, config, {})
        assert result["name"] == "Test"
        assert result["country"] == "IL"

    def test_date_format_transform(self):
        from app.core.transform import apply_field_transform

        result = apply_field_transform(
            "25/12/2026", "date_format('DD/MM/YYYY', 'YYYY-MM-DD')", {}
        )
        assert result == "2026-12-25"

    def test_lookup_transform(self):
        from app.core.transform import apply_field_transform

        tables = {"countries": {"IL": "Israel", "US": "United States"}}
        result = apply_field_transform("IL", "lookup('countries')", tables)
        assert result == "Israel"

    def test_lookup_not_found_raises(self):
        from app.core.transform import apply_field_transform, TransformError

        tables = {"countries": {"IL": "Israel"}}
        with pytest.raises(TransformError, match="not found"):
            apply_field_transform("XX", "lookup('countries')", tables)

    def test_upper_transform(self):
        from app.core.transform import apply_field_transform
        assert apply_field_transform("hello", "upper", {}) == "HELLO"

    def test_lower_transform(self):
        from app.core.transform import apply_field_transform
        assert apply_field_transform("HELLO", "lower", {}) == "hello"

    def test_trim_transform(self):
        from app.core.transform import apply_field_transform
        assert apply_field_transform("  hello  ", "trim", {}) == "hello"

    def test_to_decimal_transform(self):
        from app.core.transform import apply_field_transform
        assert apply_field_transform("10.5", "to_decimal", {}) == "10.50"
        assert apply_field_transform("3.14159", "to_decimal", {}) == "3.14"

    def test_tabular_data_with_line_mappings(self):
        from app.core.transform import apply_mappings

        data = [
            {"item": "Widget", "qty": "10", "price": "5.00"},
            {"item": "Gadget", "qty": "3", "price": "12.50"},
        ]
        config = {
            "type": "mapping",
            "field_mappings": [
                {"source_path": "lines[].item", "target_path": "lines[].name"},
                {"source_path": "lines[].qty", "target_path": "lines[].quantity"},
                {"source_path": "lines[].price", "target_path": "lines[].unit_price"},
            ],
        }
        result = apply_mappings(data, config, {})
        assert len(result["lines"]) == 2
        assert result["lines"][0]["name"] == "Widget"
        assert result["lines"][1]["quantity"] == "3"

    def test_path_navigation_nested(self):
        from app.core.transform import _get_by_path

        data = {"a": {"b": {"c": 42}}}
        assert _get_by_path(data, "a.b.c") == 42

    def test_path_navigation_missing(self):
        from app.core.transform import _get_by_path

        data = {"a": {"b": 1}}
        assert _get_by_path(data, "a.c.d") is None

    def test_unknown_transform_raises(self):
        from app.core.transform import apply_field_transform, TransformError

        with pytest.raises(TransformError, match="Unknown transform"):
            apply_field_transform("val", "nonexistent_fn()", {})


# ---------------------------------------------------------------------------
# 3. Source manager
# ---------------------------------------------------------------------------

class TestSourceManager:
    """Tests for core/source_manager.py."""

    def test_read_csv(self, tmp_path):
        from app.core.source_manager import read_file_source

        csv_file = tmp_path / "test.csv"
        csv_file.write_text("name,age\nAlice,30\nBob,25\n", encoding="utf-8")

        result = read_file_source(csv_file, {})
        assert len(result) == 2
        assert result[0]["name"] == "Alice"
        assert result[1]["age"] == "25"

    def test_read_json(self, tmp_path):
        from app.core.source_manager import read_file_source

        json_file = tmp_path / "test.json"
        json_file.write_text('{"items": [1, 2, 3]}', encoding="utf-8")

        result = read_file_source(json_file, {"format": "json"})
        assert result == {"items": [1, 2, 3]}

    def test_read_tsv(self, tmp_path):
        from app.core.source_manager import read_file_source

        tsv_file = tmp_path / "test.tsv"
        tsv_file.write_text("col1\tcol2\nval1\tval2\n", encoding="utf-8")

        result = read_file_source(tsv_file, {})
        assert len(result) == 1
        assert result[0]["col1"] == "val1"

    def test_format_detection(self):
        from app.core.source_manager import _detect_format

        assert _detect_format(Path("data.csv")) == "csv"
        assert _detect_format(Path("data.json")) == "json"
        assert _detect_format(Path("data.xml")) == "xml"
        assert _detect_format(Path("data.xls")) == "xls_tab_delimited"
        assert _detect_format(Path("data.tsv")) == "tsv"

    def test_missing_file_raises(self, tmp_path):
        from app.core.source_manager import read_file_source, SourceError

        with pytest.raises(SourceError, match="not found"):
            read_file_source(tmp_path / "nonexistent.csv", {})

    def test_empty_csv_raises(self, tmp_path):
        from app.core.source_manager import read_file_source, SourceError

        empty_file = tmp_path / "empty.csv"
        empty_file.write_text("", encoding="utf-8")

        with pytest.raises(SourceError, match="empty"):
            read_file_source(empty_file, {})


# ---------------------------------------------------------------------------
# 4. Destination manager
# ---------------------------------------------------------------------------

class TestDestinationManager:
    """Tests for core/dest_manager.py."""

    def test_write_json(self, tmp_path):
        from app.core.dest_manager import write_file_destination

        data = {"name": "Test", "value": 42}
        path = write_file_destination(
            data, str(tmp_path), "out.json",
            {"format": "json"}
        )
        assert path.exists()
        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert loaded["name"] == "Test"

    def test_write_csv(self, tmp_path):
        from app.core.dest_manager import write_file_destination

        data = [
            {"name": "Alice", "age": 30},
            {"name": "Bob", "age": 25},
        ]
        path = write_file_destination(
            data, str(tmp_path), "out.csv",
            {"format": "csv"}
        )
        assert path.exists()
        with open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 2
        assert rows[0]["name"] == "Alice"

    def test_write_xml_from_bytes(self, tmp_path):
        from app.core.dest_manager import write_file_destination

        xml_bytes = b'<?xml version="1.0"?>\n<root><item>hello</item></root>'
        path = write_file_destination(
            xml_bytes, str(tmp_path), "out.xml",
            {"format": "xml"}
        )
        assert path.exists()
        content = path.read_bytes()
        assert b"<item>hello</item>" in content

    def test_write_xml_from_dict(self, tmp_path):
        from app.core.dest_manager import write_file_destination

        data = {"name": "Test", "value": "42"}
        path = write_file_destination(
            data, str(tmp_path), "out.xml",
            {"format": "xml", "root_element": "record"}
        )
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "<record>" in content
        assert "<name>Test</name>" in content

    def test_unique_path_no_collision(self, tmp_path):
        from app.core.dest_manager import _unique_path

        path = _unique_path(str(tmp_path), "test.json")
        assert path.name == "test.json"

    def test_unique_path_with_collision(self, tmp_path):
        from app.core.dest_manager import _unique_path

        (tmp_path / "test.json").write_text("{}")
        path = _unique_path(str(tmp_path), "test.json")
        assert path.name != "test.json"
        assert path.name.startswith("test_")

    def test_unsupported_format_raises(self, tmp_path):
        from app.core.dest_manager import write_file_destination, DestinationError

        with pytest.raises(DestinationError, match="Unsupported"):
            write_file_destination({}, str(tmp_path), "out.xyz", {"format": "xyz"})


# ---------------------------------------------------------------------------
# 5. Builtin registration
# ---------------------------------------------------------------------------

class TestBuiltinRegistration:
    """Tests for builtin integration registration."""

    def test_builtins_registered(self):
        from app.core.integration import (
            get_source_handler,
            get_destination_handler,
            get_custom_transform,
        )
        # engine.py imports trigger registration
        import app.engine  # noqa

        assert get_source_handler("file") is not None
        assert get_source_handler("upload") is not None
        assert get_destination_handler("file") is not None
        assert get_custom_transform("builtin-tecom") is not None
        assert get_custom_transform("builtin-ordrsp") is not None

    def test_tecom_config_factory(self):
        from app.builtin.tecom_integration import make_tecom_config

        profile = {
            "id": "tecom",
            "display_name": "TECOM Test",
            "output_dir": "/tmp/out",
            "error_dir": "/tmp/err",
            "archive_dir": "/tmp/arch",
            "enabled": True,
        }
        config = make_tecom_config(profile, {})
        assert config["id"] == "builtin-tecom"
        assert config["name"] == "TECOM Test"
        assert config["source"]["type"] == "file"
        assert config["transform"]["type"] == "custom"
        assert config["destination"]["type"] == "file"

    def test_ordrsp_config_factory(self):
        from app.builtin.ordrsp_integration import make_ordrsp_config

        profile = {
            "id": "ordrsp",
            "display_name": "OrdRsp Test",
            "output_dir": "/tmp/out",
            "error_dir": "/tmp/err",
            "archive_dir": "/tmp/arch",
        }
        config = make_ordrsp_config(profile, {})
        assert config["id"] == "builtin-ordrsp"
        assert config["transform"]["type"] == "custom"


# ---------------------------------------------------------------------------
# 6. Integration config CRUD (config.py)
# ---------------------------------------------------------------------------

class TestConfigIntegrations:
    """Tests for integration CRUD in config.py."""

    def test_add_and_get_integration(self):
        from app.core.integration import make_integration_config
        from app import config as config_module

        cfg = config_module.load_config()
        integ = make_integration_config(
            name="Test CRUD",
            source_type="file",
            source_config={},
            transform_config={
                "type": "mapping",
                "field_mappings": [{"source_path": "a", "target_path": "b"}],
            },
            destination_type="file",
            destination_config={},
        )
        ok, err = config_module.add_integration(cfg, integ)
        assert ok, err

        found = config_module.get_integration_by_id(cfg, integ["id"])
        assert found is not None
        assert found["name"] == "Test CRUD"

        # Cleanup
        config_module.delete_integration(cfg, integ["id"])

    def test_duplicate_id_rejected(self):
        from app.core.integration import make_integration_config
        from app import config as config_module

        cfg = config_module.load_config()
        integ = make_integration_config(
            name="Dup Test",
            integration_id="dup-test-123",
            source_type="file",
            source_config={},
            transform_config={
                "type": "mapping",
                "field_mappings": [{"source_path": "a", "target_path": "b"}],
            },
            destination_type="file",
            destination_config={},
        )
        config_module.add_integration(cfg, integ)

        ok, err = config_module.add_integration(cfg, integ)
        assert not ok
        assert "already exists" in err

        config_module.delete_integration(cfg, "dup-test-123")

    def test_toggle_integration(self):
        from app.core.integration import make_integration_config
        from app import config as config_module

        cfg = config_module.load_config()
        integ = make_integration_config(
            name="Toggle Test",
            source_type="file",
            source_config={},
            transform_config={
                "type": "mapping",
                "field_mappings": [{"source_path": "a", "target_path": "b"}],
            },
            destination_type="file",
            destination_config={},
            enabled=True,
        )
        config_module.add_integration(cfg, integ)

        ok, _ = config_module.toggle_integration(cfg, integ["id"], False)
        assert ok
        found = config_module.get_integration_by_id(cfg, integ["id"])
        assert found["enabled"] is False

        config_module.delete_integration(cfg, integ["id"])


# ---------------------------------------------------------------------------
# 7. End-to-end: declarative mapping integration
# ---------------------------------------------------------------------------

class TestEndToEndMapping:
    """End-to-end test: CSV file → declarative mapping → JSON output."""

    def test_csv_to_json_mapping(self, tmp_path):
        from app.core.integration import IntegrationRunner, make_integration_config
        from app.builtin import register_all

        # Ensure handlers are registered
        try:
            register_all()
        except Exception:
            pass

        # Create test CSV
        csv_file = tmp_path / "input.csv"
        csv_file.write_text(
            "first_name,last_name,country\n"
            "David,Raz,IL\n"
            "John,Doe,US\n",
            encoding="utf-8"
        )

        config = make_integration_config(
            name="CSV to JSON test",
            source_type="file",
            source_config={"format": "csv"},
            transform_config={
                "type": "mapping",
                "field_mappings": [
                    {"source_path": "lines[].first_name", "target_path": "people[].name"},
                    {"source_path": "lines[].country", "target_path": "people[].country",
                     "transform": "lookup('countries')"},
                ],
                "lookup_tables": {
                    "countries": {"IL": "Israel", "US": "United States"},
                },
                "constants": {
                    "header.format": "contacts",
                    "header.version": "1.0",
                },
            },
            destination_type="file",
            destination_config={"format": "json", "output_dir": str(tmp_path / "out")},
        )

        runner = IntegrationRunner(config, {})

        # Read
        data = runner.read_source(csv_file)
        assert len(data) == 2

        # Transform
        output = runner.transform(data)
        assert output["header"]["format"] == "contacts"
        assert len(output["people"]) == 2
        assert output["people"][0]["country"] == "Israel"

        # Write
        out_path = runner.write_destination(output, str(tmp_path / "out"), "contacts.json")
        assert out_path.exists()
        result = json.loads(out_path.read_text(encoding="utf-8"))
        assert result["people"][1]["country"] == "United States"


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True, scope="session")
def cleanup_test_config():
    yield
    shutil.rmtree(_test_config_dir, ignore_errors=True)
