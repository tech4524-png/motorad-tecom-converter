"""
Core Integration model and runner.

An Integration is a declarative JSON config that defines a complete data
pipeline: source → transform → destination. The IntegrationRunner reads
this config and orchestrates the flow.

Builtin integrations (TECOM, OrdRsp) use a "custom" transform type that
delegates to their existing Python builder functions, preserving full
backward compatibility. New user-created integrations use the declarative
"mapping" transform type handled by transform.py.

This module has no knowledge of the web UI, folder watching, or any
specific data format. It is called by engine.py (which remains the
entry point for watcher.py and tests).
"""
import json
import logging
import uuid
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("motorad.core.integration")


# ---------------------------------------------------------------------------
# Integration Config schema
# ---------------------------------------------------------------------------

INTEGRATION_SCHEMA_VERSION = 1

# Valid source/destination/transform types (extensible per phase)
SOURCE_TYPES = {"file", "upload"}               # Phase 1
DESTINATION_TYPES = {"file"}                    # Phase 1
TRANSFORM_TYPES = {"mapping", "custom"}         # "custom" = builtin code path


def new_integration_id() -> str:
    """Generate a short, human-friendly integration ID."""
    return f"int-{uuid.uuid4().hex[:8]}"


def make_integration_config(
    name: str,
    source_type: str,
    source_config: dict,
    transform_config: dict,
    destination_type: str,
    destination_config: dict,
    *,
    integration_id: Optional[str] = None,
    enabled: bool = True,
    error_handling: Optional[dict] = None,
) -> dict:
    """Create a validated integration config dict.

    This is the canonical shape described in ARCHITECTURE.md §4. Every
    integration — builtin or user-created — is stored in this format.
    """
    if source_type not in SOURCE_TYPES:
        raise ValueError(f"Unknown source type: {source_type!r}")
    if destination_type not in DESTINATION_TYPES:
        raise ValueError(f"Unknown destination type: {destination_type!r}")

    transform_type = transform_config.get("type", "mapping")
    if transform_type not in TRANSFORM_TYPES:
        raise ValueError(f"Unknown transform type: {transform_type!r}")

    return {
        "schema_version": INTEGRATION_SCHEMA_VERSION,
        "id": integration_id or new_integration_id(),
        "name": name,
        "enabled": enabled,
        "created_at": datetime.now().isoformat(timespec="seconds"),

        "source": {
            "type": source_type,
            "config": source_config,
        },

        "transform": transform_config,

        "destination": {
            "type": destination_type,
            "config": destination_config,
        },

        "error_handling": error_handling or {
            "error_dir": "",
            "archive_dir": "",
            "on_error": "move_to_error_dir",
        },
    }


def validate_integration_config(config: dict) -> List[str]:
    """Return a list of validation error strings (empty = valid)."""
    errors = []

    for key in ("id", "name", "source", "transform", "destination"):
        if key not in config:
            errors.append(f"Missing required key: {key}")

    if "source" in config:
        src = config["source"]
        if src.get("type") not in SOURCE_TYPES:
            errors.append(f"Invalid source type: {src.get('type')}")

    if "destination" in config:
        dst = config["destination"]
        if dst.get("type") not in DESTINATION_TYPES:
            errors.append(f"Invalid destination type: {dst.get('type')}")

    if "transform" in config:
        tx = config["transform"]
        tx_type = tx.get("type", "mapping")
        if tx_type not in TRANSFORM_TYPES:
            errors.append(f"Invalid transform type: {tx_type}")
        if tx_type == "mapping" and not tx.get("field_mappings"):
            errors.append("Transform type 'mapping' requires 'field_mappings'")

    return errors


# ---------------------------------------------------------------------------
# Processing result (shared between builtin and generic paths)
# ---------------------------------------------------------------------------

class ProcessingResult:
    """Outcome of processing one unit of data through an integration.

    Kept identical to the legacy engine.ProcessingResult so callers
    (watcher, tests, web UI) don't need to change.
    """
    def __init__(self, ok: bool, primary_id=None, secondary_id=None,
                 output_path=None, message=""):
        self.ok = ok
        # Generic names; for TECOM these map to invoice_number / customer_name
        self.invoice_number = primary_id
        self.customer_name = secondary_id
        self.output_path = output_path
        self.message = message


# ---------------------------------------------------------------------------
# Source / Destination registry
# ---------------------------------------------------------------------------

# Each entry is a callable: fn(source_config, integration_config) -> data
_source_registry: Dict[str, Callable] = {}
_destination_registry: Dict[str, Callable] = {}


def register_source(source_type: str, handler: Callable):
    _source_registry[source_type] = handler


def register_destination(dest_type: str, handler: Callable):
    _destination_registry[dest_type] = handler


def get_source_handler(source_type: str) -> Optional[Callable]:
    return _source_registry.get(source_type)


def get_destination_handler(dest_type: str) -> Optional[Callable]:
    return _destination_registry.get(dest_type)


# ---------------------------------------------------------------------------
# Custom transform registry (for builtin integrations)
# ---------------------------------------------------------------------------

# Maps integration_id -> callable(key, rows, cfg) -> bytes
_custom_transforms: Dict[str, Callable] = {}


def register_custom_transform(integration_id: str, handler: Callable):
    """Register a Python function as the transform for a builtin integration.

    The handler signature must match the existing builder pattern:
        handler(key: str, rows: list[dict], cfg: dict) -> bytes
    """
    _custom_transforms[integration_id] = handler


def get_custom_transform(integration_id: str) -> Optional[Callable]:
    return _custom_transforms.get(integration_id)


# ---------------------------------------------------------------------------
# Integration Runner
# ---------------------------------------------------------------------------

class IntegrationRunner:
    """Executes one integration config against a single input.

    For Phase 1, this handles:
    - file source (read rows from a file)
    - custom transform (delegate to builtin Python code) OR
      mapping transform (declarative field mappings from transform.py)
    - file destination (write output to a file)

    The runner does NOT handle folder watching, archiving, or error routing —
    that stays in engine.py so existing behavior is preserved exactly.
    """

    def __init__(self, integration_config: dict, app_config: dict):
        self.config = integration_config
        self.app_config = app_config

    @property
    def integration_id(self) -> str:
        return self.config["id"]

    @property
    def source_type(self) -> str:
        return self.config["source"]["type"]

    @property
    def destination_type(self) -> str:
        return self.config["destination"]["type"]

    @property
    def transform_type(self) -> str:
        return self.config["transform"].get("type", "mapping")

    def read_source(self, source_path: Path) -> Any:
        """Read data from source. Returns format-dependent data structure.

        For file sources this returns a list of dicts (rows).
        """
        handler = get_source_handler(self.source_type)
        if handler is None:
            raise ValueError(f"No handler registered for source type: {self.source_type}")
        return handler(source_path, self.config["source"]["config"])

    def transform(self, data: Any, source_path: Path = None) -> Any:
        """Apply transform to data. Returns transformed output.

        For 'custom' type: delegates to registered Python function.
        For 'mapping' type: applies declarative field mappings.
        """
        if self.transform_type == "custom":
            handler = get_custom_transform(self.integration_id)
            if handler is None:
                raise ValueError(
                    f"No custom transform registered for integration: {self.integration_id}"
                )
            # Custom transforms get the raw data + app config (same signature
            # as the existing build_invoice_xml / build_ordrsp_xml)
            return handler(data, self.app_config)

        elif self.transform_type == "mapping":
            from .transform import apply_mappings
            return apply_mappings(data, self.config["transform"], self.app_config)

        else:
            raise ValueError(f"Unknown transform type: {self.transform_type}")

    def write_destination(self, output_data: Any, output_dir: str,
                          output_name: str) -> Path:
        """Write transformed data to destination. Returns output path."""
        handler = get_destination_handler(self.destination_type)
        if handler is None:
            raise ValueError(
                f"No handler registered for destination type: {self.destination_type}"
            )
        return handler(output_data, output_dir, output_name,
                       self.config["destination"]["config"])
