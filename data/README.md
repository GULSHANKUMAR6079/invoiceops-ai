# Data

`synthetic/` is generated locally by `python -m scripts.generate_dataset`. It includes PDFs, a hash manifest with ground truth and scenario annotations, vendor master records, and purchase orders. The same command and renderer version reproduce the saved evaluation corpus.

`evaluation/` contains the measured held-out run, raw predictions, failure examples, an API latency sample and a layout chart. See [evaluation definitions](../docs/EVALUATION_REPORT.md).

