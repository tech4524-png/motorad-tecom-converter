"""
Source manager — reads data from various source types.

Phase 1 supports:
  - "file": read rows from CSV/TSV/JSON/XML files
  - "upload": same as file, but path comes from a manual upload

Each source handler receives (source_path, source_config) and returns
a data structure suitable for the transform engine:
  - Tabular files (CSV/TSV/XLS) → list of dicts (one per row)
  - JSON files → parsed JSON (dict or list)
  - XML files → parsed dict representation
"""
import csv
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("motorad.core.source_manager")


class SourceError(ValueError):
    """Raised when a source file cannot be read or parsed."""
    pass


# ---------------------------------------------------------------------------
# File format readers
# ---------------------------------------------------------------------------

def _read_csv(path: Path, delimiter: str = ",", encoding: str = "utf-8-sig") -> list:
    """Read a CSV/TSV file and return a list of dicts."""
    try:
        with open(path, "r", encoding=encoding, errors="replace", newline="") as f:
            reader = csv.reader(f, delimiter=delimiter)
            rows = list(reader)
    except Exception as exc:
        raise SourceError(f"Failed to read file {path.name}: {exc}")

    if not rows:
        raise SourceError(f"File is empty: {path.name}")

    header = [h.strip() for h in rows[0]]
    data_rows = rows[1:]

    if not data_rows:
        raise SourceError(f"File has a header but no data rows: {path.name}")

    result = []
    for i, raw in enumerate(data_rows, start=2):
        row = {}
        for idx, col in enumerate(header):
            row[col] = raw[idx].strip() if idx < len(raw) else ""
        result.append(row)

    return result


def _read_json(path: Path, encoding: str = "utf-8") -> Any:
    """Read a JSON file and return parsed data."""
    try:
        with open(path, "r", encoding=encoding) as f:
            return json.load(f)
    except json.JSONDecodeError as exc:
        raise SourceError(f"Invalid JSON in {path.name}: {exc}")
    except Exception as exc:
        raise SourceError(f"Failed to read file {path.name}: {exc}")


def _read_xml(path: Path, encoding: str = "utf-8") -> dict:
    """Read an XML file and convert to a dict representation.

    Uses a simple recursive conversion: each element becomes a dict with
    its children as keys. Repeated children become lists. Attributes are
    stored under @attr keys. Text content is stored under #text.
    """
    try:
        from lxml import etree
    except ImportError:
        raise SourceError("lxml is required for XML source files")

    try:
        tree = etree.parse(str(path))
        root = tree.getroot()
        return _xml_element_to_dict(root)
    except etree.XMLSyntaxError as exc:
        raise SourceError(f"Invalid XML in {path.name}: {exc}")
    except Exception as exc:
        raise SourceError(f"Failed to read file {path.name}: {exc}")


def _xml_element_to_dict(element) -> dict:
    """Recursively convert an lxml element to a dict."""
    # Strip namespace from tag
    tag = element.tag
    if "}" in tag:
        tag = tag.split("}", 1)[1]

    result = {}

    # Attributes
    for attr_name, attr_value in element.attrib.items():
        result[f"@{attr_name}"] = attr_value

    # Children
    children = list(element)
    if children:
        child_dict = {}
        for child in children:
            child_data = _xml_element_to_dict(child)
            child_tag = child.tag
            if "}" in child_tag:
                child_tag = child_tag.split("}", 1)[1]

            if child_tag in child_dict:
                # Convert to list if multiple children with same tag
                existing = child_dict[child_tag]
                if not isinstance(existing, list):
                    child_dict[child_tag] = [existing]
                child_dict[child_tag].append(child_data[child_tag])
            else:
                child_dict.update(child_data)

        result[tag] = child_dict
    else:
        # Leaf element: text content
        text = (element.text or "").strip()
        if result:  # has attributes
            result["#text"] = text
            result = {tag: result}
        else:
            result = {tag: text}

    return result


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------

# Map of format identifiers → reader functions
FORMAT_READERS = {
    "csv": lambda p, cfg: _read_csv(p, delimiter=",",
                                     encoding=cfg.get("encoding", "utf-8-sig")),
    "tsv": lambda p, cfg: _read_csv(p, delimiter="\t",
                                     encoding=cfg.get("encoding", "utf-8-sig")),
    "xls_tab_delimited": lambda p, cfg: _read_csv(p, delimiter="\t",
                                                    encoding=cfg.get("encoding", "utf-8-sig")),
    "json": lambda p, cfg: _read_json(p, encoding=cfg.get("encoding", "utf-8")),
    "xml": lambda p, cfg: _read_xml(p, encoding=cfg.get("encoding", "utf-8")),
}


def _detect_format(path: Path) -> str:
    """Auto-detect file format from extension."""
    ext = path.suffix.lower()
    mapping = {
        ".csv": "csv",
        ".tsv": "tsv",
        ".xls": "xls_tab_delimited",  # Priority exports XLS as tab-delimited
        ".xlsx": "xls_tab_delimited",
        ".json": "json",
        ".xml": "xml",
        ".txt": "tsv",
    }
    return mapping.get(ext, "csv")


# ---------------------------------------------------------------------------
# Source handler (registered with integration.py)
# ---------------------------------------------------------------------------

def read_file_source(source_path: Path, source_config: dict) -> Any:
    """Read data from a file source.

    source_config may contain:
      - format: explicit format ("csv", "tsv", "json", "xml", "xls_tab_delimited")
      - encoding: file encoding (default: utf-8-sig)
      - delimiter: custom delimiter for CSV (default: depends on format)
    """
    path = Path(source_path)
    if not path.exists():
        raise SourceError(f"Source file not found: {path}")

    fmt = source_config.get("format") or _detect_format(path)
    reader = FORMAT_READERS.get(fmt)
    if reader is None:
        raise SourceError(f"Unsupported file format: {fmt}")

    logger.info("Reading source file %s (format: %s)", path.name, fmt)
    return reader(path, source_config)
