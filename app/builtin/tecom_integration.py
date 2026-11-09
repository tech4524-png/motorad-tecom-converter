"""
Builtin TECOM Invoice integration.

Wraps the existing tecom_xml.build_invoice_xml and xls_reader as a
declarative integration config with a "custom" transform type. The actual
conversion code is untouched — this module simply registers it with the
new integration framework so engine.py can treat it like any other
integration.

The integration config is generated dynamically from the live app config
(seller info, banking, etc.) so it always reflects the current settings.
"""
import logging
from pathlib import Path
from typing import Any

from ..core.integration import (
    make_integration_config,
    register_source,
    register_destination,
    register_custom_transform,
)
from ..core.source_manager import read_file_source
from ..core.dest_manager import write_file_destination

logger = logging.getLogger("motorad.builtin.tecom")

# Stable integration ID — used as key for custom transform registration
TECOM_INTEGRATION_ID = "builtin-tecom"


# ---------------------------------------------------------------------------
# Custom transform wrapper
# ---------------------------------------------------------------------------

def _tecom_custom_transform(data: Any, app_config: dict) -> bytes:
    """Delegate to the existing tecom_xml builder.

    The existing build_invoice_xml expects (invoice_number, rows, cfg).
    For the custom transform path, data arrives as (key, rows) tuple
    from engine.py's grouping step, so this wrapper bridges the interface.

    When called from IntegrationRunner.transform(), data is the raw
    parsed rows (list of dicts) — the grouping and key extraction happen
    in engine.py before calling transform.
    """
    from ..tecom_xml import build_invoice_xml
    from ..xls_reader import group_by_invoice

    # data is a list of row-dicts from the source reader
    groups = group_by_invoice(data)
    if len(groups) != 1:
        raise ValueError(
            f"Expected exactly one invoice per file, found {len(groups)}: "
            f"{[g[0] for g in groups]}"
        )
    key, rows = groups[0]
    return build_invoice_xml(key, rows, app_config)


# ---------------------------------------------------------------------------
# Integration config factory
# ---------------------------------------------------------------------------

def make_tecom_config(profile: dict, app_config: dict) -> dict:
    """Build a TECOM integration config from an existing profile + app config.

    This is called when engine.py processes a "tecom" profile, translating
    the legacy profile dict into the new integration config format.

    Args:
        profile: The profile dict from config.json (id, input_dir, etc.)
        app_config: The full app config (seller, banking, etc.)

    Returns:
        A valid integration config dict.
    """
    return make_integration_config(
        name=profile.get("display_name", "TECOM Invoice"),
        integration_id=TECOM_INTEGRATION_ID,
        source_type="file",
        source_config={
            "format": "xls_tab_delimited",
            "encoding": "utf-8-sig",
        },
        transform_config={
            "type": "custom",
            "custom_id": TECOM_INTEGRATION_ID,
        },
        destination_type="file",
        destination_config={
            "format": "xml",
            "output_dir": profile.get("output_dir", ""),
        },
        enabled=profile.get("enabled", True),
        error_handling={
            "error_dir": profile.get("error_dir", ""),
            "archive_dir": profile.get("archive_dir", ""),
            "on_error": "move_to_error_dir",
        },
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register():
    """Register TECOM builtin handlers with the integration framework.

    Called once at startup (from app/builtin/__init__.py).
    Source and destination use the generic file handlers; only the
    transform is custom.
    """
    register_source("file", read_file_source)
    register_source("upload", read_file_source)
    register_destination("file", write_file_destination)
    register_custom_transform(TECOM_INTEGRATION_ID, _tecom_custom_transform)
    logger.info("Registered TECOM builtin integration")
