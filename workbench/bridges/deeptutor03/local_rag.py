"""Run only under 03's interpreter. Reuse native retrieval; never synthesise with an LLM."""
import asyncio
from contextlib import redirect_stdout
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[3]
PROJECT03 = ROOT / "projects/project-03-deeptutor"
sys.path.insert(0, str(PROJECT03))


async def execute(payload):
    from deeptutor.services.embedding.config import get_embedding_config, embedding_config_scope
    from deeptutor.services.rag.embedding_binding import default_selection
    from deeptutor.services.rag.pipelines.llamaindex.config import retrieval_config_from_settings

    selection = default_selection() if payload["operation"] == "preflight" else payload["contract"]["selection"]
    if not selection:
        raise ValueError("explicit local embedding catalog selection required")
    config = get_embedding_config(selection)
    endpoint = urlsplit(config.effective_url or config.base_url or "")
    if config.provider_mode != "local" or endpoint.scheme != "http" or endpoint.hostname not in {"127.0.0.1", "localhost", "::1"} or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
        raise ValueError("M1 only permits existing local embeddings; remote/paid embeddings disabled")
    retrieval = retrieval_config_from_settings()
    if retrieval.reranker_model or retrieval.fusion_num_queries != 1:
        raise ValueError("M1 requires existing native retrieval without extra model expansion/reranking")
    contract = {"selection": selection, "model": config.model, "dimension": config.dim,
                "endpoint": config.effective_url or config.base_url, "provider_mode": "local",
                "retrieval_profile": retrieval.profile}
    if payload["operation"] == "preflight":
        return contract
    if payload["operation"] != "search" or contract != payload["contract"]:
        raise ValueError("embedding contract changed or unsupported operation")
    from deeptutor.tools.rag_tool import rag_search
    with embedding_config_scope(config):
        return await rag_search(payload["query"], kb_name=payload["kb_name"], provider="llamaindex", top_k=5)


if __name__ == "__main__":
    try:
        payload = json.load(sys.stdin)
        # Third-party imports/loggers sometimes print to stdout. Keep the wire clean.
        with redirect_stdout(sys.stderr):
            result = asyncio.run(execute(payload))
        print(json.dumps(result, ensure_ascii=True))
    except Exception as error:
        print(type(error).__name__ + ": local RAG preflight/search failed", file=sys.stderr)
        sys.exit(1)
