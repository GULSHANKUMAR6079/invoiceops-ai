# InvoiceOps AI

**Intelligent Invoice Reconciliation & Exception Resolution**

InvoiceOps AI gives accounts payable staff one workspace to compare invoice PDFs against purchase orders, correct extracted values, and record the reasoning behind approval and rejection decisions. It runs locally on CPU with SQLite and a deterministic PDF parser.

```mermaid
flowchart LR
    A[Invoice PDF] --> B[Ingest and fingerprint]
    B --> C[Read page text]
    C --> D[Baseline or optional Groq]
    D --> E[Validate fields and evidence]
    PO[(Vendor and PO records)] --> F[Decimal reconciliation]
    E --> F
    F --> G[Human review and decision]
    G --> H[(Audit log)]
```

- **Deterministic.** PDF parsing, schema validation, purchase order matching, currency and tax checks, duplicate detection, and workflow transitions.
- **AI-assisted.** Optional Groq structured extraction with page evidence and uncertainty checks.
- **Human.** An analyst reviews discrepancies, corrects values, and records an approval or rejection. Approval ends the local review workflow.

## Run on Windows

From the project root with Python 3.12 and Node.js installed:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-dev.txt
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m scripts.generate_dataset
.\.venv\Scripts\python.exe -m scripts.seed_database

Push-Location frontend
npm.cmd ci
npm.cmd run build
Pop-Location

.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

Open [the dashboard](http://127.0.0.1:8000) and [interactive API docs](http://127.0.0.1:8000/docs). On macOS or Linux, use `.venv/bin/python` and `npm` from the corresponding directories. For frontend development, keep the API running and run `npm run dev` from `frontend`.

### Demo walkthrough

1. In **Upload**, select `data/synthetic/pdfs/sample-00001.pdf`. It should match `PO-00001`.
2. Upload `sample-00003.pdf`. Check the order-total and internal-arithmetic differences, then open the PDF and source excerpts.
3. Correct the invoice total to subtotal plus tax and enter a correction reason. The app reruns reconciliation. Approve the matched revision or use the reasoned override on a review-required record.
4. Upload `sample-00004.pdf` and reject it with a reason. Open **Audit & evaluation** to see the transitions and benchmark.
5. Upload `sample-00002.pdf` after `sample-00001.pdf` to see business duplicate detection across distinct PDF files.

Use the selector to try **AP Analyst**, **Finance Administrator**, or **Read-only Viewer**. These are local demonstration personas in a shared workspace. The Finance Administrator can update the amount, quantity and confidence policy under **Rule settings**.

| Overview | Exception review |
| --- | --- |
| ![Dashboard with real local workflow counts](docs/screenshots/overview.png) | ![PDF preview and recorded discrepancies](docs/screenshots/exception-review.png) |

[Audit and evaluation screenshot](docs/screenshots/audit-evaluation.png)

### API workflow

Send `X-Demo-User: analyst` for invoice calls or `X-Demo-User: admin` for policy and PO mutations. Upload with multipart field `file`, then call `POST /api/invoices/{id}/extract` and `POST /api/invoices/{id}/reconcile`. Inspect the document, order, and discrepancies with `GET /api/invoices/{id}`. Use `PATCH /api/invoices/{id}/extracted-fields` with `fields`, the current `version`, and a `reason`. Approve or reject with the current `version`. Rejection and overrides require a reason. See `GET /api/audit-logs?invoice_id={id}` for the history.

### Extraction modes and deployment

- `INVOICE_PROVIDER=baseline` reads text PDFs offline. This is the default.
- `INVOICE_PROVIDER=fixture` loads known extraction outputs from the generated manifest. It accepts only recognized file hashes and is excluded from evaluation.
- `INVOICE_PROVIDER=groq` sends extracted page text to Groq. Copy `.env.example` to `.env`, set `GROQ_API_KEY` and a strict structured-output `GROQ_MODEL`, then restart the app. Use authorized documents. [Groq structured outputs](https://console.groq.com/docs/structured-outputs)
- The default database is `sqlite:///invoiceops.db`. To use a PostgreSQL instance, set `DATABASE_URL=postgresql+psycopg://user:password@host:5432/invoiceops` before migrating and seeding.
- `docker compose up --build` defines the SQLite container path. The image generates and seeds fictional records on start and binds to loopback through Compose. Set `COMPOSE_DATABASE_URL` to use an external database. Container and PostgreSQL runtime verification are pending.

## Measured evaluation

The fixed-seed generator produces 500 fictional invoices, independent exception annotations and PO/vendor records. The scored split has 100 documents and separate supplier identities. Half use a held-out layout. The pipeline saves predictions before scoring.

| Baseline measurement | Held-out test split |
| --- | ---: |
| Field extraction micro-F1 | 0.945 |
| Decision accuracy | 85.0% |
| Exception-code recall | 92.7% |
| False matches among expected intervention | 0/70 |
| Manual review rate | 80.0% |
| Workflow latency p50 / p95 | 17.16 / 22.93 ms |
| Live Groq extraction | NOT EVALUATED |

Decision accuracy was 100.0% on seen layouts and 70.0% on held-out Dispatch. Without business duplicate detection, decision accuracy fell to 82.0% and 3 expected interventions were classified as matches. Unsupported item delimiters, corrupt PDFs, and conflicting references account for the extraction misses. [Evaluation report](docs/EVALUATION_REPORT.md) · [Failure analysis](docs/FAILURE_ANALYSIS.md)

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m scripts.run_evaluation
.\.venv\Scripts\python.exe -m scripts.benchmark_latency
```

The default evaluator uses no external API. To opt into Groq evaluation on the synthetic corpus, configure the key and pass `--groq --output data/runtime/groq-evaluation`. The runner accepts optional input and output prices per million tokens.

## Scope and future work

The current implementation uses two-way matching, local persona access, and a synchronous extraction path. Scanned PDFs require a concrete OCR adapter. Goods receipts, production identity, tenant separation, ERP synchronization, isolated PDF workers, and authorized real-invoice validation are future extensions.

[Product requirements](docs/PRD.md) · [Architecture](docs/ARCHITECTURE.md) · [Dataset](docs/DATASET.md) · [Engineering decisions](docs/ENGINEERING_DECISIONS.md) · [Interview guide](docs/INTERVIEW_GUIDE.md) · [Resume evidence](docs/RESUME_EVIDENCE.md)

