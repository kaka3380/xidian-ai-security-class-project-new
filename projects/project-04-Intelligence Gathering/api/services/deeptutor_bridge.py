"""Local HTTP adapter; DeepTutor owns knowledge-base indexing and task state."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import threading
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException

KB_NAMES = {"academic": "ig04-academic", "social": "ig04-social"}
_locks = {kind: threading.Lock() for kind in KB_NAMES}
OWNERSHIP_FILE = Path(__file__).resolve().parents[2] / "data" / "deeptutor" / "created-knowledge-bases.json"
_ownership_lock = threading.Lock()


def _ownership_key(name: str) -> str:
    return f"{local_url('DEEPTUTOR_API_URL', 'http://127.0.0.1:8001')}|{name}"


def _load_ownership() -> dict:
    try:
        data = json.loads(OWNERSHIP_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _record_created(name: str, vault: Path) -> None:
    with _ownership_lock:
        data = _load_ownership()
        data[_ownership_key(name)] = str(vault)
        OWNERSHIP_FILE.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=OWNERSHIP_FILE.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False)
            os.replace(temporary, OWNERSHIP_FILE)
        finally:
            Path(temporary).unlink(missing_ok=True)


def _can_resume_empty(name: str, vault: Path, info: dict) -> bool:
    return (
        _load_ownership().get(_ownership_key(name)) == str(vault)
        and info.get("statistics", {}).get("raw_documents") == 0
        and not info.get("read_only")
    )


def local_url(env_name: str, default: str) -> str:
    value = os.getenv(env_name, default).strip().rstrip("/")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path
    ):
        raise HTTPException(503, f"{env_name} 必须是本机 HTTP 服务地址（不含路径和凭据）")
    return value


def web_url() -> str:
    return local_url("DEEPTUTOR_WEB_URL", "http://127.0.0.1:3782")


def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=local_url("DEEPTUTOR_API_URL", "http://127.0.0.1:8001"),
        timeout=30,
        trust_env=False,
    )


async def request(client: httpx.AsyncClient, method: str, path: str, **kwargs):
    try:
        response = await client.request(method, path, **kwargs)
    except httpx.RequestError as exc:
        raise HTTPException(503, "无法连接项目03，请先运行03的 start-local.ps1") from exc
    if response.status_code == 404 and method == "GET":
        return None
    if response.is_error:
        # Do not relay upstream bodies that may contain local/provider secrets.
        raise HTTPException(502, f"项目03接口返回 HTTP {response.status_code}，请检查03运行日志")
    return response.json()


async def status() -> dict:
    async with client() as session:
        await request(session, "GET", "/health/ready")
        rows = await request(session, "GET", "/api/knowledge-bases") or []
    by_name = {row["name"]: row for row in rows}
    return {
        "available": True,
        "web_url": web_url(),
        "knowledge_bases": {
            kind: {
                "name": name,
                "connected": name in by_name,
                "status": by_name.get(name, {}).get("status", "not_connected"),
                "documents": by_name.get(name, {}).get("statistics", {}).get("raw_documents", 0),
                "progress": by_name.get(name, {}).get("progress"),
                "url": f"{web_url()}/knowledge-bases/{name}",
            }
            for kind, name in KB_NAMES.items()
        },
    }


async def sync(kind: str, vault: Path) -> dict:
    """Copy new/modified source documents through DeepTutor's linked-folder API."""
    vault = vault.resolve()
    lock = _locks[kind]
    if not lock.acquire(blocking=False):
        raise HTTPException(409, "该资料库正在提交同步，请稍后查看状态")
    try:
        name = KB_NAMES[kind]
        async with client() as session:
            path = f"/api/knowledge-bases/{name}"
            info = await request(session, "GET", path)
            if info is None:
                await request(
                    session, "POST", "/api/knowledge-bases",
                    data={"name": name, "rag_provider": "llamaindex"},
                )
                _record_created(name, vault)
            else:
                stage = (info.get("progress") or {}).get("stage")
                if info.get("status") in {"initializing", "processing", "indexing"} or (
                    stage and stage not in {"completed", "error", "idle"}
                ):
                    raise HTTPException(409, "03正在处理该知识库，请等待完成后再同步")
                folders = await request(session, "GET", f"{path}/linked-folders") or []
                if (not folders and not _can_resume_empty(name, vault, info)) or any(
                    Path(row["path"]).resolve() != vault for row in folders
                ):
                    raise HTTPException(409, f"{name} 已存在但不属于当前04资料目录，请保留并另行处理")
            folder = await request(session, "POST", f"{path}/link-folder", json={"folder_path": str(vault)})
            result = await request(session, "POST", f"{path}/sync-folder/{folder['id']}")
            if result is None:
                raise HTTPException(502, "03未返回同步任务，请检查知识库状态")
        return {
            "kind": kind,
            "knowledge_base": name,
            "state": "submitted" if result.get("task_id") else "unchanged",
            "task_id": result.get("task_id"),
            "new_files": result["new_files"],
            "modified_files": result["modified_files"],
            "url": f"{web_url()}/knowledge-bases/{name}",
        }
    finally:
        lock.release()
