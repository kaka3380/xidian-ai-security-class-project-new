"""Offline boundary tests for archive sync; no real KB, credentials or models."""

import asyncio
import hashlib
import json
import threading

import httpx
import pytest
from fastapi import HTTPException

from api.services import deeptutor_bridge as bridge


@pytest.fixture(autouse=True)
def isolated_bridge(monkeypatch, tmp_path):
    monkeypatch.setenv("DEEPTUTOR_API_URL", "http://127.0.0.1:8001")
    monkeypatch.setenv("DEEPTUTOR_WEB_URL", "http://127.0.0.1:3782")
    monkeypatch.setattr(bridge, "OWNERSHIP_FILE", tmp_path / "ownership.json")
    monkeypatch.setattr(bridge, "_locks", {kind: threading.Lock() for kind in bridge.KB_NAMES})


@pytest.fixture
def upstream(monkeypatch):
    def install(handler):
        monkeypatch.setattr(
            bridge,
            "client",
            lambda: httpx.AsyncClient(
                base_url="http://127.0.0.1:8001",
                transport=httpx.MockTransport(handler),
                trust_env=False,
            ),
        )

    return install


def linked_reply(request, vault):
    """Model the existing linked-folder API, not the bridge's implementation."""
    path = request.url.path
    if request.method == "GET" and path.endswith("/linked-folders"):
        return httpx.Response(200, json=[{"id": "folder", "path": str(vault)}])
    if request.method == "GET":
        return httpx.Response(200, json={"status": "ready", "statistics": {"raw_documents": 1}})
    if path.endswith("/link-folder"):
        assert json.loads(request.content)["folder_path"] == str(vault.resolve())
        return httpx.Response(200, json={"id": "folder"})
    assert path.endswith("/sync-folder/folder")
    return httpx.Response(200, json={"task_id": "index-task", "new_files": 0, "modified_files": 1})


@pytest.mark.asyncio
async def test_failed_creation_does_not_claim_ownership_and_can_retry(upstream, tmp_path):
    attempts = 0
    links = 0

    def handle(request):
        nonlocal attempts, links
        if request.method == "GET":
            return httpx.Response(404)
        if request.url.path == "/api/knowledge-bases":
            attempts += 1
            return httpx.Response(503 if attempts == 1 else 200, json={})
        if request.url.path.endswith("/link-folder"):
            links += 1
            return httpx.Response(200, json={"id": "folder"})
        assert request.url.path.endswith("/sync-folder/folder")
        return httpx.Response(200, json={"task_id": "task", "new_files": 1, "modified_files": 0})

    upstream(handle)
    with pytest.raises(HTTPException) as error:
        await bridge.sync("academic", tmp_path)
    assert error.value.status_code == 502
    assert not bridge.OWNERSHIP_FILE.exists()
    assert links == 0
    result = await bridge.sync("academic", tmp_path)
    assert result["state"] == "submitted"
    assert attempts == 2 and links == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "http_error"])
async def test_sync_failure_releases_lock_and_reuses_existing_kb(upstream, tmp_path, failure):
    sync_attempts = 0

    def handle(request):
        nonlocal sync_attempts
        assert request.url.path != "/api/knowledge-bases", "Retry must not create another KB"
        if request.url.path.endswith("/sync-folder/folder"):
            sync_attempts += 1
            if sync_attempts == 1:
                if failure == "timeout":
                    raise httpx.ReadTimeout("private-provider-key", request=request)
                return httpx.Response(500, text="private-provider-key")
        return linked_reply(request, tmp_path)

    upstream(handle)
    with pytest.raises(HTTPException) as error:
        await bridge.sync("academic", tmp_path)
    assert error.value.status_code == (503 if failure == "timeout" else 502)
    assert "private-provider-key" not in error.value.detail
    result = await bridge.sync("academic", tmp_path)
    assert result["state"] == "submitted" and sync_attempts == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["read_only", "other_vault", "other_backend"])
async def test_owned_empty_kb_requires_same_backend_vault_and_write_access(
    upstream, monkeypatch, tmp_path, boundary
):
    bridge._record_created("ig04-academic", tmp_path)
    vault = tmp_path / "other" if boundary == "other_vault" else tmp_path
    if boundary == "other_backend":
        monkeypatch.setenv("DEEPTUTOR_API_URL", "http://127.0.0.1:9001")

    def handle(request):
        assert request.method == "GET", "Ownership mismatch must never mutate upstream"
        if request.url.path.endswith("/linked-folders"):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={
            "status": "ready", "statistics": {"raw_documents": 0},
            "read_only": boundary == "read_only",
        })

    upstream(handle)
    with pytest.raises(HTTPException) as error:
        await bridge.sync("academic", vault)
    assert error.value.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("contents", ["{broken", "[]", "null"])
async def test_corrupt_ownership_never_adopts_an_unlinked_kb(upstream, tmp_path, contents):
    bridge.OWNERSHIP_FILE.write_text(contents, encoding="utf-8")

    def handle(request):
        assert request.method == "GET"
        if request.url.path.endswith("/linked-folders"):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={"status": "ready", "statistics": {"raw_documents": 0}})

    upstream(handle)
    with pytest.raises(HTTPException) as error:
        await bridge.sync("academic", tmp_path)
    assert error.value.status_code == 409
    assert bridge.OWNERSHIP_FILE.read_text(encoding="utf-8") == contents


@pytest.mark.asyncio
@pytest.mark.parametrize("status,stage", [
    ("initializing", None), ("indexing", None), ("ready", "embedding"),
])
async def test_pending_index_blocks_resubmission_even_if_status_says_ready(upstream, tmp_path, status, stage):
    def handle(request):
        assert request.method == "GET"
        assert request.url.path.endswith("/ig04-academic")
        return httpx.Response(200, json={"status": status, "progress": {"stage": stage}})

    upstream(handle)
    with pytest.raises(HTTPException) as error:
        await bridge.sync("academic", tmp_path)
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_simultaneous_sync_for_same_vault_is_rejected_before_upstream_call(upstream, tmp_path):
    entered = asyncio.Event()
    release = asyncio.Event()
    starts = 0
    submissions = 0

    async def handle(request):
        nonlocal starts, submissions
        if request.method == "GET" and request.url.path.endswith("/ig04-academic"):
            starts += 1
            entered.set()
            await asyncio.wait_for(release.wait(), timeout=3)
        if request.url.path.endswith("/sync-folder/folder"):
            submissions += 1
        return linked_reply(request, tmp_path)

    upstream(handle)
    first = asyncio.create_task(bridge.sync("academic", tmp_path))
    try:
        await asyncio.wait_for(entered.wait(), timeout=3)
        with pytest.raises(HTTPException) as error:
            await bridge.sync("academic", tmp_path)
        assert error.value.status_code == 409
        assert starts == 1 and submissions == 0
    finally:
        release.set()
        result = await asyncio.wait_for(first, timeout=3)
    assert result["state"] == "submitted" and submissions == 1


@pytest.mark.asyncio
async def test_different_vaults_do_not_share_submission_lock(upstream, tmp_path):
    academic_started = asyncio.Event()
    release_academic = asyncio.Event()

    async def handle(request):
        if request.method == "GET" and request.url.path.endswith("/ig04-academic"):
            academic_started.set()
            await asyncio.wait_for(release_academic.wait(), timeout=3)
        return linked_reply(request, tmp_path)

    upstream(handle)
    first = asyncio.create_task(bridge.sync("academic", tmp_path))
    try:
        await asyncio.wait_for(academic_started.wait(), timeout=3)
        social = await asyncio.wait_for(bridge.sync("social", tmp_path), timeout=3)
        assert social["knowledge_base"] == "ig04-social"
        assert social["state"] == "submitted"
    finally:
        release_academic.set()
        await asyncio.wait_for(first, timeout=3)


@pytest.mark.asyncio
async def test_sync_preserves_source_files_and_reports_submission_not_completion(upstream, tmp_path):
    source = tmp_path / "来源卡.md"
    source.write_text("# 来源卡\n摘要级资料；不是论文全文。\n", encoding="utf-8")
    original = hashlib.sha256(source.read_bytes()).hexdigest()
    upstream(lambda request: linked_reply(request, tmp_path))

    result = await bridge.sync("academic", tmp_path / ".")

    assert result["state"] == "submitted"
    assert result["task_id"] == "index-task"
    assert result["new_files"] == 0 and result["modified_files"] == 1
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original


@pytest.mark.asyncio
async def test_status_preserves_index_progress_and_missing_kb(upstream):
    progress = {"stage": "embedding", "current": 2, "total": 5}

    def handle(request):
        assert request.method == "GET"
        if request.url.path == "/health/ready":
            return httpx.Response(200, json={"status": "ready"})
        assert request.url.path == "/api/knowledge-bases"
        return httpx.Response(200, json=[{
            "name": "ig04-academic", "status": "indexing",
            "statistics": {"raw_documents": 5}, "progress": progress,
        }, {"name": "unrelated", "status": "ready"}])

    upstream(handle)
    result = await bridge.status()
    academic = result["knowledge_bases"]["academic"]
    social = result["knowledge_bases"]["social"]
    assert academic["status"] == "indexing" and academic["progress"] == progress
    assert academic["documents"] == 5
    assert social["connected"] is False and social["status"] == "not_connected"


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8001/", "http://localhost:8001", "http://[::1]:8001",
])
def test_loopback_configuration_accepts_supported_local_addresses(monkeypatch, url):
    monkeypatch.setenv("DEEPTUTOR_API_URL", url)
    assert bridge.local_url("DEEPTUTOR_API_URL", "") == url.rstrip("/")


@pytest.mark.parametrize("url", [
    "http://localhost.example.com:8001", "http://127.0.0.1:8001?token=private",
    "http://127.0.0.1:8001#private", "http://localhost@outside.invalid:8001",
])
def test_ambiguous_service_urls_are_rejected_without_echoing_credentials(monkeypatch, url):
    monkeypatch.setenv("DEEPTUTOR_API_URL", url)
    with pytest.raises(HTTPException) as error:
        bridge.local_url("DEEPTUTOR_API_URL", "")
    assert error.value.status_code == 503
    assert url not in error.value.detail
