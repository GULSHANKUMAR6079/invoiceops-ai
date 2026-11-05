from decimal import Decimal

import pymupdf
import pytest
from pydantic import ValidationError

from backend.app.extraction import Baseline, ExtractionError, PageText, read_pdf
from backend.app.reconciliation import money, reconcile, within
from backend.app.schemas import Invoice, PurchaseOrder, Rules


@pytest.mark.parametrize("fields", [
    {"total_amount": "-1"}, {"total_amount": "NaN"}, {"currency": "US"},
    {"invoice_date": "2025-02-30"}, {"unexpected": 4},
    {"line_items": [{"quantity": "1.00001"}]},
])
def test_invoice_rejects_invalid_fields(fields):
    with pytest.raises(ValidationError):
        Invoice.model_validate(fields)


def test_rounding_and_tolerance_boundaries():
    assert money(Decimal("12.345"), "USD") == Decimal("12.35")
    assert money(Decimal("12.5"), "JPY") == Decimal("13")
    assert within(Decimal("105"), Decimal("100"), Decimal("2"), Decimal("5"))
    assert not within(Decimal("105.01"), Decimal("100"), Decimal("2"), Decimal("5"))
    assert money(Decimal("9999999999999800000000000001"), "USD") == Decimal("9999999999999800000000000001.00")


def test_unknown_po_and_uncertain_extraction(dataset):
    root, rows = dataset
    pdf = (root / rows[0]["path"]).read_bytes()
    baseline = Baseline().extract(pdf, read_pdf(pdf))
    baseline.confidence = 0.1
    result = reconcile("test", baseline, None, None, False, Rules(), 1)
    assert {"EXTRACTION_UNCERTAIN", "PO_NOT_FOUND", "UNKNOWN_VENDOR"} <= set(result.exception_codes)


def test_sources_are_page_relative_and_status_distinct():
    extraction = Baseline().extract(b"", [
        PageText(1, "Invoice No: A-1\nInvoice Date: unrecognizable"),
        PageText(2, "Invoice No: A-2\nCurrency: usd"),
    ])
    assert extraction.invoice.invoice_number is None
    assert extraction.fields["invoice_number"].status == "conflicting"
    assert [source.page for source in extraction.fields["invoice_number"].sources] == [1, 2]
    assert extraction.fields["invoice_date"].status == "unreadable"
    assert extraction.fields["vendor_name"].status == "missing"
    assert extraction.invoice.currency == "USD"


def test_blank_pdf_ocr_boundary():
    with pymupdf.open() as doc:
        doc.new_page()
        raw = doc.tobytes()
    with pytest.raises(ExtractionError, match="OCR is unavailable"):
        read_pdf(raw)

    class DemoOCR:
        def read_page(self, pdf, page):
            assert pdf == raw and page == 1
            return "Invoice No: OCR-123"

    assert read_pdf(raw, ocr=DemoOCR())[0].text == "Invoice No: OCR-123"


def test_line_product_order_validation():
    line = {"description": "Test", "product_code": "SKU-1", "quantity": "2", "unit_price": "2.50"}
    with pytest.raises(ValidationError):
        PurchaseOrder(number="PO", vendor_id="V", currency="USD", tax_rate="0.08",
                      lines=[line, {**line, "product_code": "sku-1"}])

