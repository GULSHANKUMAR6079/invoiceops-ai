# Engineering decisions

| Decision | Rationale and trade-off |
| --- | --- |
| One FastAPI service and React dashboard | The PDF, review queue, and policy share a database and workflow. A single process is easy to run locally. |
| SQLAlchemy and Alembic | Transactions, uniqueness and foreign keys protect critical operations. SQLite is the verified local path. The configured PostgreSQL path uses psycopg and the same migrations. |
| Explicit state machine | A small transition table, optimistic revision and transactional audit cover the current workflow. LangGraph becomes useful if extraction, external lookups and human interrupts must resume independently. |
| Deterministic baseline and separate providers | Offline runs extract actual PDF text. Fixture mode serves reference outputs only for recognized hashes and is excluded from evaluation. Groq uses structured outputs and source checks. |
| Versioned deterministic rules | Rules compare Decimal values. The allowed absolute difference is the larger of the absolute tolerance and the expected value times the percentage tolerance. Quantity, unit price and order total may use these settings. Arithmetic and PO tax checks remain strict. |
| Explicit currency policy | Round half up by currency at the line extension and tax calculation. USD, EUR, GBP, INR, CAD and AUD use two minor digits. JPY uses zero. Unknown currency codes and precision violations require review. |
| Prefer product code | Use the product code when supplied. Otherwise use a unique normalized description. Duplicate or unrecognized lines and absent order lines surface separately. |
| Authoritative vendor identity | A known ID wins lookup, then the vendor name is checked against the master. A missing ID can resolve through a unique name. A repeated name requires a vendor ID. |
| Keep PDF bytes and audit in the database | One transaction preserves upload integrity and enables simple local backups. Larger deployments should use private object storage, retention policies, and a managed audit sink. |

## Security boundaries and production work

Personas come from a selectable `X-Demo-User` header. Every invoice API, including PDF preview and download, requires a recognized persona. Analysts and administrators can review. Only administrators can change rules or create orders. The demo has one shared workspace and accepts headers from any local caller, so run it on loopback and use fictional data.

Uploads check size, PDF MIME and signature. Parsing enforces page and extracted-text limits. The Groq adapter sends extracted page text to Groq only when selected, provides no tools, and tests whether evidence occurs on the stated page. Suspicious common instruction patterns trigger review in Groq mode. Literal evidence checks and pattern rules still permit semantic spoofing. Production needs verified identity, tenant boundaries, malware scanning, isolated PDF parsing with resource limits, encryption, data retention, tamper-evident logs, external-provider data controls, and operational monitoring.

An OCR protocol is available for dependency injection and has a simulated adapter test. A concrete OCR engine and representative scans are future work. Receipt-aware matching, credits and split-PO invoices are outside the current financial model.

