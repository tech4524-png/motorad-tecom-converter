"""
Destination manager — writes transformed data to various output types.

Phase 1 supports:
  - "file": write to a local file in XML, JSON, or CSV format

Each destination handler receives (data, output_dir, output_name, dest_config)
and returns the Path of the written file.
"""
import csv
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("motorad.core.dest_manager")


class DestinationError(ValueError):
    """Raised when output cannot be written."""
    pass


# ---------------------------------------------------------------------------
# Output format writers
# ---------------------------------------------------------------------------

def _write_json(data: Any, output_path: Path, config: dict):
    """Write data as JSON."""
    indent = config.get("indent", 2)
    ensure_ascii = config.get("ensure_ascii", False)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=indent, ensure_ascii=ensure_ascii)


def _write_xml(data: Any, output_path: Path, config: dict):
    """Write data as XML.

    If data is bytes (already-built XML, e.g. from a custom transform),
    write directly. If data is a dict, convert to XML.
    """
    if isinstance(data, bytes):
        with open(output_path, "wb") as f:
            f.write(data)
        return

    # Dict → XML conversion
    try:
        from lxml import etree
    except ImportError:
        raise DestinationError("lxml is required for XML output")

    root_tag = config.get("root_element", "root")
    root = _dict_to_xml(data, root_tag)

    xml_bytes = etree.tostring(
        root,
        pretty_print=True,
        xml_declaration=True,
        encoding="UTF-8",
    )
    with open(output_path, "wb") as f:
        f.write(xml_bytes)


def _dict_to_xml(data: Any, tag: str):
    """Recursively convert a dict to lxml elements."""
    from lxml import etree

    element = etree.Element(tag)

    if isinstance(data, dict):
        for key, value in data.items():
            if key.startswith("@"):
                # Attribute
                element.set(key[1:], str(value))
            elif key == "#text":
                element.text = str(value)
            elif isinstance(value, list):
                for item in value:
                    child = _dict_to_xml(item, key)
                    element.append(child)
            elif isinstance(value, dict):
                child = _dict_to_xml(value, key)
                element.append(child)
            else:
                child = etree.SubElement(element, key)
                child.text = str(value) if value is not None else ""
    elif isinstance(data, list):
        for item in data:
            child = _dict_to_xml(item, "item")
            element.append(child)
    else:
        element.text = str(data) if data is not None else ""

    return element


def _write_csv(data: Any, output_path: Path, config: dict):
    """Write data as CSV.

    Expects data to be a list of dicts (rows) or a dict with an array field.
    """
    delimiter = config.get("delimiter", ",")
    encoding = config.get("encoding", "utf-8")

    # If data is a dict, find the first list field to use as rows
    rows = data
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                rows = value
                break
        else:
            # Single-record: wrap in list
            rows = [data]

    if not isinstance(rows, list) or not rows:
        raise DestinationError("No tabular data to write as CSV")

    # Flatten nested dicts for CSV (dot notation keys)
    flat_rows = [_flatten_dict(row) for row in rows]
    fieldnames = list(flat_rows[0].keys())

    with open(output_path, "w", encoding=encoding, newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=delimiter)
        writer.writeheader()
        writer.writerows(flat_rows)


def _flatten_dict(d: dict, parent_key: str = "", sep: str = ".") -> dict:
    """Flatten a nested dict into dot-notation keys."""
    items = []
    for k, v in d.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        if isinstance(v, dict):
            items.extend(_flatten_dict(v, new_key, sep).items())
        elif isinstance(v, list):
            items.append((new_key, json.dumps(v, ensure_ascii=False)))
        else:
            items.append((new_key, v))
    return dict(items)


# ---------------------------------------------------------------------------
# Format registry
# ---------------------------------------------------------------------------

FORMAT_WRITERS = {
    "json": _write_json,
    "xml": _write_xml,
    "csv": _write_csv,
}


# ---------------------------------------------------------------------------
# Destination handler (registered with integration.py)
# ---------------------------------------------------------------------------

def _unique_path(directory: str, filename: str) -> Path:
    """Return a unique output path, appending a timestamp if needed."""
    dest = Path(directory) / filename
    if not dest.exists():
        return dest
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem, suffix = Path(filename).stem, Path(filename).suffix
    return Path(directory) / f"{stem}_{stamp}{suffix}"


def write_file_destination(data: Any, output_dir: str, output_name: str,
                           dest_config: dict) -> Path:
    """Write transformed data to a file.

    dest_config may contain:
      - format: output format ("xml", "json", "csv")
      - encoding: file encoding
      - root_element: root XML element name (for XML output)
      - indent: JSON indentation
      - delimiter: CSV delimiter
    """
    out_dir = Path(dest_config.get("output_dir", output_dir))
    out_dir.mkdir(parents=True, exist_ok=True)

    fmt = dest_config.get("format", "json")
    writer = FORMAT_WRITERS.get(fmt)
    if writer is None:
        raise DestinationError(f"Unsupported output format: {fmt}")

    output_path = _unique_path(str(out_dir), output_name)

    logger.info("Writing output %s (format: %s)", output_path.name, fmt)
    writer(data, output_path, dest_config)
    return output_path
