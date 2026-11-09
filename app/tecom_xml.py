"""
Builds a TXML 2.5 Invoice XML document (TecCom / TECOM format) from one
invoice's worth of grouped Priority export rows plus the system's
configuration (seller/company constants, banking, etc).

All business-logic assumptions made here are the ones worked out with the
customer during requirements gathering:

  - One input file == one invoice (no header/OrderRef despatch-advice flow).
  - Header uses OrderRef (not DesAdvRef): the invoice is raised directly
    against the customer's purchase order.
  - VAT percent (ALLINVOICETAX) is a single value for the whole invoice and
    is replicated onto every line's <Tax> and into <TaxTotals>.
  - Per-line DISCOUNT is always a percentage, never a fixed amount.
  - DeliveryParty and a distinct InvoiceOrg/InvoiceParty are not present in
    the source data; InvoiceOrg/InvoiceParty is mirrored from BuyerParty
    (a common convention when there is no separate accounting department),
    and DeliveryParty is omitted entirely. Revisit if TECOM rejects this.
  - AdditionalPrice (GrossPrice) defaults to the pre-discount unit price,
    since no separate list price is supplied by Priority.
  - PRODNUMBER is sent through verbatim, including any prefix.
"""
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

from lxml import etree

from . import config as config_module
from .payment_terms import parse_payment_conditions, PaymentTermsParseError

NS = "http://www.teccom.de/tecorder/invoic/txml25"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
SCHEMA_LOCATION = f"{NS} ..\\XSD_Schemas\\TXML_2.5_INVOICE_DESADV.xsd"

TWOPLACES = Decimal("0.01")


class ConversionError(ValueError):
    """Raised for any data problem that should route the file to errors/."""


def _q(amount):
    """Quantize a Decimal to 2 decimal places using standard rounding."""
    return amount.quantize(TWOPLACES, rounding=ROUND_HALF_UP)


def _dec(value, field_name):
    try:
        return Decimal(str(value).strip())
    except Exception as exc:
        raise ConversionError(f"Invalid numeric value for {field_name}: {value!r}") from exc


def parse_priority_date(value):
    """Priority dates observed as DD/MM/YY. Returns a datetime.date."""
    value = (value or "").strip()
    for fmt in ("%d/%m/%y", "%d/%m/%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ConversionError(f"Unrecognized date format: {value!r} (expected DD/MM/YY)")


def fmt_date(d):
    return d.strftime("%Y%m%d")


def _el(parent, tag, text=None, **attrs):
    e = etree.SubElement(parent, f"{{{NS}}}{tag}")
    for k, v in attrs.items():
        e.set(k, str(v))
    if text is not None:
        e.text = str(text)
    return e


def _date_el(parent, tag, date_value, qualifier="At"):
    wrapper = _el(parent, tag)
    _el(wrapper, "Date", fmt_date(date_value), Qualifier=qualifier)
    return wrapper


def _require(value, field_label):
    if value is None or str(value).strip() == "":
        raise ConversionError(f"Missing required value: {field_label}")
    return value


def _resolve_country(name, cfg, field_label):
    code = config_module.resolve_country_code(name, cfg)
    if not code:
        raise ConversionError(
            f"No country-code mapping for {field_label} value {name!r}. "
            f"Add it under Settings > Country mapping."
        )
    return code


def build_invoice_xml(invoice_number, rows, cfg):
    """rows: list of dict (all rows for this single invoice, as produced by
    xls_reader.read_rows). Returns bytes (pretty-printed UTF-8 XML).
    """
    if not rows:
        raise ConversionError("No rows supplied for invoice")

    header_row = rows[0]
    seller_cfg = cfg["seller"]
    banking_cfg = cfg["banking"]

    issue_date = parse_priority_date(header_row["INVOICE_DATE"])
    order_date = parse_priority_date(header_row["OrderDate"])

    try:
        terms = parse_payment_conditions(header_row.get("PAYMENT_CONDITIONS", ""))
    except PaymentTermsParseError as exc:
        raise ConversionError(f"Could not parse PAYMENT_CONDITIONS: {exc}") from exc

    due_date = issue_date + timedelta(days=terms["net_days"])

    tax_percent = _dec(_require(header_row.get("ALLINVOICETAX"), "ALLINVOICETAX"), "ALLINVOICETAX")

    buyer_country_code = _resolve_country(header_row["COUNTRY"], cfg, "BuyerParty country")

    nsmap = {None: NS, "xsi": XSI_NS}
    root = etree.Element(f"{{{NS}}}Invoice", nsmap=nsmap)
    root.set(f"{{{XSI_NS}}}schemaLocation", SCHEMA_LOCATION)

    _el(root, "Document", Type="Invoice", Version="2.5")

    header = etree.SubElement(root, f"{{{NS}}}InvoiceHeader")
    _el(header, "InvoiceId", _require(invoice_number, "INVOICE_NUMBER"))
    _el(header, "MessageType", seller_cfg["message_type"])
    _el(header, "MessageFunction", seller_cfg["message_function"])
    _date_el(header, "InvoiceIssueDate", issue_date)

    order_ref = etree.SubElement(header, f"{{{NS}}}OrderRef")
    seller_order = header_row.get("SELLERORDER", "").strip()
    if seller_order:
        _el(order_ref, "SellerOrderNumber", seller_order)
    _el(order_ref, "BuyerOrderNumber", _require(header_row.get("OrderRef"), "OrderRef (buyer PO number)"))
    _el(order_ref, "Date", fmt_date(order_date), Qualifier="At")

    _el(header, "TaxTreatmentCode", Value=seller_cfg["tax_treatment_code"])
    _date_el(header, "InvoiceDueDate", due_date)

    if terms.get("raw_text"):
        _el(header, "PaymentInstruction", terms["raw_text"].strip())

    if terms.get("discount_percent") and terms.get("discount_days"):
        pt_discount = etree.SubElement(header, f"{{{NS}}}PaymentTerms")
        _el(pt_discount, "PaymentDeadline", "Discount")
        pd = etree.SubElement(pt_discount, f"{{{NS}}}PaymentDiscount")
        _el(pd, "DiscountPercent", f"{Decimal(str(terms['discount_percent'])):.2f}")
        _el(pd, "DiscountDays", terms["discount_days"])
        discount_date = issue_date + timedelta(days=terms["discount_days"])
        _el(pd, "Date", fmt_date(discount_date), Qualifier="At")

    pt_net = etree.SubElement(header, f"{{{NS}}}PaymentTerms")
    _el(pt_net, "PaymentDeadline", "Net")
    pd_net = etree.SubElement(pt_net, f"{{{NS}}}PaymentDiscount")
    _el(pd_net, "DiscountPercent", "0")
    _el(pd_net, "DiscountDays", terms["net_days"])
    _el(pd_net, "Date", fmt_date(due_date), Qualifier="At")

    # --- SellerParty (Motorad) ---
    seller_party = etree.SubElement(header, f"{{{NS}}}SellerParty")
    _el(seller_party, "PartyNumber", _require(header_row.get("SELLER_PARTY"), "SELLER_PARTY"))
    if seller_cfg.get("tax_registration_number"):
        _el(seller_party, "TaxRegistrationNumber", seller_cfg["tax_registration_number"])
    addr = etree.SubElement(seller_party, f"{{{NS}}}Address")
    _el(addr, "Name1", _require(seller_cfg.get("name1"), "Settings > Seller > Name"))
    if seller_cfg.get("street1"):
        _el(addr, "Street1", seller_cfg["street1"])
    if seller_cfg.get("postal_code"):
        _el(addr, "PostalCode", seller_cfg["postal_code"])
    if seller_cfg.get("city"):
        _el(addr, "City", seller_cfg["city"])
    _el(addr, "CountryCode", _require(seller_cfg.get("country_code"), "Settings > Seller > CountryCode"))
    if seller_cfg.get("country_name"):
        _el(addr, "CountryName", seller_cfg["country_name"])
    if seller_cfg.get("contact_name") or seller_cfg.get("contact_phone") or seller_cfg.get("contact_email"):
        contact = etree.SubElement(seller_party, f"{{{NS}}}ContactInformation")
        if seller_cfg.get("contact_name"):
            _el(contact, "Name", seller_cfg["contact_name"])
        if seller_cfg.get("contact_phone"):
            _el(contact, "Telephone", seller_cfg["contact_phone"])
        if seller_cfg.get("contact_email"):
            _el(contact, "EMail", seller_cfg["contact_email"])

    # --- BuyerParty (customer) ---
    buyer_party = etree.SubElement(header, f"{{{NS}}}BuyerParty")
    _el(buyer_party, "PartyNumber", _require(header_row.get("BUYER_PARTY"), "BUYER_PARTY"))
    tax_reg = header_row.get("TaxRegistrationNumb", "").strip()
    if tax_reg:
        _el(buyer_party, "TaxRegistrationNumber", tax_reg)
    baddr = etree.SubElement(buyer_party, f"{{{NS}}}Address")
    _el(baddr, "Name1", _require(header_row.get("CUSTOMER_NAME"), "CUSTOMER_NAME"))
    if header_row.get("ADDRESS"):
        _el(baddr, "Street1", header_row["ADDRESS"])
    if header_row.get("ZIP"):
        _el(baddr, "PostalCode", header_row["ZIP"])
    if header_row.get("CITY"):
        _el(baddr, "City", header_row["CITY"])
    _el(baddr, "CountryCode", buyer_country_code)
    _el(baddr, "CountryName", header_row["COUNTRY"])

    # InvoiceOrg mirrors BuyerParty (no distinct accounting-department data
    # supplied). Revisit if TECOM requires a genuinely separate party here.
    invoice_org = etree.SubElement(header, f"{{{NS}}}InvoiceOrg")
    invoice_party = etree.SubElement(invoice_org, f"{{{NS}}}InvoiceParty")
    _el(invoice_party, "PartyNumber", header_row.get("BUYER_PARTY"))
    if tax_reg:
        _el(invoice_party, "TaxRegistrationNumber", tax_reg)
    iaddr = etree.SubElement(invoice_party, f"{{{NS}}}Address")
    _el(iaddr, "Name1", header_row["CUSTOMER_NAME"])
    if header_row.get("ADDRESS"):
        _el(iaddr, "Street1", header_row["ADDRESS"])
    if header_row.get("ZIP"):
        _el(iaddr, "PostalCode", header_row["ZIP"])
    if header_row.get("CITY"):
        _el(iaddr, "City", header_row["CITY"])
    _el(iaddr, "CountryCode", buyer_country_code)
    _el(iaddr, "CountryName", header_row["COUNTRY"])

    _el(header, "Currency", _require(header_row.get("CURRENCY"), "CURRENCY"))

    if any(banking_cfg.values()):
        banking = etree.SubElement(header, f"{{{NS}}}Banking")
        if banking_cfg.get("bank_name"):
            _el(banking, "Bankname", banking_cfg["bank_name"])
        if banking_cfg.get("bank_account"):
            _el(banking, "BankAccount", banking_cfg["bank_account"])
        if banking_cfg.get("bank_code_number"):
            _el(banking, "BankCodeNumber", banking_cfg["bank_code_number"])
        if banking_cfg.get("iban"):
            _el(banking, "IBAN", banking_cfg["iban"])
        if banking_cfg.get("swift_bic"):
            _el(banking, "SWIFT-BIC", banking_cfg["swift_bic"])

    # --- Line items ---
    def sort_key(r):
        try:
            return int(r.get("BUYERORDLINE", 0))
        except ValueError:
            return 0

    sorted_rows = sorted(rows, key=sort_key)

    net_value_total = Decimal("0")
    position = 0

    detail_elements = []
    for row in sorted_rows:
        position += 1
        qty = _dec(_require(row.get("TQUANT"), "TQUANT"), "TQUANT")
        unit_price = _dec(_require(row.get("UNIT_PRICE"), "UNIT_PRICE"), "UNIT_PRICE")
        discount_pct = _dec(row.get("DISCOUNT") or "0", "DISCOUNT")

        total_price = _q(unit_price * qty)
        total_value = _q(total_price * (Decimal("1") - discount_pct / Decimal("100")))
        net_value_total += total_value

        # COUNTRY_OF_ORIGIN is sometimes genuinely blank in Priority (not an
        # unrecognized country name - just not set on that product). No
        # mapping table can resolve an empty value, and per the customer's
        # decision this should not fail the whole invoice: omit the
        # ExternalTrade/CountryOfOrigin element for that line instead of
        # raising. A non-empty but unrecognized name still raises, so it
        # can be added under Settings > Country mapping as before.
        origin_raw = (row.get("COUNTRY_OF_ORIGIN") or "").strip()
        origin_code = _resolve_country(origin_raw, cfg, "ExternalTrade CountryOfOrigin") if origin_raw else None

        detail = etree.Element(f"{{{NS}}}InvoiceDetail")
        _el(detail, "PositionNumber", position)
        _el(detail, "Quantity", str(qty), UoM=_require(row.get("UOM"), "UOM"))

        product = etree.SubElement(detail, f"{{{NS}}}ProductId")
        _el(product, "MakerCode", seller_cfg.get("maker_code", "*"))
        _el(product, "ProductNumber", _require(row.get("PRODNUMBER"), "PRODNUMBER"))
        if row.get("BARCODE"):
            _el(product, "Ean", row["BARCODE"])
        if row.get("CUSTOMER_PART_NAME"):
            _el(product, "BuyerProductNumber", row["CUSTOMER_PART_NAME"])

        desc = etree.SubElement(detail, f"{{{NS}}}ProductDescription")
        _el(desc, "ProductName1", row.get("PART_DESCRIPTION", "").strip() or row.get("PRODNUMBER"))

        line_order_ref = etree.SubElement(detail, f"{{{NS}}}OrderRef")
        if seller_order:
            _el(line_order_ref, "SellerOrderNumber", seller_order)
        _el(line_order_ref, "BuyerOrderNumber", header_row.get("OrderRef"))
        _el(line_order_ref, "Date", fmt_date(order_date), Qualifier="At")

        order_item_ref = etree.SubElement(detail, f"{{{NS}}}OrderItemRef")
        if row.get("ORDERLINE"):
            _el(order_item_ref, "SellerOrderItemRef", row["ORDERLINE"])
        _el(order_item_ref, "BuyerOrderItemRef", _require(row.get("BUYERORDLINE"), "BUYERORDLINE"))

        if discount_pct != 0:
            alc = etree.SubElement(detail, f"{{{NS}}}AllowOrCharge")
            _el(alc, "AllowOrChargeSequenceNumber", 1)
            _el(alc, "AllowOrChargeIdentifier", Value="Allow")
            _el(alc, "AllowOrChargeCode", "Discount")
            _el(alc, "Percent", f"{discount_pct:.2f}")

        tax = etree.SubElement(detail, f"{{{NS}}}Tax")
        _el(tax, "TaxCode", "VAT")
        _el(tax, "Percent", f"{tax_percent:.3f}")

        _el(etree.SubElement(detail, f"{{{NS}}}UnitPrice"), "Amount", str(unit_price))
        _el(etree.SubElement(detail, f"{{{NS}}}TotalPrice"), "Amount", f"{total_price:.2f}")
        _el(etree.SubElement(detail, f"{{{NS}}}TotalValue"), "Amount", f"{total_value:.2f}")
        _el(detail, "AdditionalPrice", f"{unit_price:.2f}", AdditionalPriceType="GrossPrice")

        if origin_code:
            ext_trade = etree.SubElement(detail, f"{{{NS}}}ExternalTrade")
            _el(ext_trade, "CountryOfOrigin", origin_code)

        detail_elements.append(detail)

    for d in detail_elements:
        root.append(d)

    # --- Summary ---
    summary = etree.SubElement(root, f"{{{NS}}}InvoiceSummary")
    tax_amount = _q(net_value_total * tax_percent / Decimal("100"))
    gross_value = _q(net_value_total + tax_amount)

    totals = etree.SubElement(summary, f"{{{NS}}}InvoiceTotals")
    _el(etree.SubElement(totals, f"{{{NS}}}InvoiceNetValue"), "Amount", f"{net_value_total:.2f}")
    _el(etree.SubElement(totals, f"{{{NS}}}InvoiceGrossValue"), "Amount", f"{gross_value:.2f}")
    _el(etree.SubElement(totals, f"{{{NS}}}InvoiceTaxAmount"), "Amount", f"{tax_amount:.2f}")
    _el(etree.SubElement(totals, f"{{{NS}}}InvoiceAmountPayable"), "Amount", f"{gross_value:.2f}")

    tax_totals = etree.SubElement(summary, f"{{{NS}}}TaxTotals")
    tax_total_el = etree.SubElement(tax_totals, f"{{{NS}}}Tax")
    _el(tax_total_el, "TaxCode", "VAT")
    _el(tax_total_el, "Percent", f"{tax_percent:.0f}" if tax_percent == tax_percent.to_integral_value() else f"{tax_percent}")
    _el(tax_total_el, "Amount", f"{tax_amount:.2f}")
    _el(tax_total_el, "TaxableAmount", f"{net_value_total:.2f}")

    tax_desc = etree.SubElement(summary, f"{{{NS}}}TaxCodeDescription")
    _el(tax_desc, "TaxCode", "VAT")
    _el(tax_desc, "TaxDescription", f"Value added tax {tax_percent:.0f}%")

    return etree.tostring(root, pretty_print=True, xml_declaration=True, encoding="UTF-8")
