import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from api.services import deeptutor_bridge as bridge
from api.routers import deeptutor as routes


@pytest.fixture
def ownership_file(monkeypatch, tmp_path):
    path = tmp_path / "ownership.json"
    monkeypatch.setattr(bridge, "OWNERSHIP_FILE", path)
    return path


@pytest.fixture(autouse=True)
def isolated_ownership(ownership_file):
    return ownership_file


@pytest.fixture
def upstream(monkeypatch):
    def install(handler):
        monkeypatch.setattr(
            bridge, "client",
            lambda: httpx.AsyncClient(base_url="http://127.0.0.1:8001", transport=httpx.MockTransport(handler)),
        )
    return install


@pytest.mark.asyncio
async def test_sync_creates_then_reuses_linked_kb(upstream, tmp_path):
    state = {"created": 0, "syncs": 0}

    def handle(request):
        path = request.url.path
        if request.method == "GET" and path.endswith("linked-folders"):
            return httpx.Response(200, json=[{"id": "folder", "path": str(tmp_path)}])
        if request.method == "GET":
            return httpx.Response(200, json={"status": "ready"}) if state["created"] else httpx.Response(404)
        if path == "/api/knowledge-bases":
            state["created"] += 1
            assert b"rag_provider=llamaindex" in request.content
            return httpx.Response(200, json={"task_id": None})
        if path.endswith("link-folder"):
            return httpx.Response(200, json={"id": "folder", "path": str(tmp_path)})
        assert path.endswith("sync-folder/folder")
        state["syncs"] += 1
        return httpx.Response(200, json={
            "task_id": "native-task" if state["syncs"] == 1 else None,
            "new_files": 2 if state["syncs"] == 1 else 0, "modified_files": 0,
        })

    upstream(handle)
    first = await bridge.sync("academic", tmp_path)
    second = await bridge.sync("academic", tmp_path)
    assert first["state"] == "submitted" and first["new_files"] == 2
    assert second["state"] == "unchanged"
    assert state["created"] == 1


@pytest.mark.asyncio
async def test_retry_recovers_owned_empty_kb_after_link_failure(upstream, tmp_path, ownership_file):
    state = {"created": False, "links": 0}

    def handle(request):
        path = request.url.path
        if request.method == "GET" and path.endswith("linked-folders"):
            return httpx.Response(200, json=[])
        if request.method == "GET":
            return httpx.Response(200, json={"status": "ready", "statistics": {"raw_documents": 0}}) if state["created"] else httpx.Response(404)
        if path == "/api/knowledge-bases":
            state["created"] = True
            return httpx.Response(200, json={})
        if path.endswith("link-folder"):
            state["links"] += 1
            if state["links"] == 1:
                return httpx.Response(500)
            return httpx.Response(200, json={"id": "folder"})
        return httpx.Response(200, json={"task_id": "task", "new_files": 1, "modified_files": 0})

    upstream(handle)
    with pytest.raises(HTTPException) as error:
        await bridge.sync("academic", tmp_path)
    assert error.value.status_code == 502
    assert ownership_file.is_file()
    # The next request must recover from disk, not process-local ownership state.
    result = await bridge.sync("academic", tmp_path)
    assert result["state"] == "submitted" and state["links"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("owned,count", [(False, 0), (True, 1), (True, None)])
async def test_unlinked_unknown_or_nonempty_kb_is_not_adopted(upstream, tmp_path, owned, count):
    if owned:
        bridge._record_created("ig04-academic", tmp_path)

    def handle(request):
        assert request.method == "GET", "Do not write to an unverified KB"
        if request.url.path.endswith("linked-folders"):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={"status": "ready", "statistics": {"raw_documents": count}})

    upstream(handle)
    with pytest.raises(HTTPException) as error:
        await bridge.sync("academic", tmp_path)
    assert error.value.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("busy", [True, False])
async def test_does_not_overwrite_busy_or_unrelated_kb(upstream, tmp_path, busy):
    def handle(request):
        assert request.method == "GET", "Must not mutate this KB"
        if request.url.path.endswith("linked-folders"):
            return httpx.Response(200, json=[{"path": str(tmp_path / "unrelated")}])
        return httpx.Response(200, json={"status": "processing" if busy else "ready"})

    upstream(handle)
    with pytest.raises(HTTPException) as error:
        await bridge.sync("social", tmp_path)
    assert error.value.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [500, 403])
async def test_upstream_error_body_is_not_exposed(upstream, tmp_path, status):
    upstream(lambda request: httpx.Response(status, text="private-provider-key"))
    with pytest.raises(HTTPException) as error:
        await bridge.sync("academic", tmp_path)
    assert error.value.status_code == 502
    assert "private-provider-key" not in error.value.detail


@pytest.mark.parametrize("url", ["https://example.com", "http://example.com", "http://user:secret@localhost:8001", "http://localhost:8001/path"])
def test_only_loopback_service_urls_are_accepted(monkeypatch, url):
    monkeypatch.setenv("DEEPTUTOR_API_URL", url)
    with pytest.raises(HTTPException):
        bridge.local_url("DEEPTUTOR_API_URL", "")


def test_status_reports_offline_and_rejects_unknown_vault(upstream, monkeypatch):
    def offline(request):
        raise httpx.ConnectError("offline", request=request)

    upstream(offline)
    monkeypatch.setattr(routes, "_vault_info", lambda kind: {"kind": kind, "available": False, "note_count": 0})
    app = FastAPI()
    app.include_router(routes.router)
    with TestClient(app) as client:
        assert client.get("/api/deeptutor/status").json()["available"] is False
        assert client.post("/api/deeptutor/sync/unknown").status_code == 422
        page = client.get("/deeptutor", follow_redirects=False)
        assert page.status_code == 307 and page.headers["location"] == "/?view=learning"


@pytest.mark.asyncio
async def test_missing_mutation_endpoint_is_reported_as_upstream_failure():
    transport = httpx.MockTransport(lambda req: httpx.Response(404))
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8001", transport=transport) as client:
        with pytest.raises(HTTPException) as error:
            await bridge.request(client, "POST", "/api/knowledge-bases")
    assert error.value.status_code == 502
