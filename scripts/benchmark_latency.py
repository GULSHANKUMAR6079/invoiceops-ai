"""Measure one-at-a-time upload through reconciliation using FastAPI TestClient."""
import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

import numpy as np
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.db import Base, RuleRow
from backend.app.main import create_app
from scripts.generate_dataset import dump
from scripts.seed_database import seed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data/synthetic"))
    parser.add_argument("--output", type=Path, default=Path("data/evaluation/api_latency.json"))
    parser.add_argument("--count", type=int, default=30)
    args = parser.parse_args()
    if args.count < 1:
        parser.error("--count must be positive.")
    rows = json.loads((args.data / "manifest.json").read_text(encoding="utf-8"))["documents"]
    samples = [row for row in rows if row["split"] == "test"][:args.count]
    if not samples:
        parser.error("The dataset contains no test documents.")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    latencies = []
    with TemporaryDirectory(prefix="api-", dir=args.output.parent) as scratch:
        database = (Path(scratch) / "benchmark.db").as_posix()
        app = create_app(Settings(database_url=f"sqlite:///{database}"))
        Base.metadata.create_all(app.state.engine)
        with app.state.sessions.begin() as session:
            session.add(RuleRow(id=1, version=1, data={}))
            seed(session, args.data)
        with TestClient(app, headers={"X-Demo-User": "analyst"}) as client:
            for sample in samples:
                path = args.data / sample["path"]
                content = path.read_bytes()
                started = perf_counter()
                upload = client.post("/api/invoices/upload", files={"file": (path.name, content, "application/pdf")})
                upload.raise_for_status()
                url = f"/api/invoices/{upload.json()['id']}"
                extraction = client.post(url + "/extract")
                extraction.raise_for_status()
                final = extraction
                if extraction.json()["state"] == "EXTRACTED":
                    final = client.post(url + "/reconcile")
                    final.raise_for_status()
                latencies.append({"id": sample["id"], "state": final.json()["state"],
                                  "latency_ms": (perf_counter() - started) * 1000})
    values = [item["latency_ms"] for item in latencies]
    result = {
        "workload": "Sequential multipart upload + extraction + reconciliation, HTTPX ASGI TestClient, fresh disk SQLite",
        "n": len(values), "p50_ms": float(np.median(values)), "p95_ms": float(np.percentile(values, 95)),
        "documents_per_second": len(values) / (sum(values) / 1000), "samples": latencies,
    }
    dump(args.output, result)
    print(f"{result['n']} requests flows: p50={result['p50_ms']:.2f} ms, p95={result['p95_ms']:.2f} ms")


if __name__ == "__main__":
    main()

