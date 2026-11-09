"""
Reads the Priority OrdRsp (order response) export file - a completely
separate source format from the TECOM invoice export (xls_reader.py).

Kept fully independent per the customer's explicit instruction (2026-08-24):
this format must never share code, folders, or assumptions with the invoice
format, so that it and any future format can be added/changed without any
risk to what already works. Do not import this from xls_reader.py or vice
versa, and do not "simplify" by merging them later without re-confirming
that's actually wanted.

Same situation as the invoice export: despite the .XLS extension, the file
is plain tab-delimited text.

Column names as agreed with the customer 2026-08-24 (second iteration -
the customer renamed the Priority query's columns to fix two problems in
the first sample: SELLER_PARTY_NO was blank, and two pairs of columns
shared the same name (LINE_BUYER_REFERENCE / LINE_SELLER_REFERENCE each
appeared twice, ambiguous). If Priority's export changes these names again,
update REQUIRED_COLUMNS here - nothing else needs to change.
"""
import csv
from pathlib import Path

REQUIRED_COLUMNS = [
    "ORD_ID", "DOC_NO", "ISSUE_DATE",
    "H_BUY_REF_QUAL", "H_BUY_REF_NO", "H_SELL_REF_QUAL", "H_SELL_REF_NO", "H_REF_DATE",
    "SELLER_PARTY_NO", "BUYER_PARTY_NO", "BUYER_CONTACT",
    "REQ_CURRENCY", "RESP_CURRENCY", "DISPATCH_MODE",
    "H_DELIV_QUAL", "H_REQ_DELIV_DATE", "BACKLOG", "COMPLETE_DELIVERY", "COMPLETE_SHIPMENT",
    "LINE_NO", "REQ_UOM", "REQ_QUANTITY", "CONF_UOM", "CONF_QUANTITY",
    "BRAND_QUAL", "BRAND_CODE", "PRODUCT_NO", "ADD_PROD_QUAL", "ADD_PROD_NO",
    "BUYER_PRODUCT_NO", "PRODUCT_NAME",
    "L_DELIV_QUAL", "L_REQ_DELIV_DATE", "NET_PRICE", "NET_AMOUNT",
    "L_BUY_REF_QUAL", "L_BUY_REF_NO", "L_BUY_REF_LINE", "L_BUY_DATE_QUAL", "L_BUY_REF_DATE",
    "L_SELL_REF_QUAL", "L_SELL_REF_NO", "L_SELL_REF_LINE", "L_SELL_DATE_QUAL", "L_SELL_REF_DATE",
    "ORD_ITEM_ID",
]


class OrdRspFormatError(ValueError):
    pass


def read_rows(path):
    """Reads the tab-delimited Priority OrdRsp export and returns a list of
    dicts (one per data row, keyed by column name). Raises OrdRspFormatError
    on missing/duplicate columns or malformed rows - mirrors xls_reader's
    behavior for the invoice format, but is its own independent code path.
    """
    path = Path(path)
    with open(path, "r", encoding="utf-8-sig", errors="replace", newline="") as f:
        reader = csv.reader(f, delimiter="\t")
        rows = list(reader)

    if not rows:
        raise OrdRspFormatError("File is empty")

    header = [h.strip() for h in rows[0]]
    data_rows = rows[1:]

    seen = {}
    for h in header:
        seen[h] = seen.get(h, 0) + 1
    dupes = [h for h, c in seen.items() if c > 1]
    if dupes:
        raise OrdRspFormatError(f"Duplicate column name(s) in file header: {dupes}")

    missing = [c for c in REQUIRED_COLUMNS if c not in header]
    if missing:
        raise OrdRspFormatError(f"Missing required column(s): {missing}")

    result = []
    for i, raw in enumerate(data_rows, start=2):  # start=2: header is line 1
        if len(raw) < len(header):
            raise OrdRspFormatError(f"Row {i} has fewer fields than the header")
        row = {col: (raw[idx].strip() if idx < len(raw) else "") for idx, col in enumerate(header)}
        result.append(row)

    if not result:
        raise OrdRspFormatError("File has a header but no data rows")

    return result


def group_by_order(rows):
    """Groups line rows by DOC_NO (the order-response document number),
    preserving first-seen order, and sorts each group's rows by LINE_NO.

    Returns a list of (doc_number, [rows...]) tuples. One input file is
    expected to contain exactly one order response - callers should treat
    more than one group as unexpected and route the file to errors, the
    same "one document per file" convention as the invoice format uses
    (each format decided this independently, they just happen to agree).
    """
    groups = {}
    order = []
    for row in rows:
        key = row["DOC_NO"]
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(row)

    for key in order:
        groups[key].sort(key=lambda r: int(r["LINE_NO"]) if r["LINE_NO"].strip().isdigit() else 0)

    return [(key, groups[key]) for key in order]
