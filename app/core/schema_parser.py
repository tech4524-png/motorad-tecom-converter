"""
Schema Parser — extract field schemas from sample data files.

Given a sample file (JSON, XML, CSV, XLS/XLSX) or raw data (dict/list),
this module extracts a flat list of field descriptors:

    [
        {"path": "header.invoice_number", "type": "string", "sample": "INV-001"},
        {"path": "header.date", "type": "string", "sample": "2026-01-15"},
        {"path": "lines[].item_code", "type": "string", "sample": "ABC-123"},
        {"path": "lines[].quantity", "type": "number", "sample": 5},
    ]

This unified schema is consumed by:
  - ai_mapper.py   → sent to the LLM for automatic mapping suggestions
  - mapping editor → displayed in the visual source↔target UI

Phase 3 of the MotoGateway roadmap.
"""
import csv
import io
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("motorad.core.schema_parser")

# Maximum depth when walking nested structures (prevents infinite recursion)
MAX_DEPTH = 10
# Maximum sample values to inspect when detecting types in arrays
MAX_ARRAY_SAMPLE = 5


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_schema_from_file(file_path: str, file_format: str = None) -> Tuple[List[dict], Optional[str]]:
    """Extract field schema from a sample file.

    Args:
        file_path: Path to the sample file.
        file_format: Explicit format hint ("json", "xml", "csv", "xls", "xlsx").
                     Auto-detected from extension if not provided.

    Returns:
        (fields, error) — fields is a list of field descriptors, error is None on success.
    """
    path = Path(file_path)
    if not path.exists():
        return [], f"File not found: {file_path}"

    fmt = (file_format or path.suffix.lstrip(".")).lower()

    try:
        if fmt == "json":
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return extract_schema_from_data(data), None

        elif fmt in ("xml",):
            return _parse_xml_schema(path), None

        elif fmt in ("csv", "tsv"):
            delimiter = "\t" if fmt == "tsv" else ","
            return _parse_csv_schema(path, delimiter), None

        elif fmt in ("xls", "xlsx"):
            return _parse_excel_schema(path), None

        else:
            return [], f"Unsupported format: {fmt}"

    except Exception as e:
        logger.exception("Schema extraction failed for %s", file_path)
        return [], f"Parse error: {e}"


def extract_schema_from_data(data: Any, root_path: str = "") -> List[dict]:
    """Extract field schema from in-memory data (dict or list).

    Returns a flat list of field descriptors with path, type, and sample value.
    """
    fields = []
    _walk(data, root_path, fields, depth=0)
    return fields


def extract_schema_from_raw(raw_text: str, content_type: str) -> Tuple[List[dict], Optional[str]]:
    """Extract schema from raw text with a content type hint.

    Useful for webhook/API payloads where we have the body text and content-type.
    """
    ct = (content_type or "").lower().split(";")[0].strip()

    if ct == "application/json" or ct == "":
        try:
            data = json.loads(raw_text)
            return extract_schema_from_data(data), None
        except json.JSONDecodeError as e:
            return [], f"Invalid JSON: {e}"

    elif ct in ("application/xml", "text/xml"):
        try:
            from lxml import etree
            root = etree.fromstring(raw_text.encode("utf-8") if isinstance(raw_text, str) else raw_text)
            fields = []
            _walk_xml(root, "", fields, depth=0)
            return fields, None
        except Exception as e:
            return [], f"Invalid XML: {e}"

    elif ct == "text/csv":
        try:
            reader = csv.DictReader(io.StringIO(raw_text))
            rows = list(reader)
            if not rows:
                return [], "CSV is empty"
            return _schema_from_rows(rows), None
        except Exception as e:
            return [], f"CSV parse error: {e}"

    else:
        return [], f"Unsupported content type: {ct}"


# ---------------------------------------------------------------------------
# Type detection
# ---------------------------------------------------------------------------

def _detect_type(value: Any) -> str:
    """Detect a human-friendly type label for a value."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    # String — try to detect common subtypes
    s = str(value).strip()
    if not s:
        return "string"
    # ISO date pattern
    if _looks_like_date(s):
        return "date"
    # Numeric string
    try:
        float(s.replace(",", ""))
        return "number"
    except ValueError:
        pass
    return "string"


def _looks_like_date(s: str) -> bool:
    """Heuristic check for common date formats."""
    import re
    patterns = [
        r"^\d{4}-\d{2}-\d{2}",           # 2026-01-15
        r"^\d{2}/\d{2}/\d{4}",           # 15/01/2026
        r"^\d{2}\.\d{2}\.\d{4}",         # 15.01.2026
        r"^\d{2}-\d{2}-\d{4}",           # 15-01-2026
    ]
    return any(re.match(p, s) for p in patterns)


# ---------------------------------------------------------------------------
# Recursive data walking
# ---------------------------------------------------------------------------

def _walk(data: Any, prefix: str, fields: List[dict], depth: int):
    """Recursively walk a data structure and collect field descriptors."""
    if depth > MAX_DEPTH:
        return

    if isinstance(data, dict):
        for key, value in data.items():
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict):
                _walk(value, path, fields, depth + 1)
            elif isinstance(value, list):
                _walk_array(value, path, fields, depth + 1)
            else:
                fields.append({
                    "path": path,
                    "type": _detect_type(value),
                    "sample": _safe_sample(value),
                })

    elif isinstance(data, list):
        _walk_array(data, prefix, fields, depth)


def _walk_array(items: list, path: str, fields: List[dict], depth: int):
    """Walk an array, using [] notation for item fields."""
    if not items or depth > MAX_DEPTH:
        return

    # Check what the array contains
    sample = items[:MAX_ARRAY_SAMPLE]
    first = sample[0]

    if isinstance(first, dict):
        # Array of objects — walk the first few to collect all keys
        merged_keys = {}
        for item in sample:
            if isinstance(item, dict):
                for k, v in item.items():
                    if k not in merged_keys:
                        merged_keys[k] = v

        for key, value in merged_keys.items():
            item_path = f"{path}[].{key}"
            if isinstance(value, dict):
                _walk(value, item_path, fields, depth + 1)
            elif isinstance(value, list):
                _walk_array(value, item_path, fields, depth + 1)
            else:
                fields.append({
                    "path": item_path,
                    "type": _detect_type(value),
                    "sample": _safe_sample(value),
                })
    else:
        # Array of primitives
        fields.append({
            "path": f"{path}[]",
            "type": _detect_type(first),
            "sample": _safe_sample(first),
        })


def _safe_sample(value: Any) -> Any:
    """Return a sample value safe for JSON serialization, truncated if long."""
    if value is None:
        return None
    if isinstance(value, (int, float, bool)):
        return value
    s = str(value)
    return s[:100] if len(s) > 100 else s


# ---------------------------------------------------------------------------
# XML schema extraction
# ---------------------------------------------------------------------------

def _parse_xml_schema(file_path: Path) -> List[dict]:
    """Parse an XML file and extract field schema."""
    from lxml import etree
    tree = etree.parse(str(file_path))
    root = tree.getroot()
    fields = []
    _walk_xml(root, "", fields, depth=0)
    return fields


def _walk_xml(element, prefix: str, fields: List[dict], depth: int):
    """Walk an XML element tree and extract field paths."""
    if depth > MAX_DEPTH:
        return

    # Strip namespace
    tag = element.tag
    if "}" in tag:
        tag = tag.split("}", 1)[1]

    path = f"{prefix}.{tag}" if prefix else tag
    children = list(element)

    if not children:
        # Leaf node
        text = (element.text or "").strip()
        fields.append({
            "path": path,
            "type": _detect_type(text) if text else "string",
            "sample": text[:100] if text else "",
        })
        # Also extract attributes
        for attr_name, attr_value in element.attrib.items():
            fields.append({
                "path": f"{path}@{attr_name}",
                "type": _detect_type(attr_value),
                "sample": str(attr_value)[:100],
            })
    else:
        # Check for repeated child tags (→ array)
        child_tags = [c.tag.split("}", 1)[-1] if "}" in c.tag else c.tag for c in children]
        from collections import Counter
        tag_counts = Counter(child_tags)

        seen_tags = set()
        for child in children:
            child_tag = child.tag.split("}", 1)[-1] if "}" in child.tag else child.tag
            if child_tag in seen_tags:
                continue
            seen_tags.add(child_tag)

            if tag_counts[child_tag] > 1:
                # Repeated element → array notation
                _walk_xml(child, f"{path}.{child_tag}[]", fields, depth + 1)
            else:
                _walk_xml(child, path, fields, depth + 1)

        # Element attributes
        for attr_name, attr_value in element.attrib.items():
            fields.append({
                "path": f"{path}@{attr_name}",
                "type": _detect_type(attr_value),
                "sample": str(attr_value)[:100],
            })


# ---------------------------------------------------------------------------
# CSV / Excel schema extraction
# ---------------------------------------------------------------------------

def _parse_csv_schema(file_path: Path, delimiter: str = ",") -> List[dict]:
    """Parse a CSV/TSV file and extract column schema."""
    with open(file_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=delimiter)
        rows = []
        for i, row in enumerate(reader):
            rows.append(row)
            if i >= MAX_ARRAY_SAMPLE:
                break

    if not rows:
        return []
    return _schema_from_rows(rows)


def _parse_excel_schema(file_path: Path) -> List[dict]:
    """Parse an XLS/XLSX file and extract column schema."""
    try:
        import openpyxl
        wb = openpyxl.load_workbook(str(file_path), read_only=True, data_only=True)
        ws = wb.active
        rows_iter = ws.iter_rows(values_only=True)

        # First row = headers
        headers = next(rows_iter, None)
        if not headers:
            return []
        headers = [str(h or f"col_{i}").strip() for i, h in enumerate(headers)]

        rows = []
        for i, row in enumerate(rows_iter):
            if i >= MAX_ARRAY_SAMPLE:
                break
            rows.append(dict(zip(headers, row)))

        wb.close()
        if not rows:
            return []
        return _schema_from_rows(rows)

    except ImportError:
        # Fall back to xlrd for .xls
        try:
            import xlrd
            wb = xlrd.open_workbook(str(file_path))
            ws = wb.sheet_by_index(0)
            if ws.nrows < 2:
                return []
            headers = [str(ws.cell_value(0, c) or f"col_{c}").strip()
                       for c in range(ws.ncols)]
            rows = []
            for r in range(1, min(ws.nrows, MAX_ARRAY_SAMPLE + 1)):
                row = {headers[c]: ws.cell_value(r, c) for c in range(ws.ncols)}
                rows.append(row)
            return _schema_from_rows(rows)
        except ImportError:
            raise ImportError("Install openpyxl or xlrd to read Excel files")


def _schema_from_rows(rows: List[dict]) -> List[dict]:
    """Build schema from a list of row dicts (from CSV or Excel)."""
    if not rows:
        return []

    # Collect all column names across rows
    all_keys = {}
    for row in rows:
        for key, value in row.items():
            if key and key not in all_keys and value is not None and str(value).strip():
                all_keys[key] = value

    fields = []
    for key, sample_value in all_keys.items():
        fields.append({
            "path": key,
            "type": _detect_type(sample_value),
            "sample": _safe_sample(sample_value),
        })
    return fields
