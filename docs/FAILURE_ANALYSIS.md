# Failure analysis

| Observation | Evidence | Impact |
| --- | --- | --- |
| Held-out item syntax | The held-out Dispatch PDFs separate item columns with semicolons. The parser recognizes a pipe-delimited row. | It emitted no line items for readable Dispatch documents. Clean documents went to review, and some quantity and unit-price annotations went undetected. |
| Over-review | 15/30 clean invoices were sent to review. Many derived missing-field and uncertainty codes have no matching business annotation. | Default exception precision was 54.8%. Those flags are operationally actionable even when the invoice has no business discrepancy. |
| Ambiguous reference | Five cases contain two different values under the same reference label. | The parser abstained instead of selecting one. The intended truth contributes an extraction miss. |
| Corrupt documents | Five test files deliberately fail PDF decoding. | They enter Processing failed and can be retried after a provider or source change. Their intended fields count as extraction misses. |
| Relaxed tolerance | Exception-code recall fell from 92.7% to 68.2% with a 5-unit amount and 1-unit quantity tolerance. | Independent exceptions stayed visible through other checks. The same decision accuracy hid the loss of diagnostic detail. |
| Duplicate detection ablation | Disabling business duplicates produced 3 false matches. | A byte hash alone does not catch duplicate invoices delivered in different PDF files. |

Test precision, recall and error counts come from [saved predictions](../data/evaluation/baseline_predictions.jsonl) and [failure examples](../data/evaluation/baseline_failures.json). The benchmark treats intentional corrupt and ambiguous inputs as failures to extract intended values. Review and explicit failure keep those records out of automatic matching.

The live Groq, real-document, scanned-document, PostgreSQL runtime, and Docker runtime measurements are pending. A future iteration would use independently labeled new layouts to measure any parser changes. Similarity between synthetic fields makes the current headline extraction F1 sensitive to the dataset design.

