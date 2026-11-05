# Synthetic dataset

Generate the corpus with `python -m scripts.generate_dataset --count 500 --seed 4317`. It contains only fictional vendors, addresses, IDs, items and amounts. The generator uses a local seeded random-number generator and PyMuPDF, and writes PDF hashes, renderer version, ground truth, split labels, and independent expected exception codes to `manifest.json`. `vendors.json` and `purchase_orders.json` hold the master records. The minimum corpus size is 34. Use the default 500 for repeated cases and balanced layout comparisons.

| Partition | Default count | Layouts |
| --- | ---: | --- |
| Train | 300 | Ledger and Grid |
| Dev | 100 | Ledger and Grid |
| Test | 100 | Seen Ledger/Grid plus held-out Dispatch |

The same formatting families recur across development and the seen test comparison. Dispatch uses a different header arrangement and item delimiter and appears only in test. All three partitions have distinct vendor IDs and name sets. Duplicate pairs stay together and are evaluated in manifest order, with the original first. The baseline uses hand-authored parsing rules and has no fitted parameters.

The scenario cycle covers clean matches, amounts above and below the order, quantity and price differences, unknown vendor, missing PO, duplicate invoice, wrong tax, one mismatch across multiple lines, missing fields, date variation, layout variation, conflicting references, corrupt PDFs, currency mismatch, reordered lines, partial delivery, shared supplier names with separate IDs, and embedded instructions. The corrupt case retains the intended invoice as truth so extraction failure contributes false negatives. A conflicting reference also retains the intended value as truth so the benchmark measures the abstention.

The manifest records independent business labels even if the parser misses the evidence needed to verify them. Synthetic examples exercise the workflow. Real supplier PDFs, scans, and customer-specific error frequencies require separate testing.

DocILE and other restricted datasets are deferred until there is authorized, license-compliant access. A future invoice-only adapter would map supported extraction fields and report them separately. PO-based reconciliation requires paired master data and its own annotations.

