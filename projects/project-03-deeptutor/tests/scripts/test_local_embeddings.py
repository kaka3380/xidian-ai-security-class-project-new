import numpy as np
import pytest
from fastapi.testclient import TestClient

from scripts import local_embeddings as server


@pytest.fixture
def client(monkeypatch):
    class FakeModel:
        def embed(self, texts, batch_size):
            return [np.full(server.DIMENSION, index, dtype=float) for index, _ in enumerate(texts)]

    monkeypatch.setattr(server, "load_model", FakeModel)
    with TestClient(server.app) as client:
        yield client


def test_openai_batch_contract_preserves_order_and_long_chunks(client):
    response = client.post(
        "/v1/embeddings",
        json={"model": server.MODEL, "input": ["中文资料" * 200, "另一份资料"]},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert [item["index"] for item in data] == [0, 1]
    assert data[0]["embedding"] == [0.0] * 512
    assert data[1]["embedding"] == [1.0] * 512


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
        "/v1/embeddings", json={"model": server.MODEL, "input": "资料", **overrides}
    )
    assert response.status_code == status
