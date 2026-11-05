# Resume evidence

All figures below come from the fixed-seed synthetic test split of 100 invoices. The complete generated corpus contains 500 invoices. The benchmark is offline and measures workflow behavior on this corpus.

| Claim | Comparison | Verified value | Formula and sample |
| --- | --- | --- | --- |
| Documents evaluated | Corpus and scored split | 500 generated, 100 evaluated | Count of generated documents and saved test predictions |
| Field extraction F1 | Baseline parser | 0.945 | `2×883 / (2×883+0+102)`, 985 labeled-present field slots |
| Exception recall | Default rule replay | 92.7% | `102/110` labeled document-code pairs |
| Reconciliation accuracy | Duplicate detection off vs on | 82.0% → 85.0% | Correct predicted workflow outcomes / 100 |
| Duplicate recall | Duplicate detection off vs on | 0/5 → 5/5 | Duplicate labels found / five annotated duplicates |
| False approval proxy | Duplicate detection off vs on | 3/70 → 0/70 | Matched outcomes among records expected to require intervention |
| Workflow latency | Serial SQLite baseline | 17.16 ms p50, 22.93 ms p95 | Percentiles over 100 timed uploads, extractions and reconciliations |
| Manual review | Serial SQLite baseline | 80.0% | 80/100 Review required, with failures counted separately |
| Live LLM comparison | Baseline vs Groq | NOT EVALUATED | A configured Groq run is required |

Run `python -m scripts.generate_dataset --count 500 --seed 4317` and `python -m scripts.run_evaluation` to reproduce these rows. Latency depends on local hardware and workload. The false approval proxy measures a machine classification before any human decision. See [the evaluation report](EVALUATION_REPORT.md) for evidence and limits.

## ATS-ready bullets

- Built a FastAPI and React accounts-payable application with PDF extraction, Decimal-based purchase-order reconciliation, versioned business rules, and auditable human approval.
- Generated a 500-invoice synthetic benchmark and evaluated 100 held-out invoices, measuring 0.945 field extraction micro-F1 and 92.7% exception-code recall.
- Implemented vendor-scoped invoice deduplication, improving decision accuracy from 82.0% to 85.0% and reducing false matches from 3 to 0 among 70 intervention cases in a controlled ablation.

