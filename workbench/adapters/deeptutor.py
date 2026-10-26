"""Approved snapshot -> existing 03 HTTP APIs. No whole-folder sync or default KB."""
import json
from pathlib import Path
import subprocess
from urllib.parse import quote, urlsplit

import httpx

from executor.protocol import canonical, confined, digest, file_hash

ROOT = Path(__file__).resolve().parents[2]
PROJECT03 = ROOT / "projects/project-03-deeptutor"


class UpstreamError(RuntimeError):
    pass


def verify_snapshot(snapshot):
    folder = Path(snapshot["folder"])
    if json.loads((folder / "approved-manifest.json").read_bytes()) != snapshot["manifest"]:
        raise ValueError("approved manifest file changed")
    if json.loads((folder / "approved-documents.json").read_bytes()) != snapshot["documents"]:
        raise ValueError("approved document snapshot changed")
    for item in snapshot["files"]:
        if file_hash(confined(folder, item["path"])) != item["sha256"]:
            raise ValueError("snapshot content changed")
    from workbench.backend.services.review_workflow import build_approved_manifest
    if build_approved_manifest(snapshot["project_id"], snapshot["documents"]) != snapshot["manifest"]:
        raise ValueError("snapshot review changed")


class DeepTutor:
    def __init__(self, base_url="http://127.0.0.1:8001", *, transport=None, python=None):
        parsed = urlsplit(base_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.path not in {"", "/"} or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("03 URL must be a local HTTP origin")
        self.base_url = base_url.rstrip("/")
        self.transport = transport
        self.python = Path(python) if python else PROJECT03 / ".venv/Scripts/python.exe"

    def _helper(self, payload):
        completed = subprocess.run([str(self.python), str(ROOT / "workbench/bridges/deeptutor03/local_rag.py")],
                                   input=canonical(payload), capture_output=True, timeout=90, cwd=PROJECT03)
        if completed.returncode:
            # Never expose config/provider credentials or arbitrary upstream logs.
            raise UpstreamError("03 local retrieval bridge failed; inspect local configuration")
        try:
            return json.loads(completed.stdout)
        except ValueError as error:
            raise UpstreamError("03 local bridge returned invalid JSON") from error

    def local_contract(self):
        return self._helper({"operation": "preflight"})

    def request(self, method, path, *, missing=False, raw=False, **kwargs):
        try:
            with httpx.Client(base_url=self.base_url, timeout=15, trust_env=False, follow_redirects=False, transport=self.transport) as client:
                response = client.request(method, path, **kwargs)
        except httpx.RequestError as error:
            raise UpstreamError("03 connection unavailable; no automatic resubmission") from error
        if missing and response.status_code == 404:
            return None
        if response.is_error or response.is_redirect:
            raise UpstreamError(f"03 HTTP {response.status_code}")
        if raw:
            return response.content
        try:
            return response.json()
        except ValueError as error:
            raise UpstreamError("03 returned invalid JSON") from error

    def info(self, name):
        info = self.request("GET", "/api/knowledge-bases/" + quote(name, safe=""), missing=True)
        if info is None:
            return None
        metadata = info.get("metadata") or {}
        statistics = info.get("statistics") or {}
        return dict(info, rag_provider=info.get("rag_provider") or metadata.get("rag_provider") or statistics.get("rag_provider"),
                    needs_reindex=bool(info.get("needs_reindex") or metadata.get("needs_reindex") or statistics.get("needs_reindex") or statistics.get("active_match") is False),
                    embedding_selection=info.get("embedding_selection") or metadata.get("embedding_selection"))

    def submit(self, snapshot, name, contract):
        verify_snapshot(snapshot)
        if self.local_contract() != contract:
            raise ValueError("local embedding configuration changed")
        files = [("files", (f["path"], confined(snapshot["folder"], f["path"]).read_bytes(), "text/markdown")) for f in snapshot["files"]]
        response = self.request("POST", "/api/knowledge-bases", data={"name": name, "rag_provider": "llamaindex", "embedding_model": json.dumps(contract["selection"])}, files=files)
        if not isinstance(response, dict) or not response.get("task_id"):
            raise UpstreamError("03 submission unconfirmed; do not automatically retry")
        return response["task_id"]

    def verify_ready(self, snapshot, name):
        verify_snapshot(snapshot)
        # Enumerate exact raw filenames, then compare bytes, not just the count.
        path = "/api/knowledge-bases/" + quote(name, safe="")
        listing = self.request("GET", path + "/files")
        rows = listing.get("files", []) if isinstance(listing, dict) else listing
        expected = {f["path"]: f["sha256"] for f in snapshot["files"]}
        actual = [r.get("name", r.get("filename", "")) for r in rows if r.get("type") != "folder" and not r.get("is_dir", False)]
        if len(actual) != len(set(actual)) or set(actual) != set(expected):
            raise ValueError("03 raw document set does not match approved snapshot")
        for filename, sha in expected.items():
            data = self.request("GET", path + "/files/" + quote(filename, safe=""), raw=True)
            if digest(data) != sha:
                raise ValueError("03 indexed input hash mismatch")
        return expected

    def search(self, snapshot, query, contract):
        verify_snapshot(snapshot)
        result = self._helper({"operation": "search", "kb_name": snapshot["external_kb_id"], "query": query, "contract": contract})
        if result.get("error_type") or result.get("needs_reindex") or result.get("warning"):
            raise UpstreamError("03 index is unavailable or embedding binding changed")
        return result
