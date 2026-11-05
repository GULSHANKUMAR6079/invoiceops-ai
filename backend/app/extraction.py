import re
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import pymupdf
from pydantic import ValidationError

from .schemas import FIELDS, Evidence, Extraction, FieldEvidence, Invoice, Line

ALIASES = {
    "invoice_number": ("invoice no", "invoice number", "invoice #", "invoice ref"),
    "invoice_date": ("invoice date", "issued on", "date issued"),
    "vendor_name": ("vendor", "supplier", "supplier name", "vendor name"),
    "vendor_id": ("vendor id", "supplier id", "vendor code"),
    "purchase_order_number": ("po number", "purchase order", "order ref"),
    "currency": ("currency", "billing currency"),
    "subtotal": ("subtotal", "net amount", "net subtotal"),
    "tax_amount": ("tax", "tax amount", "sales tax"),
    "total_amount": ("total", "amount due", "invoice total"),
}
LABELS = {alias: field for field, aliases in ALIASES.items() for alias in aliases}
HEADER = re.compile(r"^\s*([^:=|]{2,40})\s*[:=|]\s*(.*?)\s*$")
ITEM = re.compile(r"^\s*item\s*[:=]\s*(.*?)\s*$", re.IGNORECASE)
NUMBER = re.compile(r"^(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]+)?$")
DATE_FORMATS = ("%Y-%m-%d", "%d %b %Y", "%b %d, %Y", "%Y/%m/%d")


class ExtractionError(Exception):
    pass


@dataclass(frozen=True)
class PageText:
    page: int
    text: str


class OCR(Protocol):
    def read_page(self, pdf: bytes, page: int) -> str: ...


class Provider(Protocol):
    def extract(self, pdf: bytes, pages: list[PageText]) -> Extraction: ...


def read_pdf(pdf: bytes, max_pages: int = 20, ocr: OCR | None = None) -> list[PageText]:
    try:
        with pymupdf.open(stream=pdf, filetype="pdf") as document:
            if document.is_encrypted:
                raise ExtractionError("The PDF is encrypted. Upload an unlocked copy.")
            if not 1 <= document.page_count <= max_pages:
                raise ExtractionError(f"Upload a PDF with 1 to {max_pages} pages.")
            pages = [PageText(page.number + 1, page.get_text()) for page in document]
    except (pymupdf.FileDataError, RuntimeError) as exc:
        raise ExtractionError("The PDF is damaged or unreadable. Upload a readable copy.") from exc

    result = []
    for page in pages:
        if sum(char.isalnum() for char in page.text) >= 12 and "\ufffd" not in page.text:
            result.append(page)
            continue
        if ocr is None:
            raise ExtractionError(f"Page {page.page} has insufficient readable text. OCR is unavailable.")
        text = ocr.read_page(pdf, page.page)
        if sum(char.isalnum() for char in text) < 12 or "\ufffd" in text:
            raise ExtractionError(f"OCR could not read page {page.page}.")
        result.append(PageText(page.page, text))
    if sum(len(page.text) for page in result) > 60_000:
        raise ExtractionError("The PDF exceeds the extracted text limit.")
    return result


def parse_amount(value):
    if not NUMBER.fullmatch(value):
        raise ValueError("Unrecognized decimal")
    return value.replace(",", "")


def parse_field(field, value):
    if field == "invoice_date":
        for fmt in DATE_FORMATS:
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                continue
        raise ValueError("Unrecognized or ambiguous date")
    if field in {"subtotal", "tax_amount", "total_amount"}:
        return parse_amount(value)
    if field == "currency":
        return value.upper()
    return value


class Baseline:
    def extract(self, pdf: bytes, pages: list[PageText]) -> Extraction:
        candidates = {field: [] for field in FIELDS}
        for page in pages:
            for line in page.text.splitlines():
                if item := ITEM.fullmatch(line):
                    candidates["line_items"].append((item[1], Evidence(page=page.page, excerpt=line[:500])))
                elif header := HEADER.fullmatch(line):
                    field = LABELS.get(header[1].strip().casefold())
                    if field:
                        candidates[field].append((header[2], Evidence(page=page.page, excerpt=line[:500])))

        parsed = {}
        evidence = {}
        for field, entries in candidates.items():
            sources = [source for _, source in entries]
            values = []
            unreadable = False
            for raw, _ in entries:
                if not raw:
                    continue
                try:
                    if field == "line_items":
                        code, description, quantity, price, total = map(str.strip, raw.split("|"))
                        values.append(Line(description=description, product_code=code or None,
                                           quantity=parse_amount(quantity), unit_price=parse_amount(price),
                                           line_total=parse_amount(total)))
                    else:
                        value = parse_field(field, raw)
                        values.append(getattr(Invoice.model_validate({field: value}), field))
                except (ValueError, ValidationError):
                    unreadable = True
            if field != "line_items" and len(set(values)) > 1:
                status = "conflicting"
            elif unreadable or (field == "line_items" and len(values) > 100):
                status = "unreadable"
            elif values:
                status = "extracted"
                parsed[field] = values if field == "line_items" else values[0]
            else:
                status = "missing"
            evidence[field] = FieldEvidence(status=status, sources=sources)
        flags = [f"{field}: {item.status}" for field, item in evidence.items()
                 if item.status in {"unreadable", "conflicting"}]
        return Extraction(invoice=Invoice.model_validate(parsed), fields=evidence,
                          provider="baseline", confidence=0.5 if flags else 1.0, warnings=flags)

