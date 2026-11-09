"""
Builtin OrdRsp (Order Response) integration.

Wraps the existing ordrsp_xml.build_ordrsp_xml and ordrsp_reader as a
declarative integration config with a "custom" transform type. The actual
conversion code is untouched — this module simply registers it with the
new integration framework.

Completely independent from tecom_integration.py — same pattern, different
format, no shared state.
"""
import logging
from pathlib import Path
from typing import Any

from ..core.integration import (
    make_integration_config,
    register_custom_transform,
)

logger = logging.getLogger("motorad.builtin.ordrsp")

# Stable integration ID
ORDRSP_INTEGRATION_ID = "builtin-ordrsp"


# ---------------------------------------------------------------------------
# Custom transform wrapper
# ---------------------------------------------------------------------------

def _ordrsp_custom_transform(data: Any, app_config: dict) -> bytes:
    """Delegate to the existing ordrsp_xml builder.

    Same bridge pattern as tecom: data is raw rows, we group and build.
    """
    from ..ordrsp_xml import build_ordrsp_xml
    from ..ordrsp_reader import group_by_order

    groups = group_by_order(data)
    if len(groups) != 1:
        raise ValueError(
            f"Expected exactly one order response per file, found {len(groups)}: "
            f"{[g[0] for g in groups]}"
        )
    key, rows = groups[0]
    return build_ordrsp_xml(key, rows, app_config)


# ---------------------------------------------------------------------------
# Integration config factory
# ---------------------------------------------------------------------------

def make_ordrsp_config(profile: dict, app_config: dict) -> dict:
    """Build an OrdRsp integration config from an existing profile + app config."""
    return make_integration_config(
        name=profile.get("display_name", "OrdRsp Order Response"),
        integration_id=ORDRSP_INTEGRATION_ID,
        source_type="file",
        source_config={
            "format": "xls_tab_delimited",
            "encoding": "utf-8-sig",
        },
        transform_config={
            "type": "custom",
            "custom_id": ORDRSP_INTEGRATION_ID,
        },
        destination_type="file",
        destination_config={
            "format": "xml",
            "output_dir": profile.get("output_dir", ""),
        },
        enabled=profile.get("enabled", False),
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
    """Register OrdRsp builtin handlers with the integration framework.

    Source and destination handlers are already registered by tecom_integration
    (they share the same generic file handlers). Only the custom transform
    needs registering here.
    """
    register_custom_transform(ORDRSP_INTEGRATION_ID, _ordrsp_custom_transform)
    logger.info("Registered OrdRsp builtin integration")
