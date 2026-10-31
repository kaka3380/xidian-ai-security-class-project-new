"""Conservative extractive evidence view on native 03 retrieval, not generated claims."""
import math
import re
from difflib import SequenceMatcher

from workbench.backend.services.review_workflow import validate_answer


STOP_WORDS = set("what are is the a an how does do of to and in for with this that explain describe please why".split())


def source_answer(query, result, snapshot):
    words = [w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2,}", query) if w.lower() not in STOP_WORDS]
    sources = result.get("sources", [])
    if not isinstance(sources, list):
        raise ValueError("invalid retrieval sources")
    docs = {f["path"]: next(d for d in snapshot["documents"] if d["document_id"] == f["document_id"]) for f in snapshot["files"]}
    citations = []
    seen = set()
    for source in sources:
        name = str(source.get("source") or source.get("title", "")).replace("\\", "/").rsplit("/", 1)[-1]
        if name not in docs:
            raise ValueError("03 returned an unapproved source")
        doc = docs[name]
        text = source.get("content", "")
        if not isinstance(text, str):
            raise ValueError("invalid retrieved passage")
        # Scores are not an entailment proof. Require literal query terms too,
        # and describe only the verbatim passage, never assert a full QA answer.
        score = source.get("score")
        if isinstance(score, float) and not math.isfinite(score):
            raise ValueError("invalid retrieval score")
        context = result.get("content") or text
        if not isinstance(context, str):
            raise ValueError("invalid retrieval context")
        for index, paragraph in enumerate([p for p in re.split(r"\n\s*\n", doc["body"]) if p.strip()], 1):
            match = SequenceMatcher(None, context, paragraph, autojunk=False).find_longest_match()
            span = paragraph[match.b:match.b + match.size]
            positions = [span.lower().find(word) for word in words if word in span.lower()]
            offset = max(0, min(positions) - 100) if positions else 0
            quote = span[offset:offset + 500]
            matched_terms = [word for word in words if word in quote.lower()]
            if len(quote) < 30 or not matched_terms or (len(words) > 1 and len(matched_terms) < min(2, len(words))):
                continue
            key = (doc["document_id"], quote)
            if key in seen:
                continue
            seen.add(key)
            citations.append({"document_id": doc["document_id"], "version": doc["version"], "sha256": doc["sha256"],
                              "title": doc["title"], "source_url": doc["source_url"],
                              "quote": quote, "locator": {"kind": "paragraph", "value": index}})
    answer = {"text": "找到以下可定位原文，仅作摘录；未生成推理答案。" if citations else "所选批准资料中的可核对证据不足。",
              "evidence_status": "grounded" if citations else "insufficient", "citations": citations,
              "answer_mode": "extractive_evidence", "kb_version": snapshot["kb_version"],
              "approved_manifest_hash": snapshot["approved_manifest_hash"], "external_kb_id": snapshot["external_kb_id"],
              "is_current": snapshot["is_current"]}
    validate_answer(answer, snapshot["manifest"], snapshot["documents"])
    return answer
