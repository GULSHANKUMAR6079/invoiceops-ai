from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.db import Base, RuleRow
from backend.app.main import create_app
from scripts.generate_dataset import build
from scripts.seed_database import seed


@pytest.fixture(scope="session")
def dataset(tmp_path_factory):
    path = tmp_path_factory.mktemp("synthetic")
    return path, build(path, 100, 4317)


@pytest.fixture
def app(tmp_path, dataset):
    settings = Settings(database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
                        frontend=Path("not-built"), max_bytes=64 * 1024)
    instance = create_app(settings)
    Base.metadata.create_all(instance.state.engine)
    with instance.state.sessions.begin() as session:
        session.add(RuleRow(id=1, version=1, data={}))
        seed(session, dataset[0])
    return instance


@pytest.fixture
def client(app):
    with TestClient(app, headers={"X-Demo-User": "analyst"}) as test_client:
        yield test_client


def upload(client, path, filename=None):
    return client.post("/api/invoices/upload",
                       files={"file": (filename or path.name, path.read_bytes(), "application/pdf")})


def process(client, path):
    response = upload(client, path)
    assert response.status_code == 201, response.text
    url = f"/api/invoices/{response.json()['id']}"
    response = client.post(url + "/extract")
    assert response.status_code == 200, response.text
    if response.json()["state"] == "EXTRACTED":
        response = client.post(url + "/reconcile")
        assert response.status_code == 200, response.text
    return url, response.json()

