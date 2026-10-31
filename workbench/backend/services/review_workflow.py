"""Pure review/version/citation rules; persistence and reviewer authentication live at the API boundary."""
from copy import deepcopy
from datetime import datetime
import re
from urllib.parse import urlsplit

from executor.protocol import canonical, digest, identifier


def required_text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("missing " + name)


def timestamp(value):
    required_text(value, "reviewed_at")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("invalid reviewed_at") from error
    if parsed.tzinfo is None:
        raise ValueError("review time requires timezone")


def validate_document(document):
    if not isinstance(document, dict):
        raise ValueError("document must be an object")
    for key in ("document_id", "project_id", "artifact_id"):
        identifier(document.get(key))
    for key in ("title", "source_url", "body"):
        required_text(document.get(key), key)
    url = urlsplit(document["source_url"])
    if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
        raise ValueError("invalid source URL")
    authors = document.get("authors")
    if not isinstance(authors, list) or not authors:
        raise ValueError("missing authors")
    for author in authors:
        required_text(author, "author")
    if type(document.get("version")) is not int or document["version"] < 1:
        raise ValueError("invalid document version")
    if document.get("content_level") not in {"abstract", "fulltext"} or type(document.get("truncated")) is not bool:
        raise ValueError("invalid content metadata")
    if document.get("sha256") != digest(document["body"].encode("utf-8")):
        raise ValueError("document content hash mismatch")


def apply_review(document, *, decision, reviewed_version, reviewed_sha256, reviewer, reviewed_at):
    validate_document(document)
    if decision not in {"approved", "rejected"}:
        raise ValueError("invalid review decision")
    if type(reviewed_version) is not int or reviewed_version != document["version"] or reviewed_sha256 != document["sha256"]:
        raise ValueError("reviewed content is stale")
    required_text(reviewer, "reviewer")
    timestamp(reviewed_at)
    if decision == "approved" and document["truncated"]:
        raise ValueError("truncated evidence cannot be approved")
    result = deepcopy(document)
    result["review"] = {"decision": decision, "version": reviewed_version, "sha256": reviewed_sha256, "reviewer": reviewer, "reviewed_at": reviewed_at}
    return result


def build_approved_manifest(project_id, documents):
    identifier(project_id)
    seen, selected = set(), []
    for document in documents:
        if document.get("project_id") != project_id:
            continue
        doc_id = identifier(document.get("document_id"))
        if doc_id in seen:
            raise ValueError("duplicate document ID")
        seen.add(doc_id)
        review = document.get("review")
        if not review or review.get("decision") != "approved":
            continue
        try:
            apply_review(document, decision="approved", reviewed_version=review["version"], reviewed_sha256=review["sha256"], reviewer=review["reviewer"], reviewed_at=review["reviewed_at"])
        except (KeyError, TypeError) as error:
            raise ValueError("invalid stored review") from error
        selected.append({key: deepcopy(document[key]) for key in ("document_id", "version", "sha256", "artifact_id", "content_level")})
    if not selected:
        raise ValueError("no approved evidence")
    manifest = {"schema_version": 1, "project_id": project_id, "documents": sorted(selected, key=lambda d: d["document_id"])}
    return dict(manifest, manifest_sha256=digest(canonical(manifest)))


def validate_answer(answer, manifest, documents):
    if build_approved_manifest(manifest["project_id"], documents) != manifest:
        raise ValueError("evidence snapshot is stale or corrupted")
    required_text(answer.get("text"), "answer text")
    status = answer.get("evidence_status")
    citations = answer.get("citations")
    if status not in {"grounded", "insufficient"} or not isinstance(citations, list):
        raise ValueError("invalid evidence status")
    if status == "grounded" and not citations:
        raise ValueError("grounded answer requires citations")
    selected = {d["document_id"]: d for d in manifest["documents"]}
    bodies = {d["document_id"]: d["body"] for d in documents if d["project_id"] == manifest["project_id"]}
    for citation in citations:
        doc = selected.get(citation.get("document_id"))
        if doc is None or any(citation.get(k) != doc[k] for k in ("version", "sha256")):
            raise ValueError("unapproved or stale citation")
        quote = citation.get("quote")
        required_text(quote, "quote")
        locator = citation.get("locator", {})
        index = locator.get("value")
        paragraphs = [p for p in re.split(r"\n\s*\n", bodies[doc["document_id"]]) if p.strip()]
        if locator.get("kind") != "paragraph" or type(index) is not int or not 1 <= index <= len(paragraphs) or quote not in paragraphs[index - 1]:
            raise ValueError("quote cannot be verified at the stated location")
