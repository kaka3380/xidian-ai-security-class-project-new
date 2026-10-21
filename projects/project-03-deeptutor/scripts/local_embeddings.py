"""Optional CPU embedding service for the Windows local deployment."""

from contextlib import asynccontextmanager
import os
from pathlib import Path
import threading

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
MODEL = "BAAI/bge-small-zh-v1.5"
DIMENSION = 512
PORT = 8766
MODEL_CACHE = ROOT / "data" / "models" / "fastembed"
_lock = threading.Lock()


def load_model():
    from fastembed import TextEmbedding

    return TextEmbedding(
        model_name=MODEL,
        cache_dir=str(MODEL_CACHE),
        threads=2,
        local_files_only=True,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.model = load_model()
    yield
    del app.state.model


app = FastAPI(title="DeepTutor Local Embeddings", lifespan=lifespan)


class EmbeddingRequest(BaseModel):
    model: str
    input: str | list[str]
    dimensions: int | None = None
    encoding_format: str = "float"


@app.get("/health")
def health():
    return {"service": "deeptutor-local-embeddings", "model": MODEL, "dimension": DIMENSION}


@app.get("/v1/models")
def models():
    return {"object": "list", "data": [{"id": MODEL, "object": "model", "owned_by": "local"}]}


@app.post("/v1/embeddings")
def embeddings(payload: EmbeddingRequest):
    if payload.model != MODEL:
        raise HTTPException(404, "Unknown embedding model.")
    if payload.dimensions not in (None, DIMENSION):
        raise HTTPException(400, f"This model has a fixed dimension of {DIMENSION}.")
    if payload.encoding_format != "float":
        raise HTTPException(400, "Only float encoding is supported.")
    texts = [payload.input] if isinstance(payload.input, str) else payload.input
    if not texts or len(texts) > 256:
        raise HTTPException(400, "Supply between 1 and 256 texts per request.")
    if any(not text.strip() for text in texts):
        raise HTTPException(400, "Embedding input must contain non-empty text.")
    with _lock:
        vectors = list(app.state.model.embed(texts, batch_size=16))
    return {
        "object": "list",
        "model": MODEL,
        "data": [
            {"object": "embedding", "index": index, "embedding": vector.tolist()}
            for index, vector in enumerate(vectors)
        ],
    }


if __name__ == "__main__":
    import uvicorn

    os.environ.setdefault("HF_HOME", str(ROOT / "data" / "models" / "huggingface"))
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    uvicorn.run(app, host="127.0.0.1", port=PORT, access_log=False)
