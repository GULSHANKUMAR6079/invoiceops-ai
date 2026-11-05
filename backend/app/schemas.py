from datetime import date, datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


Text = Annotated[str, StringConstraints(min_length=1, max_length=200)]
Reason = Annotated[str, StringConstraints(min_length=1, max_length=1000)]
Amount = Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=4, allow_inf_nan=False)]
Currency = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
Timestamp = Annotated[datetime, AfterValidator(lambda value: value.replace(tzinfo=timezone.utc)
                                                if value.tzinfo is None else value.astimezone(timezone.utc))]
FIELDS = (
    "invoice_number", "invoice_date", "vendor_name", "vendor_id", "purchase_order_number",
    "currency", "subtotal", "tax_amount", "total_amount", "line_items",
)


class Line(Schema):
    description: Text | None = None
    quantity: Amount | None = None
    unit_price: Amount | None = None
    line_total: Amount | None = None
    product_code: Text | None = None


class Invoice(Schema):
    invoice_number: Text | None = None
    invoice_date: date | None = None
    vendor_name: Text | None = None
    vendor_id: Text | None = None
    purchase_order_number: Text | None = None
    currency: Currency | None = None
    subtotal: Amount | None = None
    tax_amount: Amount | None = None
    total_amount: Amount | None = None
    line_items: list[Line] = Field(default_factory=list, max_length=100)


class Vendor(Schema):
    vendor_id: Text
    name: Text
    address: Text


class OrderLine(Schema):
    description: Text
    product_code: Text
    quantity: Amount
    unit_price: Amount

    @field_validator("quantity")
    @classmethod
    def positive(cls, value):
        if value <= 0:
            raise ValueError("Order quantity must be positive")
        return value


class PurchaseOrder(Schema):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "number": "PO-LOCAL-001", "vendor_id": "V-TRAIN-1", "currency": "USD", "tax_rate": "0.08",
        "lines": [{"description": "Packing sleeves", "product_code": "SKU-002", "quantity": "3",
                   "unit_price": "12.50"}],
    }]})
    number: Text
    vendor_id: Text
    currency: Currency
    tax_rate: Annotated[Decimal, Field(ge=0, le=1, decimal_places=4, allow_inf_nan=False)]
    lines: list[OrderLine] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_products(self):
        if len({" ".join(line.product_code.casefold().split()) for line in self.lines}) != len(self.lines):
            raise ValueError("Product codes must be unique within the order")
        return self


class Rules(Schema):
    amount_absolute: Amount = Decimal("0")
    amount_percent: Annotated[Amount, Field(le=100)] = Decimal("0")
    quantity_absolute: Amount = Decimal("0")
    quantity_percent: Annotated[Amount, Field(le=100)] = Decimal("0")
    minimum_confidence: Annotated[float, Field(ge=0, le=1)] = 0.9


class RuleUpdate(Schema):
    rules: Rules
    version: Annotated[int, Field(ge=1)]
    reason: Reason


class Evidence(Schema):
    page: Annotated[int, Field(ge=1)]
    excerpt: Annotated[str, StringConstraints(min_length=1, max_length=500)]


class FieldEvidence(Schema):
    status: Literal["extracted", "missing", "unreadable", "conflicting", "corrected"]
    sources: list[Evidence] = Field(default_factory=list)


class Extraction(Schema):
    invoice: Invoice
    fields: dict[str, FieldEvidence]
    provider: str
    confidence: Annotated[float, Field(ge=0, le=1)]
    warnings: list[str] = Field(default_factory=list)
    input_tokens: int | None = None
    output_tokens: int | None = None


class State(StrEnum):
    UPLOADED = "UPLOADED"
    EXTRACTING = "EXTRACTING"
    EXTRACTED = "EXTRACTED"
    RECONCILING = "RECONCILING"
    MATCHED = "MATCHED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    PROCESSING_FAILED = "PROCESSING_FAILED"


class Discrepancy(Schema):
    code: str
    field: str
    explanation: str
    actual: str | None = None
    expected: str | None = None


class ReconciliationResult(Schema):
    invoice_id: str
    matched_po_id: str | None
    status: Literal[State.MATCHED, State.REVIEW_REQUIRED, State.REJECTED, State.PROCESSING_FAILED]
    exception_codes: list[str]
    discrepancy_details: list[Discrepancy]
    decision_explanation: str
    evidence_references: dict[str, FieldEvidence]
    rule_version: int
    processing_timestamp: datetime


class Correction(Schema):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "fields": {"purchase_order_number": "PO-00001"},
        "version": 4, "reason": "Confirmed the order number on the PDF",
    }]})
    fields: Invoice
    version: Annotated[int, Field(ge=1)]
    reason: Reason

    @field_validator("fields")
    @classmethod
    def nonempty(cls, value):
        if not value.model_fields_set:
            raise ValueError("Supply at least one field")
        return value


class Decision(Schema):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "version": 4, "reason": "Confirmed the variance with procurement",
    }]})
    version: Annotated[int, Field(ge=1)]
    reason: Reason | None = None


class InvoiceSummary(Schema):
    id: str
    filename: str
    sha256: str
    size: int
    state: State
    version: int
    created_at: Timestamp
    updated_at: Timestamp
    invoice_number: str | None
    vendor_name: str | None
    exception_codes: list[str]
    processing_ms: float | None


class InvoiceDetail(InvoiceSummary):
    original: Extraction | None
    extraction: Extraction | None
    result: ReconciliationResult | None
    purchase_order: PurchaseOrder | None
    error: str | None
    duplicate_upload: bool = False


class AuditRecord(Schema):
    id: int
    invoice_id: str | None
    timestamp: Timestamp
    actor: str
    action: str
    previous_state: str | None
    new_state: str | None
    reason: str | None
    changes: dict | None


class Metrics(Schema):
    total_uploaded: int
    total_processed: int
    matched: int
    review_required: int
    approved: int
    rejected: int
    processing_failed: int
    average_processing_ms: float | None

