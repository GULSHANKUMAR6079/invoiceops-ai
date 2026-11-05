import hashlib
import re
from pathlib import PurePath
from time import perf_counter
from uuid import uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from .db import Audit, Document, InvoiceIdentity, OrderRow, RuleRow, VendorRow, utcnow
from .extraction import ExtractionError, read_pdf
from .reconciliation import normalized, reconcile
from .schemas import Extraction, FieldEvidence, Invoice, InvoiceDetail, InvoiceSummary
from .schemas import PurchaseOrder, ReconciliationResult, Rules, State, Vendor

TRANSITIONS = {
    State.UPLOADED: {State.EXTRACTING},
    State.EXTRACTING: {State.EXTRACTED, State.PROCESSING_FAILED},
    State.EXTRACTED: {State.RECONCILING},
    State.RECONCILING: {State.MATCHED, State.REVIEW_REQUIRED},
    State.MATCHED: {State.RECONCILING, State.APPROVED, State.REJECTED},
    State.REVIEW_REQUIRED: {State.RECONCILING, State.APPROVED, State.REJECTED},
    State.PROCESSING_FAILED: {State.EXTRACTING},
}
DECIDABLE = {State.MATCHED, State.REVIEW_REQUIRED}
RECONCILABLE = {*DECIDABLE, State.EXTRACTED}


class Conflict(Exception):
    pass


def json_model(model):
    return model.model_dump(mode="json")


def audit(session, actor, action, invoice_id=None, previous=None, new=None, reason=None, changes=None):
    session.add(Audit(actor=actor, action=action, invoice_id=invoice_id, previous_state=previous,
                      new_state=new, reason=reason, changes=changes))


def transition(session, doc, target, actor, action, reason=None, changes=None):
    previous = State(doc.state)
    if target not in TRANSITIONS.get(previous, set()):
        raise Conflict(f"{action} is unavailable in {previous.value}.")
    doc.state = target.value
    audit(session, actor, action, doc.id, previous.value, target.value, reason, changes)


def reserve(session, doc, expected=None):
    current = doc.version if expected is None else expected
    result = session.execute(update(Document).where(Document.id == doc.id, Document.version == current)
                             .values(version=current + 1, updated_at=utcnow())
                             .execution_options(synchronize_session=False))
    if result.rowcount != 1:
        raise Conflict("The invoice changed. Refresh before trying again.")
    session.refresh(doc)


def upload(session, filename, content, actor):
    digest = hashlib.sha256(content).hexdigest()
    existing = session.scalar(select(Document).where(Document.sha256 == digest))
    if existing:
        return existing, True
    safe = PurePath(filename.replace("\\", "/")).name
    safe = re.sub(r"[^a-zA-Z0-9_. -]", "_", safe).strip(" .") or "invoice.pdf"
    doc = Document(id=str(uuid4()), filename=safe[:200], sha256=digest, size=len(content),
                   pdf=content, state=State.UPLOADED.value)
    try:
        with session.begin_nested():
            session.add(doc)
            session.flush()
    except IntegrityError:
        existing = session.scalar(select(Document).where(Document.sha256 == digest))
        if existing:
            return existing, True
        raise
    audit(session, actor, "upload", doc.id, new=doc.state)
    return doc, False


def extract_document(session, doc, actor, provider, max_pages, ocr=None):
    if doc.state not in {State.UPLOADED, State.PROCESSING_FAILED}:
        if doc.extraction:
            return
        raise Conflict(f"Extraction is unavailable in {doc.state}.")
    started = perf_counter()
    reserve(session, doc)
    transition(session, doc, State.EXTRACTING, actor, "start_extraction")
    try:
        extraction = provider.extract(doc.pdf, read_pdf(doc.pdf, max_pages, ocr))
    except ExtractionError as exc:
        doc.error = str(exc)
        doc.processing_ms = (perf_counter() - started) * 1000
        rule_version, _ = current_rules(session)
        doc.result = json_model(ReconciliationResult(
            invoice_id=doc.id, matched_po_id=None, status=State.PROCESSING_FAILED,
            exception_codes=["EXTRACTION_FAILED"], discrepancy_details=[], decision_explanation=doc.error,
            evidence_references={}, rule_version=rule_version, processing_timestamp=utcnow(),
        ))
        transition(session, doc, State.PROCESSING_FAILED, actor, "extraction_failed", reason=doc.error)
        return

    doc.original = json_model(extraction)
    doc.extraction = doc.original
    doc.error = None
    doc.result = None
    doc.processing_ms = None
    doc.extraction_ms = (perf_counter() - started) * 1000
    transition(session, doc, State.EXTRACTED, actor, "finish_extraction")


def find_vendor(session, invoice):
    if invoice.vendor_id:
        vendor = session.get(VendorRow, invoice.vendor_id)
        return Vendor.model_validate(vendor.data) if vendor else None
    if invoice.vendor_name:
        matches = [row.data for row in session.scalars(select(VendorRow))
                   if normalized(row.data["name"]) == normalized(invoice.vendor_name)]
        if len(matches) == 1:
            return Vendor.model_validate(matches[0])
    return None


def register_identity(session, invoice_id, vendor, number):
    key = (vendor.vendor_id, normalized(number)) if vendor and number else None
    claimed = session.scalar(select(InvoiceIdentity).where(InvoiceIdentity.invoice_id == invoice_id))
    if claimed:
        old_key = (claimed.vendor_id, claimed.invoice_number)
        if old_key == key:
            return False
        session.execute(delete(InvoiceIdentity).where(InvoiceIdentity.invoice_id == invoice_id))
    if key is None:
        return False
    if session.get(InvoiceIdentity, key):
        return True
    try:
        with session.begin_nested():
            session.add(InvoiceIdentity(vendor_id=key[0], invoice_number=key[1], invoice_id=invoice_id))
            session.flush()
    except IntegrityError:
        if session.get(InvoiceIdentity, key):
            return True
        raise
    return False


def current_rules(session):
    row = session.get(RuleRow, 1)
    return (row.version, Rules.model_validate(row.data)) if row else (1, Rules())


def reconcile_document(session, doc, actor, *, reserved=False):
    if doc.state not in RECONCILABLE:
        raise Conflict(f"Reconciliation is unavailable in {doc.state}.")
    if not reserved:
        reserve(session, doc)
    started = perf_counter()
    transition(session, doc, State.RECONCILING, actor, "start_reconciliation")
    extraction = Extraction.model_validate(doc.extraction)
    invoice = extraction.invoice
    reference = invoice.purchase_order_number
    order_row = session.get(OrderRow, reference) if reference else None
    order = PurchaseOrder.model_validate(order_row.data) if order_row else None
    vendor = find_vendor(session, invoice)
    version, rules = current_rules(session)
    duplicate = register_identity(session, doc.id, vendor, invoice.invoice_number)
    result = reconcile(doc.id, extraction, order, vendor, duplicate, rules, version)
    doc.result = json_model(result)
    doc.order_snapshot = json_model(order) if order else None
    doc.processing_ms = (doc.extraction_ms or 0) + (perf_counter() - started) * 1000
    transition(session, doc, result.status, actor, "finish_reconciliation",
               changes={"exception_codes": result.exception_codes, "rule_version": version})


def correct(session, doc, request, actor):
    if doc.state not in RECONCILABLE:
        raise Conflict(f"Corrections are unavailable in {doc.state}.")
    reserve(session, doc, request.version)
    extraction = Extraction.model_validate(doc.extraction)
    previous = json_model(extraction.invoice)
    edited = request.fields.model_dump(mode="json", exclude_unset=True)
    extraction.invoice = Invoice.model_validate({**previous, **edited})
    for field in edited:
        sources = extraction.fields.get(field, FieldEvidence(status="missing")).sources
        extraction.fields[field] = FieldEvidence(status="corrected", sources=sources)
    if extraction.provider == "baseline":
        extraction.warnings = [f"{field}: {item.status}" for field, item in extraction.fields.items()
                               if item.status in {"unreadable", "conflicting"}]
        extraction.confidence = 0.5 if extraction.warnings else 1.0
    doc.extraction = json_model(extraction)
    audit(session, actor, "correct_fields", doc.id, doc.state, doc.state, request.reason,
          {"before": {field: previous[field] for field in edited},
           "after": {field: json_model(extraction.invoice)[field] for field in edited}})
    reconcile_document(session, doc, actor, reserved=True)


def decide(session, doc, target, request, actor):
    action = "approve" if target == State.APPROVED else "reject"
    if doc.state == target:
        last = session.scalar(select(Audit).where(Audit.invoice_id == doc.id, Audit.action == action)
                              .order_by(Audit.id.desc()).limit(1))
        if last and last.actor == actor and last.reason == request.reason and doc.version == request.version + 1:
            return
    if doc.state not in DECIDABLE:
        raise Conflict(f"{action} is unavailable in {doc.state}.")
    if (target == State.REJECTED or doc.state == State.REVIEW_REQUIRED) and not request.reason:
        raise Conflict("Provide a reason for rejection or exception override.")
    if target == State.APPROVED and "DUPLICATE_INVOICE" in doc.result["exception_codes"]:
        raise Conflict("Resolve the duplicate identity before approving.")
    reserve(session, doc, request.version)
    transition(session, doc, target, actor, action, request.reason)


def summary(doc):
    fields = (doc.extraction or {}).get("invoice", {})
    return InvoiceSummary(
        id=doc.id, filename=doc.filename, sha256=doc.sha256, size=doc.size, state=doc.state,
        version=doc.version, created_at=doc.created_at, updated_at=doc.updated_at,
        invoice_number=fields.get("invoice_number"), vendor_name=fields.get("vendor_name"),
        exception_codes=(doc.result or {}).get("exception_codes", []), processing_ms=doc.processing_ms,
    )


def detail(doc, duplicate=False):
    return InvoiceDetail(**summary(doc).model_dump(), original=doc.original, extraction=doc.extraction,
                         result=doc.result, purchase_order=doc.order_snapshot, error=doc.error,
                         duplicate_upload=duplicate)

