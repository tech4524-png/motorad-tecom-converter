"""
Declarative transform engine.

Reads a JSON transform config (field_mappings, lookup_tables, constants,
conditions) and applies it to input data to produce output data.

This is the heart of user-created integrations: instead of writing Python
code, the user defines mappings in JSON (assisted by AI in Phase 3) and
this engine executes them.

For Phase 1, supports:
  - Direct field mapping (source_path → target_path)
  - Format conversions (dates, numbers)
  - Lookup tables (e.g. country codes)
  - Default/constant values
  - Simple conditions (if source field empty, use fallback)
  - Array/line-item iteration (source[].field → target[].field)

The transform config shape matches ARCHITECTURE.md §3.2:
{
    "type": "mapping",
    "field_mappings": [
        {"source_path": "...", "target_path": "...", "transform": null},
        {"source_path": "...", "target_path": "...", "transform": "date_format(...)"},
    ],
    "lookup_tables": {"table_name": {"KEY": "VALUE", ...}},
    "constants": {"target_path": "fixed_value"},
    "defaults": {"target_path": "fallback_value"}
}
"""
import logging
import re
from copy import deepcopy
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional

logger = logging.getLogger("motorad.core.transform")


class TransformError(ValueError):
    """Raised when a mapping rule cannot be applied."""
    pass


# ---------------------------------------------------------------------------
# Path utilities — navigate nested dicts/lists with dot notation
# ---------------------------------------------------------------------------

def _get_by_path(data: Any, path: str) -> Any:
    """Retrieve a value from nested data using dot-path notation.

    Supports:
      "header.invoice_number"       → data["header"]["invoice_number"]
      "lines[].unit_price"          → [row["unit_price"] for row in data["lines"]]
      "lines[0].unit_price"         → data["lines"][0]["unit_price"]

    Returns None for missing paths instead of raising.
    """
    if not path:
        return data

    parts = _parse_path(path)
    return _resolve_parts(data, parts)


def _parse_path(path: str) -> list:
    """Split a dot-path into navigation segments.

    "header.lines[].unit_price" → ["header", "lines", "[]", "unit_price"]
    "header.lines[0].name"     → ["header", "lines", "[0]", "name"]
    """
    segments = []
    for part in path.split("."):
        # Handle array notation: "lines[]" or "lines[0]"
        m = re.match(r"^(\w+)(\[(\d*)\])$", part)
        if m:
            segments.append(m.group(1))
            idx = m.group(3)
            segments.append(f"[{idx}]" if idx else "[]")
        else:
            segments.append(part)
    return segments


def _resolve_parts(data: Any, parts: list) -> Any:
    """Walk the data structure following parsed path segments."""
    current = data
    i = 0
    while i < len(parts):
        part = parts[i]

        if part == "[]":
            # Iterate: apply remaining path to each element
            if not isinstance(current, list):
                return None
            remaining = parts[i + 1:]
            if not remaining:
                return current
            return [_resolve_parts(item, remaining) for item in current]

        elif part.startswith("[") and part.endswith("]"):
            # Indexed access
            idx = int(part[1:-1])
            if not isinstance(current, list) or idx >= len(current):
                return None
            current = current[idx]

        else:
            # Dict key access
            if isinstance(current, dict):
                current = current.get(part)
            elif isinstance(current, list):
                # If we hit a list without [], map over it
                return [_resolve_parts(item, parts[i:]) for item in current]
            else:
                return None

            if current is None:
                return None

        i += 1

    return current


def _set_by_path(data: dict, path: str, value: Any):
    """Set a value in a nested dict, creating intermediate dicts/lists as needed.

    "header.invoice_number" sets data["header"]["invoice_number"] = value
    """
    parts = path.split(".")
    current = data
    for i, part in enumerate(parts[:-1]):
        # Handle array notation in target path
        m = re.match(r"^(\w+)\[(\d*)\]$", part)
        if m:
            key, idx_str = m.group(1), m.group(2)
            if key not in current:
                current[key] = []
            lst = current[key]
            if idx_str:
                idx = int(idx_str)
                while len(lst) <= idx:
                    lst.append({})
                current = lst[idx]
            else:
                # Will be handled at the row level
                current = current.setdefault(key, {})
        else:
            if part not in current or not isinstance(current[part], dict):
                current[part] = {}
            current = current[part]

    # Set the leaf
    leaf = parts[-1]
    m = re.match(r"^(\w+)\[(\d*)\]$", leaf)
    if m:
        key = m.group(1)
        current[key] = value
    else:
        current[leaf] = value


# ---------------------------------------------------------------------------
# Transform functions — applied per-field
# ---------------------------------------------------------------------------

# date_format('DD/MM/YYYY', 'YYYY-MM-DD')
_DATE_FORMAT_RE = re.compile(
    r"date_format\(\s*'([^']+)'\s*,\s*'([^']+)'\s*\)"
)

# lookup('table_name')
_LOOKUP_RE = re.compile(r"lookup\(\s*'(\w+)'\s*\)")

# Python date format mapping from common user-friendly tokens
_DATE_TOKENS = {
    "YYYY": "%Y", "YY": "%y",
    "MM": "%m", "DD": "%d",
    "HH": "%H", "mm": "%M", "ss": "%S",
}


def _convert_date_format(token_format: str) -> str:
    """Convert user-friendly date format to Python strftime/strptime format."""
    result = token_format
    # Sort by length descending so YYYY is replaced before YY
    for token, fmt in sorted(_DATE_TOKENS.items(), key=lambda x: -len(x[0])):
        result = result.replace(token, fmt)
    return result


def apply_field_transform(value: Any, transform_expr: str,
                          lookup_tables: dict) -> Any:
    """Apply a single transform expression to a value.

    Supported transforms:
      null / empty         → pass through
      "date_format(...)"   → reformat date
      "lookup('table')"    → lookup in table
      "upper"              → uppercase
      "lower"              → lowercase
      "trim"               → strip whitespace
      "to_decimal"         → convert to 2-decimal string
      "calculate"          → (reserved for Phase 3 expressions)
    """
    if not transform_expr:
        return value

    transform_expr = transform_expr.strip()

    # date_format('input_fmt', 'output_fmt')
    m = _DATE_FORMAT_RE.match(transform_expr)
    if m:
        in_fmt = _convert_date_format(m.group(1))
        out_fmt = _convert_date_format(m.group(2))
        try:
            dt = datetime.strptime(str(value), in_fmt)
            return dt.strftime(out_fmt)
        except (ValueError, TypeError) as exc:
            raise TransformError(f"Date conversion failed for {value!r}: {exc}")

    # lookup('table_name')
    m = _LOOKUP_RE.match(transform_expr)
    if m:
        table_name = m.group(1)
        table = lookup_tables.get(table_name, {})
        key = str(value).strip().upper()
        result = table.get(key)
        if result is None:
            raise TransformError(
                f"Lookup failed: {value!r} not found in table '{table_name}'"
            )
        return result

    # Simple transforms
    if transform_expr == "upper":
        return str(value).upper() if value else value
    if transform_expr == "lower":
        return str(value).lower() if value else value
    if transform_expr == "trim":
        return str(value).strip() if value else value
    if transform_expr == "to_decimal":
        try:
            return str(Decimal(str(value)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            ))
        except Exception as exc:
            raise TransformError(f"Decimal conversion failed for {value!r}: {exc}")

    raise TransformError(f"Unknown transform expression: {transform_expr!r}")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def apply_mappings(data: Any, transform_config: dict,
                   app_config: dict) -> dict:
    """Apply a full declarative transform config to input data.

    Args:
        data: Input data (list of dicts for tabular, or nested dict)
        transform_config: The "transform" section of an integration config
        app_config: The global app config (for lookup tables etc.)

    Returns:
        A dict representing the transformed output, structured according
        to the target_path definitions in the field mappings.
    """
    field_mappings = transform_config.get("field_mappings", [])
    lookup_tables = transform_config.get("lookup_tables", {})
    constants = transform_config.get("constants", {})
    defaults = transform_config.get("defaults", {})

    output = {}

    # 1. Apply constants first (they don't depend on source data)
    for target_path, value in constants.items():
        _set_by_path(output, target_path, value)

    # 2. Determine if we're dealing with row-based (tabular) or nested data
    if isinstance(data, list):
        # Tabular: data is a list of row-dicts
        # Separate header-level and line-level mappings
        header_mappings = []
        line_mappings = []

        for mapping in field_mappings:
            src = mapping.get("source_path", "")
            tgt = mapping.get("target_path", "")
            if "[]" in src or "[]" in tgt:
                line_mappings.append(mapping)
            else:
                header_mappings.append(mapping)

        # Apply header mappings from first row
        if data:
            first_row = data[0]
            for mapping in header_mappings:
                _apply_one_mapping(first_row, output, mapping,
                                   lookup_tables, defaults)

        # Apply line mappings across all rows
        if line_mappings and data:
            _apply_line_mappings(data, output, line_mappings,
                                lookup_tables, defaults)

    elif isinstance(data, dict):
        # Nested dict: navigate by path
        for mapping in field_mappings:
            _apply_one_mapping(data, output, mapping, lookup_tables, defaults)

    else:
        raise TransformError(f"Unsupported input data type: {type(data)}")

    # 3. Apply defaults (only where output value is still missing/None)
    for target_path, default_value in defaults.items():
        existing = _get_by_path(output, target_path)
        if existing is None:
            _set_by_path(output, target_path, default_value)

    return output


def _apply_one_mapping(source_data: dict, output: dict, mapping: dict,
                       lookup_tables: dict, defaults: dict):
    """Apply a single field mapping from source to output."""
    source_path = mapping.get("source_path", "")
    target_path = mapping.get("target_path", "")
    transform_expr = mapping.get("transform")

    if not source_path or not target_path:
        return

    value = _get_by_path(source_data, source_path)

    # Apply default if value is missing
    if value is None or (isinstance(value, str) and not value.strip()):
        default = defaults.get(target_path)
        if default is not None:
            value = default
        elif mapping.get("required", False):
            raise TransformError(
                f"Required field missing: {source_path}"
            )
        else:
            return  # Skip optional empty fields

    # Apply transform
    if transform_expr:
        value = apply_field_transform(value, transform_expr, lookup_tables)

    _set_by_path(output, target_path, value)


def _apply_line_mappings(rows: list, output: dict, line_mappings: list,
                         lookup_tables: dict, defaults: dict):
    """Apply line-level (array) mappings across all data rows.

    For each row, creates an output line item by applying each line mapping.
    The target array path is derived from the first "[]" in target_path.
    """
    if not line_mappings:
        return

    # Determine the target array path (e.g. "lines" from "lines[].field")
    first_target = line_mappings[0].get("target_path", "")
    array_match = re.match(r"^([\w.]+)\[\]\.(.+)$", first_target)
    if not array_match:
        return
    array_path = array_match.group(1)

    output_lines = []
    for row in rows:
        line_output = {}
        for mapping in line_mappings:
            src = mapping.get("source_path", "")
            tgt = mapping.get("target_path", "")
            transform_expr = mapping.get("transform")

            # Strip array notation from paths for per-row processing
            src_field = re.sub(r"^[\w.]+\[\]\.", "", src)
            tgt_field = re.sub(r"^[\w.]+\[\]\.", "", tgt)

            value = _get_by_path(row, src_field)

            if value is None or (isinstance(value, str) and not value.strip()):
                default = defaults.get(tgt)
                if default is not None:
                    value = default
                elif mapping.get("required", False):
                    raise TransformError(f"Required field missing in line: {src_field}")
                else:
                    continue

            if transform_expr:
                value = apply_field_transform(value, transform_expr, lookup_tables)

            _set_by_path(line_output, tgt_field, value)

        if line_output:
            output_lines.append(line_output)

    _set_by_path(output, array_path, output_lines)
