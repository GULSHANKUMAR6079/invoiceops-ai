from collections import Counter
from decimal import ROUND_HALF_UP, Decimal, localcontext

from .db import utcnow
from .schemas import Discrepancy, Extraction, PurchaseOrder, ReconciliationResult, Rules, State, Vendor

MINOR_UNITS = {"USD": 2, "EUR": 2, "GBP": 2, "INR": 2, "CAD": 2, "AUD": 2, "JPY": 0}
ZERO = Decimal(0)
MANDATORY = ("invoice_number", "invoice_date", "vendor_name", "purchase_order_number",
             "currency", "subtotal", "tax_amount", "total_amount", "line_items")


def money(value, currency):
    with localcontext(prec=40):
        return value.quantize(Decimal(1).scaleb(-MINOR_UNITS[currency]), rounding=ROUND_HALF_UP)


def order_total(order):
    with localcontext(prec=40):
        subtotal = sum((money(line.quantity * line.unit_price, order.currency) for line in order.lines), ZERO)
        return subtotal + money(subtotal * order.tax_rate, order.currency)


def within(actual, expected, absolute, percent):
    return abs(actual - expected) <= max(absolute, abs(expected) * percent / 100)


def normalized(value):
    return " ".join(value.casefold().split())


def reconcile(invoice_id: str, extraction: Extraction, order: PurchaseOrder | None,
              vendor: Vendor | None, duplicate: bool, rules: Rules, version: int) -> ReconciliationResult:
    invoice = extraction.invoice
    issues = []

    def flag(code, field, explanation, actual=None, expected=None):
        issues.append(Discrepancy(code=code, field=field, explanation=explanation,
                                  actual=str(actual) if actual is not None else None,
                                  expected=str(expected) if expected is not None else None))

    def amount(code, field, actual, expected, explanation, configurable=False):
        absolute = rules.amount_absolute if configurable else ZERO
        percent = rules.amount_percent if configurable else ZERO
        if not within(actual, expected, absolute, percent):
            flag(code, field, explanation, actual, expected)

    for field in MANDATORY:
        if getattr(invoice, field) in (None, []):
            flag("MISSING_FIELD", field, "A required invoice value is unavailable.")
    if extraction.confidence < rules.minimum_confidence or any(
        item.status in {"conflicting", "unreadable"} for item in extraction.fields.values()
    ):
        flag("EXTRACTION_UNCERTAIN", "extraction", "Verify uncertain fields against the PDF.")
    if duplicate:
        flag("DUPLICATE_INVOICE", "invoice_number", "This vendor and invoice number are already registered.")
    if invoice.purchase_order_number and order is None:
        flag("PO_NOT_FOUND", "purchase_order_number", "No purchase order has this reference.",
             invoice.purchase_order_number)
    if vendor is None and (invoice.vendor_name or invoice.vendor_id):
        flag("UNKNOWN_VENDOR", "vendor_id", "Confirm the supplier against the vendor master.")
    if vendor and invoice.vendor_name and normalized(vendor.name) != normalized(invoice.vendor_name):
        flag("VENDOR_NAME_MISMATCH", "vendor_name", "The supplier name differs from the vendor master.",
             invoice.vendor_name, vendor.name)
    if order and vendor and vendor.vendor_id != order.vendor_id:
        flag("VENDOR_MISMATCH", "vendor_id", "The supplier differs from the purchase order.",
             vendor.vendor_id, order.vendor_id)

    currency = invoice.currency
    supported = currency in MINOR_UNITS
    comparable = bool(order and currency == order.currency and supported)
    if currency and not supported:
        flag("UNSUPPORTED_CURRENCY", "currency", "This currency has no configured rounding policy.", currency)
    if order and currency and currency != order.currency:
        flag("CURRENCY_MISMATCH", "currency", "The invoice and order use different currencies.", currency, order.currency)

    by_code = {normalized(line.product_code): line for line in order.lines} if order else {}
    by_name = {}
    if order:
        for line in order.lines:
            by_name.setdefault(normalized(line.description), []).append(line)
    seen = Counter()
    for index, line in enumerate(invoice.line_items, 1):
        field = f"line_items[{index}]"
        for name in ("description", "quantity", "unit_price", "line_total"):
            if getattr(line, name) is None:
                flag("MISSING_FIELD", f"{field}.{name}", "A required line value is unavailable.")
        if line.quantity == 0:
            flag("INVALID_QUANTITY", field, "An invoiced quantity must be positive.", line.quantity)
        if supported and None not in (line.quantity, line.unit_price, line.line_total):
            amount("LINE_ARITHMETIC", field, line.line_total, money(line.quantity * line.unit_price, currency),
                   "The line total must equal quantity times unit price, rounded to the minor unit.")
        if not order:
            continue
        candidates = by_name.get(normalized(line.description), []) if line.description else []
        expected = (by_code.get(normalized(line.product_code)) if line.product_code
                    else candidates[0] if len(candidates) == 1 else None)
        if not expected:
            flag("UNMATCHED_LINE", field, "This line does not identify a unique order line.",
                 line.product_code or line.description)
            continue
        seen[expected.product_code] += 1
        if seen[expected.product_code] > 1:
            flag("DUPLICATE_LINE", field, "Multiple invoice lines identify the same order line.", expected.product_code)
        if line.quantity is not None and not within(line.quantity, expected.quantity,
                                                   rules.quantity_absolute, rules.quantity_percent):
            flag("QUANTITY_MISMATCH", field, "The invoiced quantity differs from the order.",
                 line.quantity, expected.quantity)
        if comparable and line.unit_price is not None:
            amount("UNIT_PRICE_MISMATCH", field, line.unit_price, expected.unit_price,
                   "The invoiced unit price differs from the order.", True)
    if order and invoice.line_items:
        for code in sorted(line.product_code for line in order.lines if not seen[line.product_code]):
            flag("MISSING_ORDER_LINE", "line_items", "A purchase order line is absent from the invoice.", expected=code)

    if supported and invoice.subtotal is not None:
        if invoice.line_items and all(line.line_total is not None for line in invoice.line_items):
            amount("SUBTOTAL_ARITHMETIC", "subtotal", invoice.subtotal,
                   money(sum((line.line_total for line in invoice.line_items), ZERO), currency),
                   "The subtotal must equal the sum of the line totals.")
        if comparable:
            if invoice.tax_amount is not None:
                amount("TAX_MISMATCH", "tax_amount", invoice.tax_amount,
                       money(invoice.subtotal * order.tax_rate, currency),
                       "The tax differs from the order tax rate applied to the invoice subtotal.")
            if invoice.total_amount is not None:
                amount("ORDER_TOTAL_MISMATCH", "total_amount", invoice.total_amount,
                       order_total(order),
                       "The invoice total differs from the full purchase order total.", True)
        if invoice.tax_amount is not None and invoice.total_amount is not None:
            amount("TOTAL_ARITHMETIC", "total_amount", invoice.total_amount,
                   money(invoice.subtotal + invoice.tax_amount, currency),
                   "The total must equal subtotal plus tax.")
    if supported:
        for field in ("subtotal", "tax_amount", "total_amount"):
            value = getattr(invoice, field)
            if value is not None and money(value, currency) != value:
                flag("CURRENCY_PRECISION", field, "Amount exceeds the currency minor-unit precision.", value)

    return ReconciliationResult(
        invoice_id=invoice_id, matched_po_id=order.number if order else None,
        status=State.REVIEW_REQUIRED if issues else State.MATCHED,
        exception_codes=list(dict.fromkeys(issue.code for issue in issues)),
        discrepancy_details=issues, evidence_references=extraction.fields, rule_version=version,
        decision_explanation="Human review required." if issues else "Invoice passed the configured two-way checks.",
        processing_timestamp=utcnow(),
    )

