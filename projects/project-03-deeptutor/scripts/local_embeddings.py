"""Optional CPU embedding service for the Windows local deployment."""

from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import threading

from fastapi import APIRouter, FastAPI, HTTPException, Request
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
MODEL_CACHE = ROOT / "data" / "models" / "fastembed"
CONFIG = ROOT / "data" / "user" / "settings" / "local_embeddings.json"
router = APIRouter()


def load_model(model_name):
    from fastembed import TextEmbedding

    return TextEmbedding(
        model_name=model_name,
        cache_dir=str(MODEL_CACHE),
        threads=2,
        local_files_only=True,
    )


def create_app(model_name):
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        model = load_model(model_name)
        vector = next(iter(model.embed(["embedding dimension check"], batch_size=1)))
        app.state.dimension = len(vector)
        app.state.model = model
        yield
        del app.state.model

    app = FastAPI(title="DeepTutor Local Embeddings", lifespan=lifespan)
    app.state.model_name = model_name
    app.state.lock = threading.Lock()
    app.include_router(router)
    return app


class EmbeddingRequest(BaseModel):
    model: str
    input: str | list[str]
    dimensions: int | None = None
    encoding_format: str = "float"


@router.get("/health")
def health(request: Request):
    return {"service": "deeptutor-local-embeddings", "model": request.app.state.model_name,
            "dimension": request.app.state.dimension}


@router.get("/v1/models")
def models(request: Request):
    return {"object": "list", "data": [{"id": request.app.state.model_name, "object": "model", "owned_by": "local"}]}


@router.post("/v1/embeddings")
def embeddings(payload: EmbeddingRequest, request: Request):
    state = request.app.state
    if payload.model != state.model_name:
        raise HTTPException(404, "Unknown embedding model.")
    if payload.dimensions not in (None, state.dimension):
        raise HTTPException(400, f"This model has a fixed dimension of {state.dimension}.")
    if payload.encoding_format != "float":
        raise HTTPException(400, "Only float encoding is supported.")
    texts = [payload.input] if isinstance(payload.input, str) else payload.input
    if not texts or len(texts) > 256:
        raise HTTPException(400, "Supply between 1 and 256 texts per request.")
    if any(not text.strip() for text in texts):
        raise HTTPException(400, "Embedding input must contain non-empty text.")
    with state.lock:
        vectors = list(state.model.embed(texts, batch_size=16))
    return {
        "object": "list",
        "model": state.model_name,
        "data": [
            {"object": "embedding", "index": index, "embedding": vector.tolist()}
            for index, vector in enumerate(vectors)
        ],
    }


if __name__ == "__main__":
    import uvicorn

    os.environ.setdefault("HF_HOME", str(ROOT / "data" / "models" / "huggingface"))
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    config = json.loads(CONFIG.read_text(encoding="utf-8-sig"))
    model_name, port = config.get("model"), config.get("port")
    if config.get("enabled") is not True or not isinstance(model_name, str) or not model_name.strip() or type(port) is not int or not 1 <= port <= 65535:
        raise SystemExit("Enable the optional service and set model and port in local_embeddings.json.")
    uvicorn.run(create_app(model_name.strip()), host="127.0.0.1", port=port, access_log=False)
