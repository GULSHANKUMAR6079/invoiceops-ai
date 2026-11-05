"""Idempotently load the generated vendor and purchase order master data."""
import argparse
import json
from pathlib import Path

from backend.app.config import Settings
from backend.app.db import OrderRow, VendorRow, make_engine, make_sessions
from backend.app.schemas import PurchaseOrder, Vendor


def seed(session, root):
    for name, model, row_type, key in (
        ("vendors.json", Vendor, VendorRow, "vendor_id"),
        ("purchase_orders.json", PurchaseOrder, OrderRow, "number"),
    ):
        for data in json.loads((root / name).read_text(encoding="utf-8")):
            entry = model.model_validate(data)
            identifier = getattr(entry, key)
            existing = session.get(row_type, identifier)
            if existing:
                if existing.data != entry.model_dump(mode="json"):
                    raise ValueError(f"Seed record {identifier} already exists with different values.")
                continue
            fields = {"vendor_id": entry.vendor_id} if row_type is OrderRow else {}
            session.add(row_type(id=identifier, data=entry.model_dump(mode="json"), **fields))
        session.flush()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data/synthetic"))
    args = parser.parse_args()
    engine = make_engine(Settings.from_env().database_url)
    try:
        with make_sessions(engine).begin() as session:
            seed(session, args.data)
    finally:
        engine.dispose()
    print(f"Seeded vendor and purchase order records from {args.data}")


if __name__ == "__main__":
    main()

