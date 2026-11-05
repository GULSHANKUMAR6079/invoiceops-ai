import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import pymupdf
from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from . import workflow as flow
from .auth import actor, require
from .config import Settings
from .db import Audit, Document, OrderRow, RuleRow, VendorRow, make_engine, make_sessions
from .providers import make_provider
from .reconciliation import MINOR_UNITS
from .schemas import AuditRecord, Correction, Decision, InvoiceDetail, InvoiceSummary, Metrics
from .schemas import PurchaseOrder, ReconciliationResult, Rules, RuleUpdate, State, Vendor

EDITOR = ("analyst", "admin")
Offset = Annotated[int, Query(ge=0)]
Limit = Annotated[int, Query(ge=1, le=100)]
Actor = Annotated[str, Depends(actor)]


def session_for(request: Request):
    with request.app.state.sessions() as session:
        yield session


DB = Annotated[Session, Depends(session_for)]


def get_doc(db, invoice_id):
    doc = db.get(Document, invoice_id)
    if doc is None:
        raise HTTPException(404, "Invoice not found.")
    return doc


def create_app(settings=None, *, provider=None, ocr=None):
    settings = settings or Settings.from_env()
    engine = make_engine(settings.database_url)
    extraction_provider = provider or make_provider(settings.provider, settings.fixture_manifest)

    @asynccontextmanager
    async def lifespan(_):
        yield
        engine.dispose()

    app = FastAPI(title="InvoiceOps AI", version="1.0.0", lifespan=lifespan)
    app.state.sessions = make_sessions(engine)
    app.state.engine = engine

    @app.middleware("http")
    async def bound_upload(request, call_next):
        if request.method == "POST" and request.url.path == "/api/invoices/upload":
            length = request.headers.get("content-length", "")
            if not length.isascii() or not length.isdecimal() or len(length) > 10:
                return JSONResponse({"detail": "Supply the upload Content-Length."}, status_code=411)
            if int(length) > settings.max_bytes + 65_536:
                return JSONResponse({"detail": "The PDF exceeds the upload size limit."}, status_code=413)
        return await call_next(request)

    @app.exception_handler(flow.Conflict)
    async def conflict(_, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(OperationalError)
    async def database_unavailable(_, __):
        return JSONResponse({"detail": "Database temporarily unavailable. Retry the operation."},
                            status_code=503, headers={"Retry-After": "1"})

    @app.get("/api/health")
    def health(db: DB):
        db.execute(text("SELECT 1"))
        db.execute(select(RuleRow.id).limit(1))
        return {"status": "ok", "provider": settings.provider, "auth": "local_demo"}

    @app.post("/api/invoices/upload", response_model=InvoiceDetail, status_code=201,
              responses={200: {"model": InvoiceDetail, "description": "PDF already exists"}})
    def upload(response: Response, user: Actor, db: DB, file: UploadFile = File(...)):
        require(user, *EDITOR)
        if file.content_type != "application/pdf" or not (file.filename or "").lower().endswith(".pdf"):
            raise HTTPException(415, "Upload a PDF with application/pdf content type.")
        content = file.file.read(settings.max_bytes + 1)
        if len(content) > settings.max_bytes:
            raise HTTPException(413, "The PDF exceeds the upload size limit.")
        if not content.startswith(b"%PDF-"):
            raise HTTPException(415, "The file does not have a PDF signature.")
        doc, duplicate = flow.upload(db, file.filename, content, user)
        db.commit()
        if duplicate:
            response.status_code = 200
        return flow.detail(doc, duplicate)

    @app.get("/api/invoices", response_model=list[InvoiceSummary])
    def list_invoices(user: Actor, db: DB, state: State | None = None, offset: Offset = 0, limit: Limit = 25):
        query = select(Document)
        if state:
            query = query.where(Document.state == state)
        query = query.order_by(Document.created_at.desc(), Document.id).offset(offset).limit(limit)
        return [flow.summary(doc) for doc in db.scalars(query)]

    @app.get("/api/invoices/{invoice_id}", response_model=InvoiceDetail)
    def invoice(invoice_id: str, user: Actor, db: DB):
        return flow.detail(get_doc(db, invoice_id))

    @app.get("/api/invoices/{invoice_id}/document")
    def original_pdf(invoice_id: str, user: Actor, db: DB):
        doc = get_doc(db, invoice_id)
        return Response(doc.pdf, media_type="application/pdf", headers={
            "Content-Disposition": f'attachment; filename="{doc.filename}"',
            "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store",
        })

    @app.get("/api/invoices/{invoice_id}/preview/{page_number}")
    def preview(invoice_id: str, page_number: int, user: Actor, db: DB):
        doc = get_doc(db, invoice_id)
        try:
            with pymupdf.open(stream=doc.pdf, filetype="pdf") as pdf:
                if pdf.is_encrypted or pdf.page_count > settings.max_pages:
                    raise HTTPException(422, "This PDF cannot be previewed.")
                if not 1 <= page_number <= pdf.page_count:
                    raise HTTPException(404, "PDF page not found.")
                page = pdf[page_number - 1]
                scale = min(2, 1100 / max(page.rect.width, page.rect.height, 1))
                image = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), annots=False).tobytes("png")
                count = pdf.page_count
        except (pymupdf.FileDataError, RuntimeError, ValueError) as exc:
            raise HTTPException(422, "This PDF cannot be previewed.") from exc
        return Response(image, media_type="image/png", headers={
            "Cache-Control": "no-store", "X-Page-Count": str(count), "X-Content-Type-Options": "nosniff",
        })

    @app.post("/api/invoices/{invoice_id}/extract", response_model=InvoiceDetail)
    def extract(invoice_id: str, user: Actor, db: DB):
        require(user, *EDITOR)
        doc = get_doc(db, invoice_id)
        flow.extract_document(db, doc, user, extraction_provider, settings.max_pages, ocr)
        db.commit()
        return flow.detail(doc)

    @app.post("/api/invoices/{invoice_id}/reconcile", response_model=InvoiceDetail)
    def reconcile(invoice_id: str, user: Actor, db: DB, rerun: bool = False):
        require(user, *EDITOR)
        doc = get_doc(db, invoice_id)
        if rerun or doc.state not in flow.DECIDABLE:
            flow.reconcile_document(db, doc, user)
        db.commit()
        return flow.detail(doc)

    @app.get("/api/invoices/{invoice_id}/exceptions", response_model=ReconciliationResult | None)
    def exceptions(invoice_id: str, user: Actor, db: DB):
        return get_doc(db, invoice_id).result

    @app.patch("/api/invoices/{invoice_id}/extracted-fields", response_model=InvoiceDetail)
    def correct(invoice_id: str, payload: Correction, user: Actor, db: DB):
        require(user, *EDITOR)
        doc = get_doc(db, invoice_id)
        flow.correct(db, doc, payload, user)
        db.commit()
        return flow.detail(doc)

    @app.post("/api/invoices/{invoice_id}/approve", response_model=InvoiceDetail)
    def approve(invoice_id: str, payload: Decision, user: Actor, db: DB):
        require(user, *EDITOR)
        doc = get_doc(db, invoice_id)
        flow.decide(db, doc, State.APPROVED, payload, user)
        db.commit()
        return flow.detail(doc)

    @app.post("/api/invoices/{invoice_id}/reject", response_model=InvoiceDetail)
    def reject(invoice_id: str, payload: Decision, user: Actor, db: DB):
        require(user, *EDITOR)
        doc = get_doc(db, invoice_id)
        flow.decide(db, doc, State.REJECTED, payload, user)
        db.commit()
        return flow.detail(doc)

    @app.get("/api/purchase-orders", response_model=list[PurchaseOrder])
    def list_orders(user: Actor, db: DB, offset: Offset = 0, limit: Limit = 25):
        return [row.data for row in db.scalars(select(OrderRow).order_by(OrderRow.id).offset(offset).limit(limit))]

    @app.get("/api/vendors", response_model=list[Vendor])
    def list_vendors(user: Actor, db: DB, offset: Offset = 0, limit: Limit = 25):
        return [row.data for row in db.scalars(select(VendorRow).order_by(VendorRow.id).offset(offset).limit(limit))]

    @app.post("/api/purchase-orders", response_model=PurchaseOrder, status_code=201)
    def create_order(payload: PurchaseOrder, user: Actor, db: DB):
        require(user, "admin")
        if db.get(VendorRow, payload.vendor_id) is None:
            raise HTTPException(422, "The supplier must exist in the vendor master.")
        if payload.currency not in MINOR_UNITS:
            raise HTTPException(422, "The order currency has no rounding policy.")
        db.add(OrderRow(id=payload.number, vendor_id=payload.vendor_id, data=flow.json_model(payload)))
        flow.audit(db, user, "create_purchase_order", changes={"number": payload.number})
        try:
            db.commit()
        except IntegrityError as exc:
            raise HTTPException(409, "This purchase order already exists.") from exc
        return payload

    @app.get("/api/rules")
    def get_rules(user: Actor, db: DB):
        version, rules = flow.current_rules(db)
        return {"version": version, "rules": rules}

    @app.put("/api/rules")
    def update_rules(payload: RuleUpdate, user: Actor, db: DB):
        require(user, "admin")
        version, previous = flow.current_rules(db)
        if payload.version != version:
            raise flow.Conflict("Rules changed. Refresh before saving.")
        serialized = flow.json_model(payload.rules)
        query = update(RuleRow).where(RuleRow.id == 1, RuleRow.version == version)
        if db.execute(query.values(version=version + 1, data=serialized)).rowcount != 1:
            raise flow.Conflict("Rules changed. Refresh before saving.")
        flow.audit(db, user, "update_rules", reason=payload.reason,
                   changes={"before": flow.json_model(previous), "after": serialized, "version": version + 1})
        db.commit()
        return {"version": version + 1, "rules": payload.rules}

    @app.get("/api/dashboard/metrics", response_model=Metrics)
    def metrics(user: Actor, db: DB):
        counts = dict(db.execute(select(Document.state, func.count()).group_by(Document.state)).all())
        processed, average = db.execute(select(func.count(Document.processing_ms),
                                               func.avg(Document.processing_ms))).one()
        return Metrics(total_uploaded=sum(counts.values()), total_processed=processed,
                       matched=counts.get(State.MATCHED, 0), approved=counts.get(State.APPROVED, 0),
                       rejected=counts.get(State.REJECTED, 0), review_required=counts.get(State.REVIEW_REQUIRED, 0),
                       processing_failed=counts.get(State.PROCESSING_FAILED, 0), average_processing_ms=average)

    @app.get("/api/audit-logs", response_model=list[AuditRecord])
    def logs(user: Actor, db: DB, invoice_id: str | None = None, offset: Offset = 0, limit: Limit = 25):
        query = select(Audit)
        if invoice_id:
            get_doc(db, invoice_id)
            query = query.where(Audit.invoice_id == invoice_id)
        query = query.order_by(Audit.id.desc()).offset(offset).limit(limit)
        return [AuditRecord.model_validate(row, from_attributes=True) for row in db.scalars(query)]

    @app.get("/api/evaluation")
    def evaluation(user: Actor):
        report = Path(settings.report)
        if not report.is_file():
            raise HTTPException(404, "Generate the evaluation report before viewing metrics.")
        return json.loads(report.read_text(encoding="utf-8"))

    if settings.frontend.joinpath("index.html").is_file():
        app.mount("/", StaticFiles(directory=settings.frontend, html=True), name="dashboard")
    return app


app = create_app()

