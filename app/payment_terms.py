"""
Parses Priority's free-text payment-conditions string into the structured
fields the TXML PaymentTerms block needs (net days, discount percent,
discount days).

This is the one field in the whole pipeline that is NOT structured at the
source, so parsing it is inherently a best-effort operation. If the text does
not match a known pattern, ParseError is raised and the caller must route the
file to the error folder rather than guess at legally-binding payment terms.

Known pattern (seen in real Priority exports from Motorad), e.g.:
    "60 day net/30 day with 3% cash discount"
  -> net_days=60, discount_days=30, discount_percent=3.0

Add more patterns to PATTERNS as real-world variants are discovered.
"""
import re


class PaymentTermsParseError(ValueError):
    pass


PATTERNS = [
    # "60 day net/30 day with 3% cash discount"
    re.compile(
        r"(?P<net_days>\d+)\s*day\s*net\s*/\s*(?P<discount_days>\d+)\s*day\s*with\s*"
        r"(?P<discount_percent>\d+(?:\.\d+)?)\s*%\s*cash\s*discount",
        re.IGNORECASE,
    ),
    # "net 60 days" with no discount clause at all
    re.compile(
        r"^\s*net\s*(?P<net_days>\d+)\s*days?\s*$",
        re.IGNORECASE,
    ),
    # "3% within 30 days, net 60" (a more conventional phrasing, in case it
    # shows up for a different customer)
    re.compile(
        r"(?P<discount_percent>\d+(?:\.\d+)?)\s*%\s*(?:within|discount)\s*"
        r"(?P<discount_days>\d+)\s*days?.*?net\s*(?P<net_days>\d+)",
        re.IGNORECASE,
    ),
]


def parse_payment_conditions(text):
    """Returns a dict with keys: net_days (int), discount_days (int or None),
    discount_percent (float or None). Raises PaymentTermsParseError if no
    known pattern matches.
    """
    if not text or not text.strip():
        raise PaymentTermsParseError("Empty PAYMENT_CONDITIONS value")

    cleaned = text.strip()
    for pattern in PATTERNS:
        m = pattern.match(cleaned) or pattern.search(cleaned)
        if m:
            gd = m.groupdict()
            net_days = int(gd["net_days"]) if gd.get("net_days") else None
            discount_days = int(gd["discount_days"]) if gd.get("discount_days") else None
            discount_percent = float(gd["discount_percent"]) if gd.get("discount_percent") else None
            if net_days is None:
                raise PaymentTermsParseError(f"Could not determine net days from: {text!r}")
            return {
                "net_days": net_days,
                "discount_days": discount_days,
                "discount_percent": discount_percent,
                "raw_text": text,
            }

    raise PaymentTermsParseError(f"Unrecognized payment terms format: {text!r}")
