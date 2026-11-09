"""
Builds an OrdRsp 5.0 XML document (TecCom order-response format) from one
order's worth of grouped Priority export rows (see ordrsp_reader.py) plus
system configuration.

Kept completely independent from tecom_xml.py (a different business
document, different schema, different customer relationship-level party
numbers). No shared code, no shared assumptions - do not import from
tecom_xml.py or have it import from here, and do not merge them later
without re-confirming that's actually wanted. This is the second format
the system supports; more can be added the same way (new reader + new
builder + a registry entry in engine.py) without touching this file or
tecom_xml.py.

Business-logic notes worked out with the customer (2026-08-24):
  - One input file == one order response document (DOC_NO).
  - No XML namespace on the root element - confirmed against the
    customer's own sample (<OrdRsp version="5.0">, no xmlns), unlike
    tecom_xml.py's TXML invoice format which does declare one.
  - NetPrice/NetAmount are passed through verbatim from Priority. Priority
    already computes NetAmount itself (observed ~= NetPrice x 1.19, i.e. a
    VAT-inclusive per-unit price - NOT NetPrice x Quantity, unlike the
    invoice format's line totals) - we do not recompute or validate this
    ratio, just pass both numbers through as given.
  - Requested vs Confirmed quantity/UoM can differ (partial order
    confirmation) - both are read independently from their own columns,
    never assumed equal.
  - ISSUE_DATE's seconds component has been observed arriving broken from
    Priority (a literal "ss" instead of two digits - apparently a report
    format string that isn't being substituted correctly on their side).
    Per the customer's explicit instruction, this is handled entirely on
    our side rather than blocked on a Priority fix: default to "00" when
    the seconds aren't two digits, and always append a fixed "+03:00"
    offset (matching the customer's sample XML exactly - not computed from
    real DST, this is a fixed constant by agreement, confirmed 2026-08-24).
  - All other date fields in this format are date-only (XSD xs:date,
    YYYY-MM-DD). TECOM rejected our first real submission because Priority
    sends these as 'M/D/YYYY' (e.g. '1/22/2026', no leading zeros) - not
    YYYY-MM-DD - so as of 2026-09-14 every one of these fields is
    reformatted on our side via _format_date() rather than passed through
    verbatim: H_REF_DATE, H_REQ_DELIV_DATE, L_REQ_DELIV_DATE,
    L_BUY_REF_DATE, L_SELL_REF_DATE.
  - Same submission also rejected Backlog/CompleteDelivery/CompleteShipment:
    Priority sends 'TRUE'/'FALSE' but XSD xs:boolean requires lowercase
    'true'/'false' (or '1'/'0') - reformatted via _format_boolean().
  - 2026-10-07, per TECOM's OrdRsp-sync compatibility feedback:
      * AdditionalProductId/Number where Qualifier == 'TradeNumber' must
        carry a trailing '=' ('B' -> 'B=', 'C' -> 'C=') - applied to any
        value, not just 'B', and never doubled if already present. See
        _format_trade_number().
      * Brand/Code is a fixed constant, BRAND_CODE_FIXED = 'Motora' (the
        code the brand is registered under on the TecCom platform),
        regardless of what Priority sends in BRAND_CODE.
"""
import re

from lxml import etree

ISSUE_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}):(\d{2}):(\S+)$")
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MDY_DATE_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
FIXED_UTC_OFFSET = "+03:00"
BRAND_CODE_FIXED = "Motora"


class ConversionError(ValueError):
    pass


def _require(value, field_label):
    if value is None or str(value).strip() == "":
        raise ConversionError(f"Missing required value: {field_label}")
    return value


def _format_trade_number(raw, qualifier):
    """TECOM requires the TradeNumber value to end with '=' (e.g. 'B=').
    Appends it when missing; other qualifiers pass through untouched."""
    value = str(raw).strip()
    if str(qualifier).strip() == "TradeNumber" and not value.endswith("="):
        value += "="
    return value


def _el(parent, tag, text=None, **attrib):
    e = etree.SubElement(parent, tag)
    if text is not None:
        e.text = str(text)
    for k, v in attrib.items():
        e.set(k, str(v))
    return e


def _format_issue_date(raw):
    raw = _require(raw, "ISSUE_DATE").strip()
    m = ISSUE_DATE_RE.match(raw)
    if not m:
        raise ConversionError(f"Unrecognized ISSUE_DATE format: {raw!r}")
    date_part, hh, mm, ss = m.groups()
    if not (ss.isdigit() and len(ss) == 2):
        ss = "00"  # Priority sometimes sends a literal "ss" placeholder here
    return f"{date_part}T{hh}:{mm}:{ss}{FIXED_UTC_OFFSET}"


def _format_date(raw, field_label):
    """Normalizes a date-only field to XSD xs:date (YYYY-MM-DD).

    Priority's ORDRSP export has been observed sending these as
    'M/D/YYYY' with no leading zeros (e.g. '1/22/2026'), which TECOM's
    schema validator rejects outright. Accepts that format or an
    already-correct ISO date; anything else is a hard error rather than a
    silent guess, per the same pattern as every other field in this file.
    """
    raw = _require(raw, field_label).strip()
    if ISO_DATE_RE.match(raw):
        return raw
    m = MDY_DATE_RE.match(raw)
    if m:
        month, day, year = m.groups()
        return f"{year}-{int(month):02d}-{int(day):02d}"
    raise ConversionError(f"Unrecognized date format for {field_label}: {raw!r}")


def _format_boolean(raw, field_label):
    """Normalizes a boolean field to XSD xs:boolean ('true'/'false').

    Priority sends 'TRUE'/'FALSE' (any case observed) - TECOM's schema
    validator requires lowercase 'true'/'false' (or '1'/'0').
    """
    raw = _require(raw, field_label).strip().lower()
    if raw in ("true", "1"):
        return "true"
    if raw in ("false", "0"):
        return "false"
    raise ConversionError(f"Unrecognized boolean value for {field_label}: {raw!r}")


def build_ordrsp_xml(doc_number, rows, cfg):
    """rows: all line-rows for one order response (already grouped/sorted
    by ordrsp_reader.group_by_order). cfg is accepted for a consistent
    signature with tecom_xml.build_invoice_xml, even though this format
    currently needs nothing from Settings - every value comes from the
    Priority export itself.
    """
    header_row = rows[0]

    root = etree.Element("OrdRsp", version="5.0")

    # --- Head ---
    head = etree.SubElement(root, "Head")
    _el(head, "DocumentNumber", _require(header_row.get("DOC_NO"), "DOC_NO"))
    _el(head, "IssueDate", _format_issue_date(header_row.get("ISSUE_DATE")))

    ref_buyer = etree.SubElement(head, "Reference")
    _el(ref_buyer, "Qualifier", _require(header_row.get("H_BUY_REF_QUAL"), "H_BUY_REF_QUAL"))
    _el(ref_buyer, "Number", _require(header_row.get("H_BUY_REF_NO"), "H_BUY_REF_NO"))

    ref_seller = etree.SubElement(head, "Reference")
    _el(ref_seller, "Qualifier", _require(header_row.get("H_SELL_REF_QUAL"), "H_SELL_REF_QUAL"))
    _el(ref_seller, "Number", _require(header_row.get("H_SELL_REF_NO"), "H_SELL_REF_NO"))
    if header_row.get("H_REF_DATE"):
        seller_ref_date = etree.SubElement(ref_seller, "Date")
        _el(seller_ref_date, "Qualifier", "At")
        _el(seller_ref_date, "Date", _format_date(header_row["H_REF_DATE"], "H_REF_DATE"))

    seller_party = etree.SubElement(head, "SellerParty")
    _el(seller_party, "Number", _require(header_row.get("SELLER_PARTY_NO"), "SELLER_PARTY_NO"))

    buyer_party = etree.SubElement(head, "BuyerParty")
    _el(buyer_party, "Number", _require(header_row.get("BUYER_PARTY_NO"), "BUYER_PARTY_NO"))
    contact = etree.SubElement(buyer_party, "Contact")
    info = etree.SubElement(contact, "Information")
    _el(info, "Qualifier", "Person")
    _el(info, "Name", header_row.get("BUYER_CONTACT") or "")

    currency = etree.SubElement(head, "Currency")
    _el(currency, "Request", _require(header_row.get("REQ_CURRENCY"), "REQ_CURRENCY"))
    _el(currency, "Response", _require(header_row.get("RESP_CURRENCY"), "RESP_CURRENCY"))

    proc = etree.SubElement(head, "ProcessingInstructions")
    _el(proc, "DispatchMode", _require(header_row.get("DISPATCH_MODE"), "DISPATCH_MODE"))
    head_rdd = etree.SubElement(proc, "RequestedDeliveryDate")
    _el(head_rdd, "Qualifier", _require(header_row.get("H_DELIV_QUAL"), "H_DELIV_QUAL"))
    _el(head_rdd, "Date", _format_date(header_row.get("H_REQ_DELIV_DATE"), "H_REQ_DELIV_DATE"))
    _el(proc, "Backlog", _format_boolean(header_row.get("BACKLOG"), "BACKLOG"))
    _el(proc, "CompleteDelivery", _format_boolean(header_row.get("COMPLETE_DELIVERY"), "COMPLETE_DELIVERY"))
    _el(proc, "CompleteShipment", _format_boolean(header_row.get("COMPLETE_SHIPMENT"), "COMPLETE_SHIPMENT"))

    # --- Lines ---
    for row in rows:
        line = etree.SubElement(root, "Line")
        _el(line, "Number", _require(row.get("LINE_NO"), "LINE_NO"))

        total_qty = etree.SubElement(line, "TotalQuantity")
        requested = etree.SubElement(total_qty, "Requested")
        _el(requested, "UoM", _require(row.get("REQ_UOM"), "REQ_UOM"))
        _el(requested, "Value", _require(row.get("REQ_QUANTITY"), "REQ_QUANTITY"))
        confirmed = etree.SubElement(total_qty, "Confirmed")
        _el(confirmed, "UoM", _require(row.get("CONF_UOM"), "CONF_UOM"))
        _el(confirmed, "Value", _require(row.get("CONF_QUANTITY"), "CONF_QUANTITY"))

        product = etree.SubElement(line, "ProductId")
        brand = etree.SubElement(product, "Brand")
        _el(brand, "Qualifier", _require(row.get("BRAND_QUAL"), "BRAND_QUAL"))
        _el(brand, "Code", BRAND_CODE_FIXED)
        _el(product, "ProductNumber", _require(row.get("PRODUCT_NO"), "PRODUCT_NO"))
        add_id = etree.SubElement(product, "AdditionalProductId")
        add_qual = _require(row.get("ADD_PROD_QUAL"), "ADD_PROD_QUAL")
        _el(add_id, "Qualifier", add_qual)
        _el(add_id, "Number", _format_trade_number(_require(row.get("ADD_PROD_NO"), "ADD_PROD_NO"), add_qual))
        _el(product, "BuyerProductNumber", _require(row.get("BUYER_PRODUCT_NO"), "BUYER_PRODUCT_NO"))

        _el(line, "ProductName", _require(row.get("PRODUCT_NAME"), "PRODUCT_NAME"))

        line_rdd = etree.SubElement(line, "RequestedDeliveryDate")
        _el(line_rdd, "Qualifier", _require(row.get("L_DELIV_QUAL"), "L_DELIV_QUAL"))
        _el(line_rdd, "Date", _format_date(row.get("L_REQ_DELIV_DATE"), "L_REQ_DELIV_DATE"))

        price = etree.SubElement(line, "Price")
        _el(price, "NetPrice", _require(row.get("NET_PRICE"), "NET_PRICE"))
        _el(price, "NetAmount", _require(row.get("NET_AMOUNT"), "NET_AMOUNT"))

        line_ref_buyer = etree.SubElement(line, "Reference")
        _el(line_ref_buyer, "Qualifier", _require(row.get("L_BUY_REF_QUAL"), "L_BUY_REF_QUAL"))
        _el(line_ref_buyer, "Number", _require(row.get("L_BUY_REF_NO"), "L_BUY_REF_NO"))
        _el(line_ref_buyer, "Line", _require(row.get("L_BUY_REF_LINE"), "L_BUY_REF_LINE"))
        lb_date = etree.SubElement(line_ref_buyer, "Date")
        _el(lb_date, "Qualifier", _require(row.get("L_BUY_DATE_QUAL"), "L_BUY_DATE_QUAL"))
        _el(lb_date, "Date", _format_date(row.get("L_BUY_REF_DATE"), "L_BUY_REF_DATE"))

        line_ref_seller = etree.SubElement(line, "Reference")
        _el(line_ref_seller, "Qualifier", _require(row.get("L_SELL_REF_QUAL"), "L_SELL_REF_QUAL"))
        _el(line_ref_seller, "Number", _require(row.get("L_SELL_REF_NO"), "L_SELL_REF_NO"))
        _el(line_ref_seller, "Line", _require(row.get("L_SELL_REF_LINE"), "L_SELL_REF_LINE"))
        ls_date = etree.SubElement(line_ref_seller, "Date")
        _el(ls_date, "Qualifier", _require(row.get("L_SELL_DATE_QUAL"), "L_SELL_DATE_QUAL"))
        _el(ls_date, "Date", _format_date(row.get("L_SELL_REF_DATE"), "L_SELL_REF_DATE"))

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)
