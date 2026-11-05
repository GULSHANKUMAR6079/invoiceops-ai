"""Evaluate saved predictions against independent, held-out synthetic annotations."""
import argparse
import hashlib
import json
import os
import platform
from collections import Counter
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

import numpy as np
import pandas as pd
from sklearn.metrics import confusion_matrix

from backend.app import workflow as flow
from backend.app.db import Base, Document, RuleRow, make_engine, make_sessions, utcnow
from backend.app.providers import GroqProvider, PROMPT_VERSION, make_provider, model_name
from backend.app.reconciliation import reconcile
from backend.app.schemas import FIELDS, Extraction, Invoice, PurchaseOrder, ReconciliationResult, Rules, State
from scripts.generate_dataset import dump
from scripts.seed_database import seed

ROOT = Path(__file__).resolve().parents[1]
STATUSES = ["MATCHED", "REVIEW_REQUIRED", "PROCESSING_FAILED"]
RELAXED = Rules(amount_absolute=Decimal(5), quantity_absolute=Decimal(1))
NUMERIC = {"subtotal", "tax_amount", "total_amount", "quantity", "unit_price", "line_total"}


def ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def wilson(successes, n):
    if not n:
        return None
    z = 1.96
    p = successes / n
    center = (p + z * z / (2 * n)) / (1 + z * z / n)
    width = z * ((p * (1 - p) + z * z / (4 * n)) / n) ** 0.5 / (1 + z * z / n)
    return [max(0.0, center - width), min(1.0, center + width)]


def prf(tp, fp, fn):
    return {
        "tp": tp, "fp": fp, "fn": fn, "precision": ratio(tp, tp + fp),
        "recall": ratio(tp, tp + fn), "f1": ratio(2 * tp, 2 * tp + fp + fn),
    }


def normalized(field, value):
    if value is None:
        return None
    if field == "line_items":
        return tuple(sorted(line_key(line) for line in value))
    if field in NUMERIC:
        return str(Decimal(value).normalize())
    return " ".join(str(value).casefold().split())


def line_key(line):
    return tuple(normalized(key, line.get(key)) or "" for key in
                 ("product_code", "description", "quantity", "unit_price", "line_total"))


def extraction_metrics(rows):
    per_field = {}
    for field in FIELDS:
        exact = norm = tp = fp = fn = missing = hallucinated = truth_present = truth_absent = 0
        for row in rows:
            actual = row["prediction"][field]
            expected = row["truth"][field]
            exact += actual == expected
            equal = normalized(field, actual) == normalized(field, expected)
            norm += equal
            present = actual not in (None, [])
            labeled = expected not in (None, [])
            truth_present += labeled
            truth_absent += not labeled
            tp += present and labeled and equal
            fp += present and not equal
            fn += labeled and not equal
            missing += labeled and not present
            hallucinated += present and not labeled
        per_field[field] = {
            "n": len(rows), "exact_accuracy": ratio(exact, len(rows)),
            "normalized_accuracy": ratio(norm, len(rows)), "expected_present": truth_present,
            "expected_absent": truth_absent, "missing": missing, "hallucinated": hallucinated, **prf(tp, fp, fn),
        }
    micro = prf(*(sum(metric[name] for metric in per_field.values()) for name in ("tp", "fp", "fn")))
    macro = {}
    for name in ("precision", "recall", "f1"):
        defined = [metric[name] for metric in per_field.values() if metric[name] is not None]
        macro[name] = float(np.mean(defined)) if defined else None

    line_counts = Counter()
    for row in rows:
        actual = Counter(map(line_key, row["prediction"]["line_items"]))
        expected = Counter(map(line_key, row["truth"]["line_items"]))
        line_counts["tp"] += (actual & expected).total()
        line_counts["fp"] += (actual - expected).total()
        line_counts["fn"] += (expected - actual).total()
    present = sum(metric["expected_present"] for metric in per_field.values())
    absent = sum(metric["expected_absent"] for metric in per_field.values())
    missing = sum(metric["missing"] for metric in per_field.values())
    hallucinated = sum(metric["hallucinated"] for metric in per_field.values())
    total = per_field["total_amount"]
    return {
        "fields": per_field, "micro": micro, "macro": macro, "line_item_quality": prf(**line_counts),
        "missing_field_rate": ratio(missing, present), "missing_field_count": missing, "present_fields": present,
        "hallucinated_field_rate": ratio(hallucinated, absent), "hallucinated_field_count": hallucinated,
        "absent_fields": absent,
        "total_amount_accuracy": total["recall"], "total_amount_n": total["expected_present"],
    }


def reconciliation_metrics(rows, variant):
    correct = false_match = false_exception = needs_intervention = clean = 0
    exceptions = Counter(tp=0, fp=0, fn=0)
    per_code = {}
    expected_status = []
    predicted_status = []
    for row in rows:
        result = row["variants"][variant]
        expected = row["expected_status"]
        predicted = result["status"]
        expected_status.append(expected)
        predicted_status.append(predicted)
        correct += expected == predicted
        clean += expected == "MATCHED"
        needs_intervention += expected != "MATCHED"
        false_match += expected != "MATCHED" and predicted == "MATCHED"
        false_exception += expected == "MATCHED" and predicted == "REVIEW_REQUIRED"
        known = set(row["expected_exceptions"])
        predicted_codes = set(result["exception_codes"])
        for code in known | predicted_codes:
            count = per_code.setdefault(code, Counter(tp=0, fp=0, fn=0))
            key = "tp" if code in known and code in predicted_codes else "fp" if code in predicted_codes else "fn"
            count[key] += 1
            exceptions[key] += 1
    performance = prf(**exceptions)
    duplicate = prf(**per_code.get("DUPLICATE_INVOICE", Counter(tp=0, fp=0, fn=0)))
    return {
        "n": len(rows), "correct": correct, "accuracy": ratio(correct, len(rows)),
        "accuracy_wilson_95": wilson(correct, len(rows)),
        "confusion_matrix": {"labels": STATUSES, "rows_truth_columns_prediction":
                             confusion_matrix(expected_status, predicted_status, labels=STATUSES).tolist()},
        "exceptions": performance, "exception_recall": performance["recall"],
        "exception_recall_wilson_95": wilson(exceptions["tp"], exceptions["tp"] + exceptions["fn"]),
        "by_exception_code": {code: prf(**count) for code, count in sorted(per_code.items())},
        "duplicate_detection": duplicate, "duplicate_detection_recall": duplicate["recall"],
        "false_approval_rate": ratio(false_match, needs_intervention),
        "false_approval_count": false_match, "expected_intervention": needs_intervention,
        "false_exception_rate": ratio(false_exception, clean),
        "false_exception_count": false_exception, "expected_clean": clean,
    }


def operational(rows, elapsed, rates):
    latencies = np.array([row["latency_ms"] for row in rows])
    states = Counter(row["variants"]["default"]["status"] for row in rows)
    usage = [row["tokens"] for row in rows if row["tokens"]["input"] is not None and row["tokens"]["output"] is not None]
    average_cost = None
    if rates is not None:
        average_cost = ratio(sum(tokens["input"] * rates[0] + tokens["output"] * rates[1] for tokens in usage),
                             len(rows) * 1_000_000)
    return {
        "n": len(rows), "p50_ms": float(np.median(latencies)), "p95_ms": float(np.percentile(latencies, 95)),
        "throughput_documents_per_second": len(rows) / elapsed, "workload_seconds": elapsed,
        "manual_review_rate": ratio(states["REVIEW_REQUIRED"], len(rows)),
        "manual_review_count": states["REVIEW_REQUIRED"], "failure_count": states["PROCESSING_FAILED"],
        "failure_rate": ratio(states["PROCESSING_FAILED"], len(rows)),
        "average_estimated_cost_usd": average_cost, "documents_with_token_usage": len(usage),
    }


def grouped(rows):
    frame = pd.DataFrame(rows)
    output = {}
    for column in ("layout_group", "layout", "difficulty", "quality", "case"):
        output[column] = {}
        for name, group in frame.groupby(column):
            subset = group.to_dict("records")
            recon = reconciliation_metrics(subset, "default")
            extract = extraction_metrics(subset)
            output[column][name] = {
                "n": len(subset), "decision_accuracy": recon["accuracy"],
                "exception_recall": recon["exception_recall"], "extraction_micro_f1": extract["micro"]["f1"],
            }
    return output


def result_summary(result):
    return {"status": result.status, "exception_codes": result.exception_codes}


def replay(doc, session, extraction, default):
    order = PurchaseOrder.model_validate(doc.order_snapshot) if doc.order_snapshot else None
    vendor = flow.find_vendor(session, extraction.invoice)
    duplicate = "DUPLICATE_INVOICE" in default.exception_codes
    variants = {"default": result_summary(default)}
    for name, enabled, rules in (
        ("relaxed_tolerances", duplicate, RELAXED),
        ("no_duplicate_detection", False, Rules()),
        ("no_soft_confidence_gate", duplicate, Rules(minimum_confidence=0.0)),
    ):
        variants[name] = result_summary(reconcile(doc.id, extraction, order, vendor, enabled, rules, 1))
    return variants


def measure(data, samples, provider, database):
    engine = make_engine(f"sqlite:///{database.as_posix()}")
    Base.metadata.create_all(engine)
    sessions = make_sessions(engine)
    rows = []
    try:
        with sessions.begin() as session:
            seed(session, data)
            session.add(RuleRow(id=1, version=1, data={}))
        for sample in samples:
            content = (data / sample["path"]).read_bytes()
            if hashlib.sha256(content).hexdigest() != sample["sha256"]:
                raise ValueError(f"PDF hash mismatch for {sample['id']}")
            started = perf_counter()
            with sessions.begin() as session:
                doc, _ = flow.upload(session, Path(sample["path"]).name, content, "evaluation")
            with sessions.begin() as session:
                doc = session.get(Document, doc.id)
                flow.extract_document(session, doc, "evaluation", provider, 20)
            if doc.state == State.EXTRACTED:
                with sessions.begin() as session:
                    doc = session.get(Document, doc.id)
                    flow.reconcile_document(session, doc, "evaluation")
            latency = (perf_counter() - started) * 1000
            extraction = Extraction.model_validate(doc.extraction) if doc.extraction else None
            default = ReconciliationResult.model_validate(doc.result)
            if extraction:
                with sessions() as session:
                    variants = replay(doc, session, extraction, default)
            else:
                variants = dict.fromkeys(("default", "relaxed_tolerances", "no_duplicate_detection",
                                          "no_soft_confidence_gate"), result_summary(default))
            rows.append({
                **{key: sample[key] for key in ("id", "case", "layout", "layout_group", "difficulty", "quality",
                                                "expected_status", "expected_exceptions", "sha256")},
                "truth": sample["invoice"],
                "prediction": (extraction.invoice if extraction else Invoice()).model_dump(mode="json"),
                "field_status": {field: evidence.status for field, evidence in extraction.fields.items()} if extraction else {},
                "confidence": extraction.confidence if extraction else None,
                "error": doc.error, "latency_ms": latency, "variants": variants,
                "tokens": {"input": extraction.input_tokens if extraction else None,
                           "output": extraction.output_tokens if extraction else None},
            })
        # Exclude report generation, hash verification, and replay experiments from workflow throughput.
        elapsed = sum(row["latency_ms"] for row in rows) / 1000
    finally:
        engine.dispose()
    return rows, elapsed


def save_predictions(output, rows, prefix):
    path = output / f"{prefix}_predictions.jsonl"
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    saved = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    failures = []
    for row in saved:
        default = row["variants"]["default"]
        mismatches = [field for field in FIELDS if normalized(field, row["truth"][field]) !=
                      normalized(field, row["prediction"][field])]
        if (mismatches or default["status"] != row["expected_status"] or
                set(default["exception_codes"]) != set(row["expected_exceptions"])):
            failures.append({"id": row["id"], "case": row["case"], "layout": row["layout"],
                             "mismatched_fields": mismatches, "expected_status": row["expected_status"],
                             "expected_exceptions": row["expected_exceptions"], "observed": default,
                             "error": row["error"]})
    dump(output / f"{prefix}_failures.json", failures)
    return saved


def summarize(rows, elapsed, rates=None):
    return {
        "extraction": extraction_metrics(rows),
        "reconciliation": reconciliation_metrics(rows, "default"),
        "operational": operational(rows, elapsed, rates),
        "groups": grouped(rows),
        "experiments": {name: reconciliation_metrics(rows, name) for name in rows[0]["variants"] if name != "default"},
    }


def fingerprint():
    digest = hashlib.sha256()
    paths = [*(ROOT / "backend/app").glob("*.py"), *(ROOT / "scripts").glob("*.py")]
    for path in sorted(paths):
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()


def chart(path, groups):
    palette = ["#266047", "#975c25"]
    lines = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 680 230" role="img" '
             'aria-labelledby="title"><title id="title">Decision accuracy by layout group</title>',
             '<rect width="680" height="230" rx="12" fill="#f1f6ef"/>',
             '<text x="28" y="43" fill="#17392c" font-family="sans-serif" font-size="23" font-weight="700">'
             'Decision accuracy by layout group</text>']
    for index, (name, group) in enumerate(sorted(groups.items(), reverse=True)):
        y = 90 + index * 62
        rate = group["decision_accuracy"]
        lines.extend([
            f'<text x="28" y="{y + 16}" fill="#17392c" font-family="sans-serif" font-size="15">'
            f'{name.replace("_", " ").title()} (n={group["n"]})</text>',
            f'<rect x="220" y="{y}" width="340" height="20" rx="4" fill="#d7e3d8"/>',
            f'<rect x="220" y="{y}" width="{340 * rate:.2f}" height="20" rx="4" fill="{palette[index]}"/>',
            f'<text x="575" y="{y + 16}" fill="#17392c" font-family="sans-serif" font-size="15">{rate:.1%}</text>',
        ])
    path.write_text("\n".join([*lines, "</svg>"]) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data/synthetic"))
    parser.add_argument("--output", type=Path, default=Path("data/evaluation"))
    parser.add_argument("--split", choices=["dev", "test"], default="test")
    parser.add_argument("--groq", action="store_true", help="Opt into a live Groq run using generated documents")
    parser.add_argument("--input-usd-per-million", type=float)
    parser.add_argument("--output-usd-per-million", type=float)
    args = parser.parse_args()
    rates = (args.input_usd_per_million, args.output_usd_per_million)
    if any(rate is not None for rate in rates) and not all(rate is not None and np.isfinite(rate) and rate >= 0 for rate in rates):
        parser.error("Supply both finite, nonnegative USD token rates or leave both unset.")
    if args.groq and not os.getenv("GROQ_API_KEY"):
        parser.error("--groq requires GROQ_API_KEY.")
    manifest = json.loads((args.data / "manifest.json").read_text(encoding="utf-8"))
    samples = [row for row in manifest["documents"] if row["split"] == args.split]
    if not samples:
        parser.error("The requested split has no documents.")
    args.output.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="run-", dir=args.output) as scratch:
        rows, elapsed = measure(args.data, samples, make_provider("baseline", args.data / "manifest.json"),
                                Path(scratch) / "baseline.db")
        baseline = summarize(save_predictions(args.output, rows, "baseline"), elapsed)
        llm = {"status": "NOT EVALUATED"}
        if args.groq:
            groq_rows, groq_elapsed = measure(args.data, samples, GroqProvider(), Path(scratch) / "groq.db")
            llm = {"status": "EVALUATED", "model": model_name(), "prompt_version": PROMPT_VERSION,
                   **summarize(save_predictions(args.output, groq_rows, "groq"), groq_elapsed,
                               rates if rates[0] is not None else None)}
            if llm["operational"]["documents_with_token_usage"] == 0 and llm["operational"]["failure_rate"] == 1:
                llm["status"] = "ATTEMPTED; NO USABLE OUTPUTS"
    report = {
        "metadata": {
            "dataset_version": manifest["version"], "dataset_seed": manifest["seed"],
            "dataset_count": manifest["count"], "split": args.split, "split_definition": manifest["split_definition"],
            "dataset_provenance": manifest["provenance"], "pdf_renderer": manifest["renderer"],
            "sample_size": len(samples), "provider": "baseline", "model": None, "prompt_version": None,
            "code_sha256": fingerprint(), "generated_at": utcnow().isoformat(),
            "hardware": {"platform": platform.platform(), "processor": platform.processor(),
                         "logical_cpus_visible": os.cpu_count(), "python": platform.python_version()},
            "workload": "One document at a time; upload, extraction, reconciliation and commits to fresh local SQLite.",
            "cost_assumptions": {"baseline_api_cost_usd": 0, "groq_usd_per_million_input_output":
                                 list(rates) if args.groq and rates[0] is not None else None,
                                 "note": "Token estimates exclude failed calls without usage and all non-API costs."},
            "rule_versions": {"default": Rules().model_dump(mode="json"), "relaxed": RELAXED.model_dump(mode="json")},
        },
        "baseline": baseline, "llm": llm,
    }
    dump(args.output / "metrics.json", report)
    chart(args.output / "decision-accuracy.svg", baseline["groups"]["layout_group"])
    print(f"{args.split}: {len(samples)} documents; extraction micro F1 {baseline['extraction']['micro']['f1']:.3f}; "
          f"decision accuracy {baseline['reconciliation']['accuracy']:.3f}; report {args.output / 'metrics.json'}")


if __name__ == "__main__":
    main()

