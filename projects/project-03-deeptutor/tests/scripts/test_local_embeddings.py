import numpy as np
import pytest
from fastapi.testclient import TestClient

from scripts import local_embeddings as server

MODEL = "test/alternate-embedding"
DIMENSION = 3


@pytest.fixture
def client(monkeypatch):
    class FakeModel:
        def embed(self, texts, batch_size):
            return [np.full(DIMENSION, index, dtype=float) for index, _ in enumerate(texts)]

    def load_model(model_name):
        assert model_name == MODEL
        return FakeModel()

    monkeypatch.setattr(server, "load_model", load_model)
    with TestClient(server.create_app(MODEL)) as client:
        yield client


def test_reports_selected_model_and_detected_dimension(client):
    assert client.get("/health").json() == {
        "service": "deeptutor-local-embeddings", "model": MODEL, "dimension": DIMENSION,
    }
    assert client.get("/v1/models").json()["data"][0]["id"] == MODEL


def test_openai_batch_contract_preserves_order_and_long_chunks(client):
    response = client.post(
        "/v1/embeddings",
        json={"model": MODEL, "input": ["中文资料" * 200, "另一份资料"]},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert [item["index"] for item in data] == [0, 1]
    assert data[0]["embedding"] == [0.0] * DIMENSION
    assert data[1]["embedding"] == [1.0] * DIMENSION


@pytest.mark.parametrize(
    "overrides,status",
    [
        ({"model": "unknown"}, 404),
        ({"dimensions": 384}, 400),
        ({"encoding_format": "base64"}, 400),
        ({"input": []}, 400),
        ({"input": [" "]}, 400),
        ({"input": ["text"] * 257}, 400),
    ],
)
def test_rejects_incompatible_embedding_requests(client, overrides, status):
    response = client.post(
        "/v1/embeddings", json={"model": MODEL, "input": "资料", **overrides}
    )
    assert response.status_code == status
