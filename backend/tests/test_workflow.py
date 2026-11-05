from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy.exc import OperationalError

from scripts.generate_dataset import SCENARIOS
from conftest import process, upload


@pytest.mark.parametrize("index", range(len(SCENARIOS)), ids=SCENARIOS)
def test_independent_scenario(client, dataset, index):
    root, samples = dataset
    row = samples[index]
    if row["case"] == "duplicate_invoice":
        process(client, root / samples[index - 1]["path"])
    url, response = process(client, root / row["path"])
    assert response["state"] == row["expected_status"]
    assert set(response["exception_codes"]) == set(row["expected_exceptions"])
    assert client.get(url + "/exceptions").json()["status"] == row["expected_status"]
    if row["case"] == "prompt_injection":
        assert response["extraction"]["invoice"]["total_amount"] == row["invoice"]["total_amount"]


def test_review_with_corrected_total(client, dataset):
    root, rows = dataset
    sample = rows[2]
    url, row = process(client, root / sample["path"])
    original_total = row["extraction"]["invoice"]["total_amount"]
    from decimal import Decimal
    correct_total = str(Decimal(original_total) - Decimal("4.31"))
    edited = client.patch(url + "/extracted-fields", json={
        "version": row["version"], "fields": {"total_amount": correct_total},
        "reason": "Confirmed invoice correction",
    })
    assert edited.status_code == 200, edited.text
    row = edited.json()
    assert row["state"] == "MATCHED"
    assert row["original"]["invoice"]["total_amount"] == original_total
    assert row["extraction"]["invoice"]["total_amount"] == correct_total
    result = client.post(url + "/approve", json={"version": row["version"]})
    assert result.json()["state"] == "APPROVED"
    audit = client.get("/api/audit-logs", params={"invoice_id": row["id"]}).json()
    event = next(item for item in audit if item["action"] == "correct_fields")
    assert event["changes"]["before"]["total_amount"] == original_total
    assert audit[0]["actor"] == "analyst"
    assert audit[0]["new_state"] == "APPROVED"


@pytest.mark.parametrize("operation", ["approve", "reject"])
def test_exception_requires_reason(client, dataset, operation):
    root, rows = dataset
    url, row = process(client, root / rows[2]["path"])
    assert client.post(url + "/" + operation, json={"version": row["version"]}).status_code == 409
    response = client.post(url + "/" + operation, json={"version": row["version"], "reason": "Reviewed original"})
    assert response.status_code == 200
    assert response.json()["state"] == ("APPROVED" if operation == "approve" else "REJECTED")


def test_permissions_and_pdf_access(client, dataset):
    root, rows = dataset
    url, row = process(client, root / rows[0]["path"])
    assert client.post(url + "/approve", headers={"X-Demo-User": "viewer"},
                       json={"version": row["version"]}).status_code == 403
    assert client.get(url + "/document", headers={"X-Demo-User": ""}).status_code == 401
    response = client.get(url + "/document", headers={"X-Demo-User": "viewer"})
    assert response.content == (root / rows[0]["path"]).read_bytes()
    assert response.headers["content-disposition"].startswith("attachment")
    preview = client.get(url + "/preview/1")
    assert preview.content.startswith(b"\x89PNG")
    assert preview.headers["x-page-count"] == "1"


def test_idempotent_repeated_upload_and_approval(client, dataset):
    root, rows = dataset
    file = root / rows[0]["path"]
    url, row = process(client, file)
    duplicate = upload(client, file, "different-name.pdf")
    assert duplicate.status_code == 200
    assert duplicate.json()["duplicate_upload"]
    assert duplicate.json()["id"] == row["id"]
    assert client.post(url + "/reconcile").json()["version"] == row["version"]
    body = {"version": row["version"], "reason": "Verified"}
    assert client.post(url + "/approve", json=body).status_code == 200
    assert client.post(url + "/approve", json=body).status_code == 200
    assert client.post(url + "/approve", json={**body, "reason": "Different"}).status_code == 409
    events = client.get("/api/audit-logs", params={"invoice_id": row["id"]}).json()
    assert len([e for e in events if e["action"] == "approve"]) == 1
    assert len([e for e in events if e["action"] == "upload"]) == 1


def test_illegal_and_stale_transitions(client, dataset):
    root, rows = dataset
    row = upload(client, root / rows[0]["path"]).json()
    url = f"/api/invoices/{row['id']}"
    assert client.post(url + "/reconcile").status_code == 409
    assert client.post(url + "/approve", json={"version": row["version"]}).status_code == 409
    client.post(url + "/extract")
    client.post(url + "/reconcile")
    assert client.post(url + "/approve", json={"version": row["version"]}).status_code == 409


def test_concurrent_conflicting_decisions(client, dataset):
    root, rows = dataset
    url, row = process(client, root / rows[0]["path"])
    barrier = Barrier(2)

    def decide(action):
        barrier.wait(timeout=5)
        response = client.post(url + "/" + action, json={"version": row["version"], "reason": "Review"})
        if response.status_code == 503:
            response = client.post(url + "/" + action, json={"version": row["version"], "reason": "Review"})
        return response.status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(decide, ["approve", "reject"])) == [200, 409]
    events = client.get("/api/audit-logs", params={"invoice_id": row["id"]}).json()
    assert sum(e["action"] in {"approve", "reject"} for e in events) == 1


@pytest.mark.parametrize(("name", "mime", "content", "expected"), [
    ("a.txt", "text/plain", b"%PDF-valid", 415),
    ("a.pdf", "application/pdf", b"not pdf", 415),
    ("a.pdf", "application/octet-stream", b"%PDF-valid", 415),
    ("big.pdf", "application/pdf", b"%PDF-" + b"0" * 140_000, 413),
])
def test_upload_rejections(client, name, mime, content, expected):
    assert client.post("/api/invoices/upload", files={"file": (name, content, mime)}).status_code == expected


def test_missing_upload_and_record(client):
    assert client.post("/api/invoices/upload", content=b"", headers={"Content-Length": "0"}).status_code == 422
    assert client.get("/api/invoices/missing/document").status_code == 404


def test_filename_sanitization(client, dataset):
    root, rows = dataset
    response = upload(client, root / rows[0]["path"], "../folder/odd^name.pdf")
    assert response.json()["filename"] == "odd_name.pdf"


def test_retry_failed_pdf(client, dataset):
    root, rows = dataset
    url, failed = process(client, root / rows[14]["path"])
    assert "damaged" in failed["error"]
    retry = client.post(url + "/extract").json()
    assert retry["state"] == "PROCESSING_FAILED"
    assert retry["version"] == failed["version"] + 1


def test_retry_after_database_failure(client, app, monkeypatch):
    from sqlalchemy import event
    engine = app.state.engine

    def unavailable(*_):
        raise OperationalError("SELECT 1", None, RuntimeError("simulated outage"))

    event.listen(engine, "before_cursor_execute", unavailable)
    try:
        response = client.get("/api/health")
        assert response.status_code == 503
        assert "simulated outage" not in response.text
        assert response.headers["retry-after"] == "1"
    finally:
        event.remove(engine, "before_cursor_execute", unavailable)
    assert client.get("/api/health").status_code == 200


def test_rule_versioning_and_audit(client, dataset):
    root, rows = dataset
    rules = client.get("/api/rules").json()
    body = {**rules, "reason": "Allow small variances"}
    body["rules"]["amount_absolute"] = "5"
    assert client.put("/api/rules", json=body).status_code == 403
    headers = {"X-Demo-User": "admin"}
    assert client.put("/api/rules", json=body, headers=headers).json()["version"] == 2
    assert client.put("/api/rules", json=body, headers=headers).status_code == 409
    _, result = process(client, root / rows[2]["path"])
    assert result["result"]["rule_version"] == 2
    assert result["exception_codes"] == ["TOTAL_ARITHMETIC"]


def test_duplicate_cannot_be_overridden(client, dataset):
    root, rows = dataset
    process(client, root / rows[0]["path"])
    url, duplicate = process(client, root / rows[1]["path"])
    response = client.post(url + "/approve", json={"version": duplicate["version"], "reason": "Override"})
    assert response.status_code == 409
    assert "duplicate identity" in response.json()["detail"]


def test_upload_and_audit_rollback_together(client, app, dataset):
    from sqlalchemy import event
    root, rows = dataset
    path = root / rows[0]["path"]
    engine = app.state.engine

    def fail_audit(_connection, _cursor, sql, *_):
        if sql.startswith("INSERT INTO audit_events"):
            raise OperationalError(sql, None, RuntimeError("simulated audit write failure"))

    event.listen(engine, "before_cursor_execute", fail_audit)
    try:
        assert upload(client, path).status_code == 503
    finally:
        event.remove(engine, "before_cursor_execute", fail_audit)
    assert client.get("/api/invoices").json() == []
    assert upload(client, path).status_code == 201


def test_recheck_uses_new_rules_and_invalidates_old_review(client, dataset):
    root, rows = dataset
    url, initial = process(client, root / rows[5]["path"])
    data = client.get("/api/rules").json()
    data["rules"]["amount_absolute"] = "100"
    data["reason"] = "Updated unit price and total tolerance"
    assert client.put("/api/rules", json=data, headers={"X-Demo-User": "admin"}).status_code == 200
    checked = client.post(url + "/reconcile?rerun=true").json()
    assert checked["state"] == "MATCHED"
    assert checked["result"]["rule_version"] == 2
    assert client.post(url + "/approve", json={"version": initial["version"]}).status_code == 409

