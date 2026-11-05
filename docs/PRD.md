# Product requirements

## Problem and users

Accounts payable staff rekey invoices, reconcile them against purchase orders, and investigate supplier and amount discrepancies. Manual work makes it difficult to explain how a reviewer reached a decision.

- **AP analyst.** Upload a PDF, inspect source excerpts, compare invoice and order lines, correct extraction errors, and record a decision.
- **Finance administrator.** Set tolerances, inspect the exception queue, monitor processing, and review audit history.
- **Read-only viewer.** Inspect records and history during the local role demonstration.

## User stories and acceptance

| Story | Acceptance criteria |
| --- | --- |
| Submit an invoice | A PDF within the size limit is saved with a SHA-256 identity. Resubmitting the same bytes returns the existing record. |
| Reconcile an invoice | A seeded matching invoice reaches Matched. A mismatched amount reaches Review required with observed and expected values. |
| Inspect and correct | The reviewer sees the original document, field evidence, and the linked order. A reasoned correction preserves the original extraction and reruns checks. |
| Record a decision | The analyst can approve or reject a current revision. Rejection and exception override require a reason. Competing decisions yield one terminal transition. |
| Manage policy | The administrator can update tolerances with a reason. The reconciliation result stores the policy version it used. |
| Review evidence | The audit log shows the actor, time, transition and reason. The evaluation view reads saved benchmark outputs. |

## Scope

The MVP handles digitally generated PDF invoices, vendor and PO master records, two-way matching, local personas, and a synthetic evaluation. Scanned-document OCR, multi-tenancy, ERP integrations, goods receipts, payment initiation, and production identity are outside the MVP. The optional Groq adapter assists extraction.

## Product success measures

Track field extraction F1, exception recall, false-match rate, manual review rate, processing latency, and the fraction of decisions with a recorded actor and revision. For a customer pilot, measure time from arrival to decision against the customer’s manual workflow. This repository has synthetic benchmark results and no customer pilot data.

## Assumptions

POs and vendor IDs are authoritative. Each invoice references at most one PO. Supplier names can repeat. Tax is checked against one PO rate. Local users share one workspace.

