"""
Reads the Priority export file.

Despite the .XLS extension, the files produced by this Priority query are
actually plain tab-delimited text (verified against two real samples). This
reader is intentionally tolerant of that: it sniffs the delimiter but expects
tab, and treats the file as UTF-8 text. If Priority is later changed to
produce a true binary .xls/.xlsx, swap this module's implementation for
openpyxl/pandas without touching any other module - callers only depend on
the list-of-dict rows this returns.
"""
import csv
from pathlib import Path

REQUIRED_COLUMNS = [
    "INVOICE_NUMBER", "INVOICE_DATE", "OrderRef", "OrderDate", "SELLERORDER",
    "BUYER_PARTY", "SELLER_PARTY", "CUSTOMER_NAME",
    "ADDRESS", "CITY", "ZIP", "COUNTRY", "CURRENCY",
    "BUYERORDLINE", "ORDERLINE", "TQUANT", "UOM", "PRODNUMBER", "BARCODE",
    "CUSTOMER_PART_NAME", "PART_DESCRIPTION", "UNIT_PRICE", "DISCOUNT",
    "COUNTRY_OF_ORIGIN", "PAYMENT_CONDITIONS", "ALLINVOICETAX",
]

# The buyer tax-id column: the two original samples exported it truncated
# as "TaxRegistrationNumb" (Priority appears to cut it at a fixed field-name
# length). A real production file received 2026-08-23 exported the same
# column fully spelled as "TaxRegistrationNumber" instead - whichever
# Priority version generated the file, apparently. Rather than gamble on
# which spelling shows up next, accept either and normalize to the
# canonical name below so every other module only ever needs to know one
# key. This column is treated as required (one of the two spellings must be
# present) but is intentionally NOT in REQUIRED_COLUMNS since "in header"
# there would only ever match one exact spelling.
TAX_REG_COLUMN = "TaxRegistrationNumb"
TAX_REG_COLUMN_ALIASES = ["TaxRegistrationNumb", "TaxRegistrationNumber"]


class XlsFormatError(ValueError):
    pass


def read_rows(path):
    """Reads the tab-delimited Priority export and returns a list of dicts
    (one dict per data row, keyed by column name). Raises XlsFormatError if
    required columns are missing or column names are duplicated (a known
    failure mode seen in early versions of the Priority query).
    """
    path = Path(path)
    with open(path, "r", encoding="utf-8-sig", errors="replace", newline="") as f:
        reader = csv.reader(f, delimiter="\t")
        rows = list(reader)

    if not rows:
        raise XlsFormatError("File is empty")

    header = [h.strip() for h in rows[0]]
    data_rows = rows[1:]

    # Duplicate column names are a real bug we hit during testing - fail
    # loudly instead of silently reading the wrong column.
    seen = {}
    for h in header:
        seen[h] = seen.get(h, 0) + 1
    dupes = [h for h, c in seen.items() if c > 1]
    if dupes:
        raise XlsFormatError(f"Duplicate column name(s) in file header: {dupes}")

    missing = [c for c in REQUIRED_COLUMNS if c not in header]
    tax_reg_present = [c for c in TAX_REG_COLUMN_ALIASES if c in header]
    if not tax_reg_present:
        missing.append(f"{TAX_REG_COLUMN} (or TaxRegistrationNumber)")
    if missing:
        raise XlsFormatError(f"Missing required column(s): {missing}")
    # Prefer the canonical spelling if a file ever has both.
    tax_reg_source_col = TAX_REG_COLUMN if TAX_REG_COLUMN in tax_reg_present else tax_reg_present[0]

    result = []
    for i, raw in enumerate(data_rows, start=2):  # start=2: header is line 1
        if len(raw) < len(header):
            raise XlsFormatError(f"Row {i} has fewer fields than the header")
        row = {col: (raw[idx].strip() if idx < len(raw) else "") for idx, col in enumerate(header)}
        # Normalize whichever spelling this file used to the canonical key,
        # so tecom_xml.py only ever has to look up TAX_REG_COLUMN.
        if tax_reg_source_col != TAX_REG_COLUMN:
            row[TAX_REG_COLUMN] = row.get(tax_reg_source_col, "")
        result.append(row)

    if not result:
        raise XlsFormatError("File has a header but no data rows")

    return result


def group_by_invoice(rows):
    """Groups line rows by INVOICE_NUMBER, preserving first-seen order.

    Returns a list of (invoice_number, [rows...]) tuples. A single input
    file is expected to contain exactly one invoice (confirmed for this
    integration) - callers should treat more than one group as unexpected
    and route the file to errors, since downstream logic (one XML per file)
    assumes a single invoice.
    """
    groups = {}
    order = []
    for row in rows:
        inv = row["INVOICE_NUMBER"]
        if inv not in groups:
            groups[inv] = []
            order.append(inv)
        groups[inv].append(row)
    return [(inv, groups[inv]) for inv in order]
