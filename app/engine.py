"""
Orchestrates one file's journey: input -> parse -> convert -> output +
archive, or -> errors on any failure. This module has no knowledge of
folder-watching or the web UI; it is called by watcher.py (production) and
directly by tests/test_conversion.py (offline verification).

Dispatches by profile["converter"] to an entry in FORMAT_HANDLERS. Each
format (TECOM invoice/TXML, OrdRsp order-response, and anything added
later) plugs in its own reader module, grouping function, and XML builder
here - completely independent of every other format, per the customer's
explicit requirement that formats never share folders or code paths.
Adding a new format means adding one entry to FORMAT_HANDLERS and writing
its own reader/builder modules; it must never require editing an existing
format's entry or its reader/builder.

All of a format's own "this file is malformed" exceptions are expected to
subclass ValueError (xls_reader.XlsFormatError, tecom_xml.ConversionError,
ordrsp_reader.OrdRspFormatError, ordrsp_xml.ConversionError all do) - that
lets process_file() report a clean message for any of them without needing
to know each format's exception classes individually. Anything else is
treated as a genuine bug and logged with a full traceback.
"""
import logging
import shutil
import traceback
from datetime import datetime
from pathlib import Path

from . import db
from . import tecom_xml
from . import ordrsp_xml
from . import xls_reader
from . import ordrsp_reader

logger = logging.getLogger("motorad.engine")

db.init_db()  # safe no-op if already initialized; ensures the log table always exists

FORMAT_HANDLERS = {
    "tecom": {
        "read_rows": xls_reader.read_rows,
        "group": xls_reader.group_by_invoice,
        "group_label": "invoice",
        "build": tecom_xml.build_invoice_xml,
        "output_name": lambda key, rows: f"{key}.xml",
        # "primary"/"secondary" feed the conversion log's existing
        # invoice_number/customer_name columns - kept generic (not renamed)
        # so every format's identifiers show up in the same log/search UI.
        "log_primary": lambda key, rows: key,
        "log_secondary": lambda key, rows: rows[0].get("CUSTOMER_NAME"),
    },
    "ordrsp": {
        "read_rows": ordrsp_reader.read_rows,
        "group": ordrsp_reader.group_by_order,
        "group_label": "order response",
        "build": ordrsp_xml.build_ordrsp_xml,
        "output_name": lambda key, rows: f"{key}.xml",
        "log_primary": lambda key, rows: key,  # DOC_NO
        "log_secondary": lambda key, rows: rows[0].get("BUYER_PARTY_NO"),
    },
}


class ProcessingResult:
    def __init__(self, ok, invoice_number=None, customer_name=None, output_path=None, message=""):
        self.ok = ok
        self.invoice_number = invoice_number
        self.customer_name = customer_name
        self.output_path = output_path
        self.message = message


def _ensure_dirs(profile):
    for key in ("output_dir", "archive_dir", "error_dir"):
        p = profile.get(key)
        if p:
            Path(p).mkdir(parents=True, exist_ok=True)


def _unique_destination(directory, filename):
    dest = Path(directory) / filename
    if not dest.exists():
        return dest
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem, suffix = Path(filename).stem, Path(filename).suffix
    return Path(directory) / f"{stem}_{stamp}{suffix}"


def process_file(source_path, profile, cfg):
    """Processes a single source file for the given profile config dict.
    Returns a ProcessingResult. Never raises - all failures are caught,
    logged, and routed to the profile's error_dir.
    """
    source_path = Path(source_path)
    _ensure_dirs(profile)
    converter_name = profile.get("converter", "tecom")
    handler = FORMAT_HANDLERS.get(converter_name)

    invoice_number = None
    customer_name = None

    try:
        if handler is None:
            raise ValueError(f"Unknown converter '{converter_name}' for profile {profile.get('id')}")

        rows = handler["read_rows"](source_path)
        groups = handler["group"](rows)
        if len(groups) != 1:
            raise ValueError(
                f"Expected exactly one {handler['group_label']} per file, found {len(groups)}: "
                f"{[g[0] for g in groups]}"
            )
        key, group_rows = groups[0]
        invoice_number = handler["log_primary"](key, group_rows)
        customer_name = handler["log_secondary"](key, group_rows)

        xml_bytes = handler["build"](key, group_rows, cfg)

        output_name = handler["output_name"](key, group_rows)
        output_path = _unique_destination(profile["output_dir"], output_name)
        with open(output_path, "wb") as f:
            f.write(xml_bytes)

        archive_dest = _unique_destination(profile["archive_dir"], source_path.name)
        shutil.move(str(source_path), str(archive_dest))

        db.log_event(
            timestamp=datetime.now().isoformat(timespec="seconds"),
            profile_id=profile.get("id"),
            invoice_number=invoice_number,
            customer_name=customer_name,
            status="success",
            source_file=str(archive_dest),
            output_file=str(output_path),
            message="OK",
        )
        logger.info("Converted %s -> %s", source_path.name, output_path.name)
        return ProcessingResult(True, invoice_number, customer_name, output_path, "OK")

    except ValueError as exc:
        # Every format's own "this file is malformed" exceptions subclass
        # ValueError (see module docstring) - report their message as-is.
        message = str(exc)
    except Exception:
        message = "Unexpected error:\n" + traceback.format_exc()

    # --- failure path: route to errors/, with a sidecar explaining why ---
    try:
        error_dest = _unique_destination(profile["error_dir"], source_path.name)
        shutil.move(str(source_path), str(error_dest))
        sidecar = error_dest.with_suffix(error_dest.suffix + ".error.txt")
        with open(sidecar, "w", encoding="utf-8") as f:
            f.write(message)
    except Exception:
        logger.exception("Failed to move failed file %s to error folder", source_path)
        error_dest = source_path

    db.log_event(
        timestamp=datetime.now().isoformat(timespec="seconds"),
        profile_id=profile.get("id"),
        invoice_number=invoice_number,
        customer_name=customer_name,
        status="error",
        source_file=str(error_dest),
        output_file=None,
        message=message,
    )
    logger.error("Failed to convert %s: %s", source_path.name, message)
    return ProcessingResult(False, invoice_number, customer_name, None, message)
