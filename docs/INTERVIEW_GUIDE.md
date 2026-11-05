# Interview guide

## Walkthrough

1. Upload `sample-00001.pdf` to show a clean match, evidence excerpts, and the linked order.
2. Upload `sample-00003.pdf` to show both an invoice-to-PO total difference and an internal arithmetic error. Open the original preview and examine the observed and expected amounts.
3. Correct the total to subtotal plus tax and record a reason. Show the original extraction, new reconciliation and policy version.
4. Approve that revision. Upload `sample-00004.pdf` and reject it with a reason. Show both actions in the audit history.
5. Open the evaluation view and explain what the seen and held-out layouts measure. Use the saved failure cases to illustrate the baseline’s weaknesses.

## Technical discussion

- **Business problem.** AP staff need to find meaningful mismatches and explain financial review decisions. Document extraction reduces rekeying. Deterministic checks and approvals preserve review accountability.
- **Why FastAPI and Pydantic?** Typed request and response validation, generated OpenAPI, and straightforward testing. Pydantic validates an external extraction response before it reaches the rules.
- **Why SQLite and PostgreSQL?** SQLite makes the offline demo portable. PostgreSQL is the migration target for higher write concurrency, multiple workers and production operations.
- **Why consider an LLM?** OCR recovers text and rules work well when structure is stable. A structured model can interpret unfamiliar labels and layouts. The live adapter exists. The benchmark makes no claim that it beats the baseline.
- **Why deterministic finance?** Decimal arithmetic, PO matching, versioned tolerances, and duplicate checks can be replayed and explained. Confidence controls when a reviewer sees uncertainty. The reviewer records the decision.
- **Why no orchestration framework?** The current workflow fits explicit transitions and SQL transactions. If jobs wait on ERP callbacks or approvals across workers, durable graph checkpoints or a job engine become more useful.
- **Failures.** Unreadable PDFs move to Processing failed with a reason. Provider timeout has one bounded retry. Invalid structured output fails closed. Database contention is retryable. Conflicting revisions cannot both commit.
- **Injection.** The prompt treats page text as data. The provider has no tools or financial authority. Evidence must exist on the given page and recognizable baseline values must agree. This reduces some attack paths. Semantic spoofing remains a risk.
- **Metrics.** A fixed-seed manifest has independent labels. The runner writes and reloads predictions before scoring. Exception scoring uses document-code pairs. Latency uses the documented serial workload. See [evaluation](EVALUATION_REPORT.md).
- **Cost.** Offline extraction incurs zero API fees. The optional live evaluation estimates token cost using supplied prices and observed usage. Infrastructure, failed requests with unavailable usage, and reviewer time need separate measurement.
- **Scaling.** Use Postgres, isolated workers, bounded queues, private object storage, retry and idempotency keys, and separate PDF processing from the write transaction. Load-test with concurrent workloads before making a throughput claim.
- **ERP and customer adaptation.** Use a connector with external supplier, order and receipt IDs, replay-safe sync, and a reconciliation view of source snapshots. Validate tax and rounding policies, tolerance rules, approval authority, SLA and data residency with the customer.

