# -*- coding: utf-8 -*-
"""Bridge to the local AI-Scientist-v2 framework.

This router reads the AI-Scientist workspace and research_platform SQLite store
without importing its heavy dependencies, and manages production experiment
runs as background subprocesses.
"""

from __future__ import annotations

import json
import os
import re
import signal
import shutil
import sqlite3
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from pipeline.research_intelligence import context_snapshot as intelligence_context_snapshot
from pipeline.research_intelligence import overview_counts as intelligence_overview_counts
from pipeline.research_intelligence import (
    ACADEMIC_VAULT_ENV,
    SOCIAL_VAULT_ENV,
    import_existing_analyses,
    record_generated_ideas,
    sync_obsidian,
)
from pipeline.analysis_service import available_themes
from pipeline.ai_response import AIResponseError, chat_content
from pipeline.topic_taxonomy import canonical_topic

router = APIRouter(prefix="/scientist", tags=["scientist"])

DEFAULT_ROOT = str(Path(__file__).resolve().parents[2] / "external" / "AI-Scientist")
MEDIA_ROOT = Path(__file__).resolve().parents[2]
GENERATED_IDEAS_DIR = MEDIA_ROOT / "data" / "scientist" / "ideas"
GENERATED_WORKSHOPS_DIR = MEDIA_ROOT / "data" / "scientist" / "workshops"
RUNTIME_IDEAS_DIR = MEDIA_ROOT / "data" / "scientist" / "run_ideas"
MEDIA_ANALYSIS_DIR = MEDIA_ROOT / "data" / "analysis"
RUNNING_PROCESSES: dict[str, subprocess.Popen] = {}
RUNNING_PROCESSES_LOCK = threading.Lock()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def scientist_root() -> Path:
    root = os.getenv("AI_SCIENTIST_ROOT", "").strip() or DEFAULT_ROOT
    return Path(root).expanduser().resolve()


def _is_within(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory)
        return True
    except ValueError:
        return False


def scientist_python() -> str:
    configured = os.getenv("AI_SCIENTIST_PYTHON", "").strip()
    if configured:
        return configured
    venv = scientist_root() / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return str(venv) if venv.is_file() else sys.executable


def _db_path() -> Path:
    return scientist_root() / "data" / "research_platform.sqlite3"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def _list_table(table: str, limit: int = 100) -> list[dict[str, Any]]:
    if not _db_path().is_file():
        return []
    try:
        conn = _connect()
        try:
            rows = conn.execute(
                f"SELECT payload FROM {table} ORDER BY rowid DESC LIMIT ?", (limit,)
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return []
    return [json.loads(row["payload"]) for row in rows]


def _get_task(task_id: str) -> dict[str, Any] | None:
    for task in _list_table("production_tasks", 500):
        if task.get("id") == task_id:
            return task
    return None


def _pid_is_running(pid: Any) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _reconcile_task(task: dict[str, Any]) -> bool:
    """Repair persisted task state after an API restart or a lost worker thread."""
    if task.get("status") not in {"queued", "running"}:
        return False
    if _pid_is_running(task.get("pid")):
        return False
    task["status"] = "cancelled" if task.get("cancel_requested") else "failed"
    task["finished_at"] = task.get("finished_at") or utc_now()
    task["error"] = task.get("error") or ("任务已取消" if task.get("cancel_requested") else "任务进程已结束；后端重启后无法取得退出码。")
    _save_task(task)
    return True


def _reconcile_production_tasks() -> None:
    for task in _list_table("production_tasks", 500):
        _reconcile_task(task)


def _save_task(task: dict[str, Any]) -> None:
    _db_path().parent.mkdir(parents=True, exist_ok=True)
    conn = _connect()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS production_tasks (id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT OR REPLACE INTO production_tasks(id, payload) VALUES (?, ?)",
            (task["id"], json.dumps(task, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()


def _tail_log(path: str | Path, max_chars: int = 12000) -> str:
    log_path = Path(path)
    if not log_path.is_file():
        return "任务尚未写入日志。"
    text = log_path.read_text(encoding="utf-8", errors="replace")
    return text[-max_chars:]


def _check_codex(root: Path) -> dict[str, Any]:
    executable = shutil.which("codex")
    if not executable:
        return {"name": "codex_cli", "ok": True, "message": "Idea 生成使用配置的 API，未使用 Codex CLI。", "severity": "info"}
    config = root / ".codex-home" / "config.toml"
    if not config.is_file():
        return {"name": "codex_config", "ok": True, "message": "Idea 生成不需要 Codex CLI 配置。", "severity": "info"}
    if "<PROJECT_ROOT>" in config.read_text(encoding="utf-8", errors="replace"):
        return {"name": "codex_config", "ok": True, "message": "Idea 生成不读取 Codex CLI 配置。", "severity": "info"}
    try:
        result = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=15)
    except OSError as exc:
        return {"name": "codex_cli", "ok": False, "message": f"无法执行 Codex CLI：{exc}", "severity": "error"}
    version = (result.stdout or result.stderr).strip().splitlines()[:1]
    return {"name": "codex_cli", "ok": result.returncode == 0, "message": f"Codex CLI：{' '.join(version) or 'unknown'}", "severity": "info" if result.returncode == 0 else "error"}


def _preflight() -> dict[str, Any]:
    root = scientist_root()
    workspace = root / "AI-Scientist-v2_workspace"
    checks: list[dict[str, Any]] = []

    def item(name: str, ok: bool, message: str, severity: str = "error") -> dict[str, Any]:
        return {"name": name, "ok": ok, "message": message, "severity": severity}

    checks.append(item("project_root", (root / "launch_scientist_bfts.py").is_file(), "AI Scientist 入口存在。" if (root / "launch_scientist_bfts.py").is_file() else "未找到 launch_scientist_bfts.py。"))
    for directory in ("data", "framework_repos", "experiments", "logs", "workspaces"):
        path = workspace / directory
        checks.append(item(f"workspace_{directory}", path.is_dir(), f"工作区目录：{path}"))
    data_dir = workspace / "data"
    has_dataset = data_dir.is_dir() and any(data_dir.iterdir())
    checks.append(item("dataset", has_dataset, "已配置数据集。" if has_dataset else "AI-Scientist-v2_workspace/data/ 中没有数据集。", "warning" if not has_dataset else "info"))

    resources = root / "data" / "resources.json"
    try:
        resource_data = json.loads(resources.read_text(encoding="utf-8"))
        configured = [name for name in resource_data.get("datasets", {}) if name != "example_dataset"]
        checks.append(item("resource_manifest", bool(configured), "已登记数据集：" + ", ".join(configured) if configured else "data/resources.json 仅包含示例数据集；已选 GitHub 基础项目时可由 Framework Mode 自行准备资源。", "warning" if not configured else "info"))
    except (OSError, json.JSONDecodeError) as exc:
        checks.append(item("resource_manifest", False, f"无法读取 data/resources.json：{exc}"))

    checks.append(_check_codex(root))
    config = root / "bfts_config.yaml"
    config_text = config.read_text(encoding="utf-8", errors="replace") if config.is_file() else ""
    checks.append(item("python_path", "/path/to/your/python" not in config_text, "bfts_config.yaml 的 Python 路径已配置。" if "/path/to/your/python" not in config_text else "bfts_config.yaml 仍使用占位 Python 路径。"))

    errors = [check for check in checks if not check["ok"] and check.get("severity", "error") == "error"]
    warnings = [check for check in checks if not check["ok"] and check.get("severity", "error") == "warning"]
    return {"ready": not errors, "root": str(root), "python": scientist_python(), "workspace": str(workspace), "checks": checks, "errors": errors, "warnings": warnings}


class ProductionRequest(BaseModel):
    idea_file: str = Field(min_length=1, max_length=500)
    idea_idx: int = Field(default=0, ge=0)
    mode: str = Field(default="experiment", pattern="^(experiment|full)$")
    attempt_id: int = Field(default=1, ge=1)
    base_project: str = Field(default="", max_length=500)


class GenerateIdeasRequest(BaseModel):
    count: int = Field(default=5, ge=1, le=10)
    theme: str = Field(default="", max_length=100)
    strategy: str = Field(default="v2_reflection", pattern="^(v2_reflection|v2_novelty)$")
    reflections: int = Field(default=2, ge=1, le=4)
    allow_rule_fallback: bool = False


class VaultConfigRequest(BaseModel):
    path: str = Field(min_length=1, max_length=1000)


class VaultsConfigRequest(BaseModel):
    academic_path: str = Field(min_length=1, max_length=1000)
    social_path: str = Field(min_length=1, max_length=1000)


class VaultReportRenameRequest(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    title: str = Field(min_length=1, max_length=160)


def _analysis_meta_files(limit: int = 12) -> list[Path]:
    if not MEDIA_ANALYSIS_DIR.is_dir():
        return []
    return sorted(
        (path for path in MEDIA_ANALYSIS_DIR.glob("*.meta.json") if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[:limit]


def _load_media_intelligence(direction_limit: int = 20) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    directions: list[dict[str, Any]] = []
    topics: list[dict[str, Any]] = []
    seen_directions: set[str] = set()
    seen_topics: set[str] = set()

    for path in _analysis_meta_files():
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        intelligence = meta.get("intelligence") or {}
        for direction in intelligence.get("research_directions", []):
            if not isinstance(direction, dict):
                continue
            key = str(direction.get("research_direction") or direction.get("topic") or "").strip()
            if not key or key in seen_directions:
                continue
            seen_directions.add(key)
            direction.setdefault("source_file", meta.get("source_file", ""))
            direction.setdefault("output_file", meta.get("output_file", ""))
            directions.append(direction)
        for topic in intelligence.get("topics", []):
            if not isinstance(topic, dict):
                continue
            key = str(topic.get("topic") or "").strip()
            if not key or key in seen_topics:
                continue
            seen_topics.add(key)
            topics.append(topic)

    directions.sort(key=lambda item: (_to_number(item.get("score")), _to_number(item.get("content_count"))), reverse=True)
    topics.sort(key=lambda item: (_to_number(item.get("content_count")), _to_number(item.get("quality_score"))), reverse=True)
    return directions[:direction_limit], topics[:direction_limit]


def _to_number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _context_snapshot(
    trend_limit: int = 6,
    record_limit: int = 12,
    candidate_limit: int = 12,
    direction_limit: int = 20,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    intelligence = intelligence_context_snapshot(max(trend_limit, record_limit, candidate_limit))
    trends = intelligence["trends"][:trend_limit] or _list_table("trends", trend_limit)
    records = intelligence["documents"][:record_limit] or _list_table("records", record_limit)
    candidates = intelligence["candidates"][:candidate_limit] or _list_table("candidates", candidate_limit)
    media_directions, media_topics = _load_media_intelligence(direction_limit)
    return trends, records, candidates, media_directions, media_topics


class IdeaGenerationError(RuntimeError):
    """A user-actionable failure from the configured idea-generation API."""


async def _call_llm(payload: dict[str, Any]) -> str:
    api_key = os.getenv("AI_API_KEY", "").strip()
    base_url = os.getenv("AI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.getenv("AI_MODEL", "gpt-5.6-sol")
    if not api_key:
        raise IdeaGenerationError("未配置 AI_API_KEY，无法使用 AI 生成。")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=10.0)) as client:
            response = await client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "temperature": 0.6,
                    "stream": False,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": "你是 AI-Scientist-v2 风格的严谨人工智能安全研究员。只依据给定材料提出可验证、彼此差异明显的研究想法；拒绝空泛的“评测与防御”套话。每个想法必须指出具体攻击面或机制、可操纵变量、可获得的数据/基准、对照基线和可量化指标。"},
                        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                    ],
                },
            )
            response.raise_for_status()
            return chat_content(response)
    except IdeaGenerationError:
        raise
    except AIResponseError as exc:
        raise IdeaGenerationError(str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        raise IdeaGenerationError(f"AI 接口请求失败（HTTP {exc.response.status_code}）。请检查 DeepSeek 密钥、余额和模型权限。") from exc
    except httpx.RequestError as exc:
        raise IdeaGenerationError(f"无法连接 AI 接口：{exc.__class__.__name__}。请检查网络和 AI_BASE_URL。") from exc
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise IdeaGenerationError("AI 接口响应格式不符合 Chat Completions 规范。") from exc


def _seed_from_direction(direction: dict[str, Any]) -> dict[str, Any]:
    label = _clean_text(direction.get("research_direction") or direction.get("topic") or "人工智能安全")
    return {
        "source": "crawler",
        "label": label,
        "topic": _clean_text(direction.get("topic") or label),
        "summary": _clean_text(direction.get("why_now")),
        "keywords": direction.get("evidence_topics") or direction.get("recommended_methods") or [],
        "questions": direction.get("research_questions") or [],
        "methods": direction.get("recommended_methods") or [],
        "score": _to_number(direction.get("score"), 50),
        "source_file": direction.get("source_file", ""),
    }


def _seed_from_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    title = _clean_text(candidate.get("title") or candidate.get("question") or "人工智能安全候选方向")
    return {
        "source": "candidate",
        "label": title,
        "topic": _clean_text(candidate.get("title") or title),
        "summary": _clean_text(candidate.get("question") or candidate.get("description")),
        "keywords": candidate.get("tags") or [],
        "questions": [str(candidate.get("question"))] if candidate.get("question") else [],
        "methods": candidate.get("recommended_methods") or [],
        "score": _to_number(candidate.get("score"), 60),
        "source_file": "",
    }


def _seed_from_trend(trend: dict[str, Any]) -> dict[str, Any]:
    label = _clean_text(trend.get("label") or "人工智能安全")
    return {
        "source": "trend",
        "label": label,
        "topic": label,
        "summary": _clean_text(trend.get("summary")),
        "keywords": trend.get("keywords") or [],
        "questions": [],
        "methods": [],
        "score": _to_number(trend.get("score"), 30),
        "source_file": "",
    }


def _fallback_ideas(
    count: int,
    trends: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    directions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    seeds: list[dict[str, Any]] = []
    for direction in directions:
        seeds.append(_seed_from_direction(direction))
    for candidate in candidates:
        seeds.append(_seed_from_candidate(candidate))
    for trend in trends:
        seeds.append(_seed_from_trend(trend))

    if not seeds:
        seeds.append({
            "source": "fallback",
            "label": "人工智能安全",
            "topic": "人工智能安全",
            "summary": "从公开内容中提取人工智能安全风险信号。",
            "keywords": ["人工智能安全", "评测", "防御"],
            "questions": ["如何建立可复现的人工智能安全风险评测基准？"],
            "methods": ["跨平台证据对照", "构造可复现实验集"],
            "score": 50,
            "source_file": "",
        })

    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for seed in seeds:
        key = str(seed.get("label") or seed.get("topic") or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(seed)

    source_order = {"crawler": 0, "candidate": 1, "trend": 2, "fallback": 3}
    unique.sort(key=lambda seed: (source_order.get(str(seed.get("source")), 9), -_to_number(seed.get("score"), 0)))
    chosen = unique[:max(1, min(count, len(unique)))]
    return [_idea_from_seed(seed, index) for index, seed in enumerate(chosen)]


def _research_title(label: str, topic: str) -> str:
    label = _clean_text(label)
    if label.endswith(" 的安全评测与防御"):
        label = label[: -len(" 的安全评测与防御")] + "的安全评测与防御"
    if "研究" in label or "评测" in label or "防御" in label:
        return label
    return f"{label} 的安全评测与防御研究"


def _idea_from_seed(seed: dict[str, Any], index: int) -> dict[str, Any]:
    topic = _clean_text(seed.get("topic") or seed.get("label") or "人工智能安全")
    label = _clean_text(seed.get("label") or topic)
    summary = _clean_text(seed.get("summary"))
    keywords = [_clean_text(item) for item in (seed.get("keywords") or []) if _clean_text(item)]
    questions = [_clean_text(item) for item in (seed.get("questions") or []) if _clean_text(item)]
    methods = [_clean_text(item) for item in (seed.get("methods") or []) if _clean_text(item)]
    if not questions:
        questions = [f"如何建立可复现的{topic}风险评测基准？", f"现有防御方法在真实应用场景中的有效性如何？"]
    if not methods:
        methods = ["跨平台证据对照", "构造可复现实验集", "比较攻击成功率与任务性能"]
    if not keywords:
        keywords = [topic]

    evidence = "；".join(summary.splitlines()) if summary else "；".join(keywords)
    title = _research_title(label, topic)
    return {
        "Name": f"generated_{index:02d}",
        "Title": title,
        "Short Hypothesis": f"围绕 {topic} 建立可复现评测并验证代表性防御策略，能够揭示公开讨论中被忽视的可靠性差距与真实风险边界。",
        "Related Work": evidence,
        "Abstract": f"以近期公开热点 {topic} 为切入，构建统一评测基准与对照实验，回答：{ '；'.join(questions) }",
        "Experiments": "；".join(methods) + "。同时报告消融结果、负面结果与失败案例。",
        "Risk Factors and Limitations": "公开热点不等同于科学证据；数据可得性、评测集偏差、防御方法泛化性与伦理边界均需人工复核。",
        "github_repos": [],
        "_source": seed.get("source", ""),
        "_score": _to_number(seed.get("score"), 0),
        "_evidence": keywords[:6],
        "_source_file": seed.get("source_file", ""),
    }


def _matches_theme(item: dict[str, Any], theme: str) -> bool:
    if not theme:
        return True
    selected = canonical_topic(theme)
    if str(item.get("topic_id") or "") == selected["id"]:
        return True
    for mapping in item.get("topic_mappings") or []:
        if str(mapping.get("topic_id") or "") == selected["id"]:
            return True
    values = [
        item.get("topic"), item.get("label"), item.get("title"), item.get("research_direction"),
        *(item.get("domains") or []), *(item.get("tags") or []), *(item.get("keywords") or []),
        *(item.get("evidence_topics") or []),
    ]
    return any(theme.casefold() in str(value).casefold() for value in values if value)


def _filter_idea_context(theme: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    trends, records, candidates, media_directions, media_topics = _context_snapshot()
    if theme:
        trends = [item for item in trends if _matches_theme(item, theme)]
        records = [item for item in records if _matches_theme(item, theme)]
        candidates = [item for item in candidates if _matches_theme(item, theme)]
        media_directions = [item for item in media_directions if _matches_theme(item, theme)]
        media_topics = [item for item in media_topics if _matches_theme(item, theme)]
    return trends, records, candidates, media_directions, media_topics


def _basis_preview(theme: str = "") -> dict[str, Any]:
    theme = _clean_text(theme)
    trends, records, candidates, directions, topics = _filter_idea_context(theme)
    selected = canonical_topic(theme) if theme else None
    crawler_score = round(min(100, 20 + len(records) * 6 + len(trends) * 10 + len(directions) * 8), 1)
    academic_score = round(min(100, 15 + sum(1 for record in records if str(record.get("platform")) in {"arxiv", "github"}) * 18 + len(candidates) * 7), 1)
    fusion_score = round(0.65 * academic_score + 0.35 * crawler_score, 1)
    cards = []
    for trend in trends[:4]:
        cards.append({"kind": "热点趋势", "title": trend.get("label") or trend.get("topic"), "detail": f"{trend.get('document_count', 0)} 条文档 · {trend.get('evidence_count', 0)} 条证据 · {trend.get('trend', 'stable')}", "url": ""})
    for direction in directions[:4]:
        cards.append({"kind": "研究方向", "title": direction.get("research_direction") or direction.get("topic"), "detail": direction.get("why_now") or "来自已分析的采集资料", "url": ""})
    for record in records[:4]:
        cards.append({"kind": "采集证据", "title": record.get("title") or "未命名记录", "detail": str(record.get("summary") or record.get("text") or "")[:140], "url": record.get("source_url") or ""})
    return {
        "theme": theme or "全部主题",
        "topic_alignment": {"topic_id": selected["id"], "label": selected["label"], "taxonomy_version": selected["version"], "match_method": "canonical topic_id + alias fallback"} if selected else {"topic_id": "all", "label": "全部主题", "taxonomy_version": "mixed", "match_method": "all canonical topics"},
        "fusion_scores": {"ai_scientist_academic": academic_score, "mediacrawler_evidence": crawler_score, "combined": fusion_score, "weights": {"ai_scientist_academic": 0.65, "mediacrawler_evidence": 0.35}},
        "counts": {"trends": len(trends), "records": len(records), "candidates": len(candidates), "directions": len(directions), "topics": len(topics)},
        "cards": cards,
        "note": "主题先以稳定 topic_id 对齐，再以别名和标签兜底；AI-Scientist-v2 负责学术可行性与新颖性，MediaCrawler 负责现实热点与可追溯证据。",
    }


def _v2_knowledge_base_dir() -> Path:
    configured = os.getenv("AI_SCIENTIST_KNOWLEDGE_BASE", "").strip()
    return Path(configured).expanduser() if configured else scientist_root() / "knowledge_base"


def _build_workshop(theme: str, records: list[dict[str, Any]], directions: list[dict[str, Any]], topics: list[dict[str, Any]]) -> tuple[Path, str, list[str]]:
    label = theme or _clean_text((directions[0].get("topic") if directions else "")) or "人工智能安全"
    canonical = canonical_topic(label)
    direction = directions[0] if directions else {}
    keywords = list(dict.fromkeys([label, *(direction.get("evidence_topics") or []), *(topics[0].get("representative_terms") or [] if topics else [])]))[:10]
    references = list(dict.fromkeys(_clean_text(item.get("title")) for item in records if _clean_text(item.get("title"))))[:8]
    why_now = _clean_text(direction.get("why_now")) or f"基于近期已采集的 {label} 讨论、研究记录与风险证据，识别可验证的研究缺口。"
    questions = "；".join(_clean_text(item) for item in (direction.get("research_questions") or [])[:3]) or f"如何为 {label} 建立可复现、可比较的安全评测与防御研究框架？"
    reference_lines = [*(f"- {title}" for title in references)] if references else ["- 暂无可用的已分析文献标题；生成时不得虚构参考文献。"]
    content = "\n".join([
        f"# Title: {canonical['label']} 的安全研究方向",
        "", "## Topic Alignment", f"topic_id: {canonical['id']}", f"taxonomy_version: {canonical['version']}", "mapping: canonical topic_id + aliases",
        "", "## Keywords", ", ".join(keywords) or label,
        "", "## TL;DR", questions,
        "", "## Abstract",
        f"{why_now} 本主题文件由 MediaCrawler 已完成分析的研究情报自动构建，作为 AI-Scientist-v2 的研究范围输入。后续 proposal 必须将上述问题转化为可证伪假设，并使用可复现实验、基线比较和明确指标进行验证。",
        "", "## References", *reference_lines, "",
    ])
    GENERATED_WORKSHOPS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = GENERATED_WORKSHOPS_DIR / f"workshop_{stamp}_{re_slug(label)}.md"
    path.write_text(content, encoding="utf-8")
    return path, content, references


def _load_v2_knowledge_base(references: list[str], theme: str = "") -> tuple[list[dict[str, str]], str, str]:
    kb_dir = _v2_knowledge_base_dir()
    index_path = kb_dir / "index.json"
    if not index_path.is_file():
        return [], "", f"知识库不可用：未找到 {index_path}"
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [], "", "知识库索引无法读取。"
    if not isinstance(index, list):
        return [], "", "知识库索引格式不正确。"
    selected = canonical_topic(theme) if theme else None
    reference_terms = [item.casefold() for item in references if item]
    matched: list[dict[str, str]] = []
    summaries: list[str] = []
    ranked: list[tuple[int, dict[str, Any]]] = []
    for entry in index:
        title = str(entry.get("title_lower") or entry.get("title") or "").casefold()
        score = sum(4 for ref in reference_terms if ref and (ref in title or title in ref))
        if selected and entry.get("topic_id") == selected["id"]:
            score += 3
        if score:
            ranked.append((score, entry))
    for _, entry in sorted(ranked, key=lambda item: (item[0], str(item[1].get("updated_at", ""))), reverse=True):
        path = kb_dir / str(entry.get("md_file", ""))
        if not path.is_file():
            continue
        try:
            summary = path.read_text(encoding="utf-8", errors="replace")[:5000]
        except OSError:
            continue
        title = str(entry.get("title") or path.stem)
        matched.append({"title": title, "path": str(path)})
        summaries.append(f"## {title}\n{summary}")
        if len(matched) >= 4:
            break
    return matched, "\n\n".join(summaries), (f"已匹配 {len(matched)} 篇 AI-Scientist-v2 知识库论文。" if matched else "知识库中没有与本次主题文件参考文献匹配的论文。")


def _openalex_abstract(item: dict[str, Any]) -> str:
    inverted = item.get("abstract_inverted_index") or {}
    if not isinstance(inverted, dict):
        return ""
    positions = [(position, word) for word, indexes in inverted.items() if isinstance(indexes, list) for position in indexes if isinstance(position, int)]
    return " ".join(word for _, word in sorted(positions))[:8000]


def _academic_query(theme: str, directions: list[dict[str, Any]]) -> str:
    topic = canonical_topic(theme or str(directions[0].get("topic") if directions else ""))
    hints = {
        "ai-safety.rag-security": "RAG security retrieval augmented generation",
        "ai-safety.prompt-injection": "prompt injection LLM security",
        "ai-safety.agent-tool-security": "AI agent tool use security",
        "ai-safety.adversarial-jailbreak": "LLM jailbreak adversarial attacks",
        "ai-safety.alignment-reliability": "AI alignment reliability safety",
        "ai-safety.multimodal-security": "multimodal model security",
        "ai-safety.privacy-unlearning": "machine unlearning privacy AI",
        "ai-safety.data-supply-chain": "machine learning data poisoning supply chain security",
        "ai-safety.evaluation-red-teaming": "AI safety evaluation red teaming benchmark",
        "ai-safety.governance-audit": "AI governance audit safety",
    }
    return hints.get(topic["id"], _clean_text(theme or (directions[0].get("research_direction") if directions else "AI safety")))


async def _refresh_v2_knowledge_base(theme: str, directions: list[dict[str, Any]], limit: int = 10) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Fetch up to 10 topic-scoped OpenAlex works and persist a local, reviewable KB.

    The generation prompt remains bounded by ``_load_v2_knowledge_base`` (top 4
    topic/references matches), so a broader automatic retrieval does not bloat
    the model context.
    """
    topic = canonical_topic(theme or str(directions[0].get("topic") if directions else "人工智能安全（其他）"))
    query = _academic_query(theme, directions)
    kb_dir = _v2_knowledge_base_dir()
    index_path = kb_dir / "index.json"
    try:
        existing = json.loads(index_path.read_text(encoding="utf-8")) if index_path.is_file() else []
        existing = existing if isinstance(existing, list) else []
    except (OSError, json.JSONDecodeError):
        existing = []
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(25.0, connect=8.0)) as client:
            response = await client.get("https://api.openalex.org/works", params={"search": query, "per-page": limit})
            response.raise_for_status()
            works = response.json().get("results", [])
    except Exception as exc:
        return ({"status": "failed", "query": query, "imported": 0, "skipped": 0, "reason": str(exc)}, [])
    try:
        kb_dir.mkdir(parents=True, exist_ok=True)
        by_id = {str(item.get("openalex_id") or item.get("id") or ""): item for item in existing}
        imported = 0
        skipped = 0
        visible: list[dict[str, str]] = []
        for work in works:
            work_id = str(work.get("id") or "").rstrip("/").split("/")[-1]
            title = _clean_text(work.get("display_name"))
            if not work_id or not title:
                continue
            visible.append({"title": title, "year": str(work.get("publication_year") or ""), "url": str(work.get("doi") or work.get("id") or "")})
            filename = f"openalex_{re_slug(work_id)}.md"
            paper_path = kb_dir / filename
            if work_id in by_id and paper_path.is_file():
                skipped += 1
                continue
            abstract = _openalex_abstract(work)
            authors = ", ".join(_clean_text((author.get("author") or {}).get("display_name")) for author in (work.get("authorships") or [])[:8] if _clean_text((author.get("author") or {}).get("display_name")))
            content = "\n".join([f"# {title}", "", "## Metadata", f"- OpenAlex: {work.get('id') or ''}", f"- DOI: {work.get('doi') or ''}", f"- Year: {work.get('publication_year') or ''}", f"- Authors: {authors or 'Unknown'}", f"- Topic ID: {topic['id']}", "", "## Abstract", abstract or "OpenAlex 未提供摘要。", ""])
            paper_path.write_text(content, encoding="utf-8")
            by_id[work_id] = {"openalex_id": work_id, "title": title, "title_lower": title.casefold(), "md_file": filename, "source": "OpenAlex", "url": work.get("doi") or work.get("id") or "", "year": work.get("publication_year") or "", "topic_id": topic["id"], "topic_label": topic["label"], "updated_at": utc_now()}
            imported += 1
        index_path.write_text(json.dumps(list(by_id.values()), ensure_ascii=False, indent=2), encoding="utf-8")
        return ({"status": "completed", "query": query, "imported": imported, "skipped": skipped, "knowledge_base": str(kb_dir)}, visible)
    except OSError as exc:
        return ({"status": "failed", "query": query, "imported": 0, "skipped": 0, "reason": str(exc)}, visible)


async def _openalex_novelty_context(theme: str, directions: list[dict[str, Any]], candidates: list[dict[str, Any]]) -> list[dict[str, str]]:
    query = _clean_text(theme or (directions[0].get("research_direction") if directions else "") or (candidates[0].get("title") if candidates else "") or "AI safety")[:200]
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=8.0)) as client:
            response = await client.get("https://api.openalex.org/works", params={"search": query, "per-page": 5})
            response.raise_for_status()
            return [{"title": item.get("display_name", ""), "year": str(item.get("publication_year", "")), "url": item.get("doi") or item.get("id") or ""} for item in response.json().get("results", [])]
    except Exception:
        return []


def _parse_ideas(text: str | None, count: int) -> list[dict[str, Any]]:
    if not text:
        return []
    try:
        parsed = json.loads(text)
        ideas = parsed.get("ideas") if isinstance(parsed, dict) else parsed
        cleaned: list[dict[str, Any]] = []
        for index, idea in enumerate(ideas[:count] if isinstance(ideas, list) else []):
            if isinstance(idea, dict) and idea.get("Title"):
                idea.setdefault("Name", f"generated_{index:02d}_{re_slug(idea.get('Title', ''))}")
                idea.setdefault("github_repos", [])
                idea.setdefault("_source", "ai")
                idea.setdefault("_score", 0)
                cleaned.append(idea)
        return cleaned
    except json.JSONDecodeError:
        return []


async def _generate_research_ideas(count: int, theme: str = "", strategy: str = "v2_reflection", reflections: int = 2, allow_rule_fallback: bool = False) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    theme = _clean_text(theme)
    trends, records, candidates, media_directions, media_topics = _filter_idea_context(theme)
    trend_context = [
        {"topic_id": trend.get("topic_id"), "label": trend.get("label") or trend.get("topic"), "summary": trend.get("summary") or f"{trend.get('document_count', 0)} 条文档、{trend.get('evidence_count', 0)} 条证据、趋势 {trend.get('trend', 'stable')}", "keywords": trend.get("keywords"), "metrics": trend.get("metrics") or {}, "score": trend.get("score") or trend.get("quality_score")}
        for trend in trends
    ]
    record_context = [
        {"title": record.get("title"), "platform": record.get("platform"), "content": str(record.get("content") or record.get("text") or "")[:500], "source_url": record.get("source_url"), "topic_mappings": record.get("topic_mappings") or []}
        for record in records
    ]
    candidate_context = [
        {"title": candidate.get("title"), "question": candidate.get("question") or "；".join(candidate.get("questions") or []), "tags": candidate.get("tags") or candidate.get("evidence_topics") or []}
        for candidate in candidates
    ]
    direction_context = [
        {"research_direction": item.get("research_direction"), "topic": item.get("topic"), "topic_id": canonical_topic(str(item.get("topic") or ""))["id"], "score": item.get("score"),
         "why_now": item.get("why_now"), "research_questions": item.get("research_questions"),
         "recommended_methods": item.get("recommended_methods"), "evidence_topics": item.get("evidence_topics")}
        for item in media_directions
    ]
    topic_context = [
        {"topic": item.get("topic"), "content_count": item.get("content_count"), "platforms": item.get("platforms"),
         "representative_terms": item.get("representative_terms"), "trend": item.get("trend"), "quality_score": item.get("quality_score")}
        for item in media_topics
    ]
    knowledge_update, discovered_papers = await _refresh_v2_knowledge_base(theme, media_directions)
    workshop_path, workshop_content, references = _build_workshop(theme, records, media_directions, media_topics)
    kb_papers, kb_summaries, kb_note = _load_v2_knowledge_base(references, theme)
    payload = {
        "ai_scientist_v2_workshop": workshop_content,
        "ai_scientist_v2_references": references,
        "ai_scientist_v2_knowledge_base": kb_summaries or "(没有匹配的知识库论文；不可虚构论文基础。)",
        "trends": trend_context,
        "records": record_context,
        "candidates": candidate_context,
        "crawler_research_directions": direction_context,
        "crawler_topics": topic_context,
        "selected_theme": theme or "全部主题",
        "generation_strategy": strategy,
        "instruction": (
            f"基于以上{theme or '人工智能安全全部'}领域的研究方向、爬虫热点趋势、候选方向与知识记录，生成 {count} 个可执行的研究想法。"
            "优先使用 crawler_research_directions 中已经由爬虫分析得到的、可追溯证据的方向。"
            "严格返回 JSON 对象：{\"ideas\": [{\"Name\":\"英文短名\",\"Title\":\"标题\",\"Short Hypothesis\":\"核心假设\","
            "\"Related Work\":\"相关工作\",\"Abstract\":\"摘要\",\"Experiments\":\"实验设计\","
            "\"Risk Factors and Limitations\":\"风险与局限\",\"github_repos\":[]}]}。"
            "每个想法都要能追溯到给定热点或记录，不要编造未出现的事实。"
            "不同想法必须探索不同的研究缺口，Title 不得复用“安全评测与防御”等泛化后缀。"
            "每项必须明确：攻击面/机制、核心自变量与因变量、至少一个可获得基准或数据来源、两个比较基线，以及成功判据。"
        ),
    }
    basis = _basis_preview(theme)
    basis["workshop"] = {"path": str(workshop_path), "reference_count": len(references), "references": references}
    basis["knowledge_base"] = {"path": str(_v2_knowledge_base_dir()), "papers": kb_papers, "note": kb_note, "auto_update": knowledge_update}
    if strategy == "v2_novelty":
        novelty = discovered_papers or await _openalex_novelty_context(theme, media_directions, candidates)
        payload["openalex_novelty_check"] = novelty
        payload["instruction"] += " 以 ai_scientist_v2_workshop 和 ai_scientist_v2_knowledge_base 为主要学术基础，将 MediaCrawler 证据作为发现研究缺口和验证研究价值的辅助依据。使用 AI-Scientist-v2 Codex 路线的约束：以下 OpenAlex 结果只用于排除已发表的相似工作，不可将其虚构为本项目证据；输出必须清楚说明与给定材料的差异。"
        basis["openalex"] = novelty
        basis["strategy_label"] = "AI-Scientist-v2 文献新颖性融合（API）"
    else:
        payload["instruction"] += f" 以 ai_scientist_v2_workshop 和 ai_scientist_v2_knowledge_base 为主要学术基础，将 MediaCrawler 证据作为发现研究缺口和验证研究价值的辅助依据。使用 AI-Scientist-v2 多轮反思路线：先形成草案，再在内部进行 {reflections} 轮质量、新颖性与可行性复核，只返回复核后的 JSON。"
        basis["strategy_label"] = f"AI-Scientist-v2 多轮反思融合（{reflections} 轮，API）"
    ideas = _parse_ideas(await _call_llm(payload), count)
    # The temp-free AI-Scientist-v2 route explicitly iterates over a proposal.
    # Keep those iterations as ordinary API calls, never as Codex CLI invocations.
    if ideas and strategy == "v2_reflection":
        for round_index in range(1, reflections + 1):
            reflection_payload = {
                "selected_theme": theme or "全部主题",
                "evidence_basis": {"trends": trend_context, "candidates": candidate_context, "crawler_research_directions": direction_context},
                "draft_ideas": ideas,
                "instruction": (
                    f"这是 AI-Scientist-v2 风格的第 {round_index}/{reflections} 轮反思。逐项检查草案的可验证性、"
                    "与给定证据的可追溯性、新颖性风险、学术实验室可行性和伦理边界；修订后严格返回同一 ideas JSON。"
                    "不得编造论文、数据集、实验结果或来源；删除与其他草案同质、只有泛化评测措辞的提案。"
                ),
            }
            revised = _parse_ideas(await _call_llm(reflection_payload), count)
            if revised:
                ideas = revised
    if ideas:
        for idea in ideas:
            idea["_basis"] = basis
        return ideas, "ai", basis
    if not allow_rule_fallback:
        raise IdeaGenerationError("AI 未返回可解析的 ideas JSON；本次已停止，未使用规则模板替代。")
    fallback = _fallback_ideas(count, trends, candidates, media_directions)
    basis["fallback_reason"] = "AI 未返回可解析的 ideas JSON，用户已允许使用规则兜底。"
    for idea in fallback:
        idea["_basis"] = basis
    return fallback, "rules", basis


def re_slug(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in value.lower()).strip("_")[:40] or "generated_idea"


def _idea_file_label(ideas: list[dict[str, Any]]) -> str:
    """Create a human-readable filename from the generated topics."""
    labels: list[str] = []
    for idea in ideas[:3]:
        title = str(idea.get("Title") or idea.get("Name") or "").strip()
        if title:
            title = re.sub(r"[\\/:*?\"<>|]+", "-", title)
            title = re.sub(r"\s+", "-", title).strip(".-_ " )
            if title and title not in labels:
                labels.append(title[:28])
    return "+".join(labels)[:100] or "人工智能安全研究方向"


def _load_ideas(idea_file: str) -> tuple[str, list[dict[str, Any]]]:
    root = scientist_root()
    path = Path(idea_file)
    if not path.is_absolute():
        path = (root / path).resolve()
    allowed = False
    for base in (root, MEDIA_ROOT):
        try:
            path.relative_to(base)
            allowed = True
            break
        except ValueError:
            continue
    if not allowed:
        raise HTTPException(status_code=400, detail="idea 文件必须位于 AI Scientist 或 MediaCrawler 项目目录内")
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"找不到 idea 文件：{path}")
    try:
        ideas = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"idea JSON 解析失败：{exc}") from exc
    if not isinstance(ideas, list) or not ideas:
        raise HTTPException(status_code=400, detail="idea JSON 必须是非空列表")
    return str(path), ideas


def _build_command(idea_path: str, mode: str, idea_idx: int, attempt_id: int) -> list[str]:
    command = [scientist_python(), str(scientist_root() / "launch_scientist_bfts.py"), "--load_ideas", idea_path, "--idea_idx", str(idea_idx), "--attempt_id", str(attempt_id)]
    if mode == "experiment":
        command.extend(["--skip_writeup", "--skip_review"])
    else:
        command.extend(["--writeup-type", "normal", "--writeup_codex", "--review_codex"])
    return command


def _runtime_idea_file(
    source_path: str, ideas: list[dict[str, Any]], idea_idx: int, base_project: str, task_id: str
) -> tuple[str, int, list[str]]:
    """Create an immutable, single-Idea input for one production run.

    The upstream launcher only enters Framework Mode when ``github_repos`` is
    present in the selected Idea.  Keeping this change in a per-run file means
    choosing a baseline never mutates the user's original Idea JSON.
    """
    selected = dict(ideas[idea_idx])
    repositories = selected.get("github_repos") or []
    if not isinstance(repositories, list):
        repositories = [repositories]
    normalized: list[str] = []
    for repository in repositories:
        url = repository.get("url") if isinstance(repository, dict) else repository
        if isinstance(url, str) and url.startswith(("https://github.com/", "git@github.com:")):
            normalized.append(url)
    if base_project:
        if not base_project.startswith("https://github.com/"):
            raise HTTPException(status_code=400, detail="基础项目必须是公开 GitHub 仓库地址")
        normalized.append(base_project)
    normalized = list(dict.fromkeys(normalized))
    if normalized:
        selected["github_repos"] = normalized

    RUNTIME_IDEAS_DIR.mkdir(parents=True, exist_ok=True)
    runtime_path = RUNTIME_IDEAS_DIR / f"{Path(source_path).stem}_{task_id}.json"
    runtime_path.write_text(json.dumps([selected], ensure_ascii=False, indent=2), encoding="utf-8")
    return str(runtime_path), 0, normalized


def _idea_repository_query(idea: dict[str, Any]) -> tuple[str, list[str]]:
    """Build a conservative GitHub query from the technical terms in an Idea."""
    source = " ".join(str(idea.get(key) or "") for key in ("Title", "Short Hypothesis", "Experiments", "Related Work"))
    ignored = {"with", "from", "that", "this", "using", "based", "security", "research", "study", "evaluation", "defense", "model", "models", "safety", "attack", "attacks"}
    terms: list[str] = []
    for term in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", source):
        normalized = term.lower()
        if normalized not in ignored and normalized not in terms:
            terms.append(normalized)
    selected = terms[:5] or ["llm", "security"]
    return " ".join(selected[:2]) + " language:Python", selected


def _local_repository_recommendations(terms: list[str]) -> list[dict[str, Any]]:
    """Use already-crawled GitHub records when the public API is unavailable."""
    github_dir = MEDIA_ROOT / "data" / "github"
    candidates: dict[str, dict[str, Any]] = {}
    if not github_dir.is_dir():
        return []
    for path in github_dir.rglob("*.jsonl"):
        try:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[:500]:
                item = json.loads(line)
                if not isinstance(item, dict) or not item.get("url"):
                    continue
                searchable = " ".join([str(item.get("title") or ""), str(item.get("description") or ""), *(str(topic) for topic in item.get("topics") or [])]).lower()
                matched = [term for term in terms if term in searchable]
                if not matched:
                    continue
                url = str(item["url"])
                result = {
                    "full_name": str(item.get("title") or url.removeprefix("https://github.com/")),
                    "url": url,
                    "description": str(item.get("description") or item.get("content") or ""),
                    "stars": int(item.get("stars") or 0),
                    "language": str(item.get("language") or ""),
                    "updated_at": str(item.get("updated_at") or ""),
                    "matched_terms": matched,
                    "score": min(100, 40 + len(matched) * 15 + min(20, int(item.get("stars") or 0) // 1000)),
                }
                if url not in candidates or result["score"] > candidates[url]["score"]:
                    candidates[url] = result
        except (OSError, json.JSONDecodeError):
            continue
    return sorted(candidates.values(), key=lambda item: (item["score"], item["stars"]), reverse=True)[:8]


@router.get("/idea-recommendations")
def idea_recommendations(idea_file: str, idea_idx: int = 0) -> dict[str, Any]:
    """Find public GitHub repositories that can serve as an experimental baseline."""
    _, ideas = _load_ideas(idea_file)
    if idea_idx >= len(ideas):
        raise HTTPException(status_code=400, detail=f"idea_idx 超出范围：共 {len(ideas)} 个想法")
    idea = ideas[idea_idx]
    if not isinstance(idea, dict):
        raise HTTPException(status_code=400, detail="所选 Idea 格式无效")
    query, terms = _idea_repository_query(idea)
    headers = {"Accept": "application/vnd.github+json"}
    if token := os.getenv("GITHUB_TOKEN", "").strip():
        headers["Authorization"] = f"Bearer {token}"
    try:
        response = httpx.get("https://api.github.com/search/repositories", params={"q": query, "sort": "stars", "order": "desc", "per_page": 8}, headers=headers, timeout=12)
        response.raise_for_status()
        items = response.json().get("items", [])
        if not items and terms:
            response = httpx.get("https://api.github.com/search/repositories", params={"q": f"{terms[0]} language:Python", "sort": "stars", "order": "desc", "per_page": 8}, headers=headers, timeout=12)
            response.raise_for_status()
            items = response.json().get("items", [])
    except httpx.HTTPStatusError as exc:
        detail = "GitHub 搜索额度已用完；配置 GITHUB_TOKEN 后可提高额度。" if exc.response.status_code == 403 else f"GitHub 搜索失败（HTTP {exc.response.status_code}）。"
        local = _local_repository_recommendations(terms)
        return {"query": query, "terms": terms, "repositories": local, "warning": f"{detail}{' 已改用本地已采集的 GitHub 仓库。' if local else ''}"}
    except httpx.HTTPError:
        local = _local_repository_recommendations(terms)
        return {"query": query, "terms": terms, "repositories": local, "warning": f"无法连接 GitHub；{'已改用本地已采集的 GitHub 仓库。' if local else '请检查网络后重试。'}"}

    if not items:
        local = _local_repository_recommendations(terms)
        return {"query": query, "terms": terms, "repositories": local, "warning": f"GitHub 未找到与当前 Idea 匹配的公开 Python 仓库。{' 已改用本地已采集的 GitHub 仓库。' if local else ''}"}

    repositories: list[dict[str, Any]] = []
    for item in items:
        name = str(item.get("full_name") or "")
        description = str(item.get("description") or "")
        searchable = f"{name} {description}".lower()
        matched = [term for term in terms if term in searchable]
        repositories.append({
            "full_name": name,
            "url": str(item.get("html_url") or ""),
            "description": description,
            "stars": int(item.get("stargazers_count") or 0),
            "language": str(item.get("language") or ""),
            "updated_at": str(item.get("updated_at") or ""),
            "matched_terms": matched,
            "score": min(100, 45 + len(matched) * 12 + min(20, int(item.get("stargazers_count") or 0) // 1000)),
        })
    repositories.sort(key=lambda item: (item["score"], item["stars"]), reverse=True)
    return {"query": query, "terms": terms, "repositories": repositories, "warning": ""}


@router.get("/overview")
def overview() -> dict[str, Any]:
    preflight = _preflight()
    _reconcile_production_tasks()
    counts = intelligence_overview_counts()
    return {
        "root": str(scientist_root()),
        "python": scientist_python(),
        "ready": preflight["ready"],
        "counts": {
            "records": counts["records"],
            "trends": counts["trends"],
            "candidates": counts["candidates"],
            "production_tasks": len(_list_table("production_tasks", 500)),
        },
        "checks": preflight["checks"],
    }


@router.get("/preflight")
def preflight() -> dict[str, Any]:
    return _preflight()


@router.get("/ideas")
def list_ideas() -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    candidates = [scientist_root() / "ai_scientist" / "ideas", GENERATED_IDEAS_DIR]
    seen: set[str] = set()
    for ideas_dir in candidates:
        if not ideas_dir.is_dir():
            continue
        for path in sorted(ideas_dir.glob("*.json")):
            if str(path) in seen:
                continue
            seen.add(str(path))
            try:
                ideas = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(ideas, list):
                    continue
                preview = [
                    {"index": index, "name": idea.get("Name", ""), "title": idea.get("Title", "")}
                    for index, idea in enumerate(ideas)
                    if isinstance(idea, dict)
                ]
                files.append({"file": str(path), "name": path.name, "count": len(preview), "ideas": preview})
            except (OSError, json.JSONDecodeError):
                continue
    return {"files": files}


@router.delete("/ideas")
def delete_idea(idea_file: str) -> dict[str, Any]:
    """Delete an Idea JSON file listed by this router and its generated metadata."""
    path = Path(idea_file).expanduser().resolve()
    allowed_dirs = [scientist_root() / "ai_scientist" / "ideas", GENERATED_IDEAS_DIR]
    if not any(_is_within(path, directory.resolve()) for directory in allowed_dirs):
        raise HTTPException(status_code=400, detail="只能删除 Idea 文件列表中的文件")
    if path.suffix.lower() != ".json" or not path.is_file():
        raise HTTPException(status_code=404, detail="找不到 Idea JSON 文件")

    path.unlink()
    metadata_path = path.with_suffix(".meta.json")
    if metadata_path.is_file():
        metadata_path.unlink()
    return {"deleted": True, "name": path.name}


@router.post("/ideas/generate")
async def generate_ideas(request: GenerateIdeasRequest) -> dict[str, Any]:
    try:
        ideas, method, basis = await _generate_research_ideas(request.count, request.theme, request.strategy, request.reflections, request.allow_rule_fallback)
    except IdeaGenerationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    record_generated_ideas(ideas, method, intelligence_context_snapshot(20))
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    GENERATED_IDEAS_DIR.mkdir(parents=True, exist_ok=True)
    label = _idea_file_label(ideas)
    output = GENERATED_IDEAS_DIR / f"research_ideas_{stamp}_{label}.json"
    output.write_text(json.dumps(ideas, ensure_ascii=False, indent=2), encoding="utf-8")
    output.with_suffix(".meta.json").write_text(json.dumps({"theme": request.theme or "全部主题", "strategy": request.strategy, "reflections": request.reflections, "method": method, "basis": basis, "created_at": utc_now()}, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "file": str(output),
        "name": output.name,
        "count": len(ideas),
        "method": method,
        "strategy": request.strategy,
        "basis": basis,
        "ideas": [
            {
                "index": index,
                "name": idea.get("Name", ""),
                "title": idea.get("Title", ""),
                "hypothesis": idea.get("Short Hypothesis", ""),
                "abstract": idea.get("Abstract", ""),
                "related_work": idea.get("Related Work", ""),
                "experiments": idea.get("Experiments", ""),
                "risks": idea.get("Risk Factors and Limitations", ""),
                "source": idea.get("_source", ""),
                "score": idea.get("_score", 0),
                "evidence": idea.get("_evidence", []),
            }
            for index, idea in enumerate(ideas)
        ],
    }


@router.get("/idea-themes")
def idea_themes() -> dict[str, Any]:
    return {"themes": available_themes()}


@router.get("/idea-strategies")
def idea_strategies() -> dict[str, Any]:
    return {"strategies": [
        {"value": "v2_reflection", "label": "多轮反思融合", "description": "复刻 AI-Scientist-v2 的生成、反思和修订方法；通过配置的 API 调用模型。"},
        {"value": "v2_novelty", "label": "文献新颖性融合", "description": "复刻 AI-Scientist-v2 Codex 路线的知识库/新颖性约束；通过配置的 API 调用模型，并用 OpenAlex 校验。"},
    ]}


@router.get("/idea-basis-preview")
def idea_basis_preview(theme: str = "") -> dict[str, Any]:
    return _basis_preview(theme)


@router.get("/records")
def records(limit: int = 100) -> dict[str, Any]:
    items = intelligence_context_snapshot(min(limit, 500))["documents"]
    return {"records": items or _list_table("records", min(limit, 500))}


@router.get("/trends")
def trends(limit: int = 100) -> dict[str, Any]:
    items = intelligence_context_snapshot(min(limit, 500))["trends"]
    return {"trends": items or _list_table("trends", min(limit, 500))}


@router.get("/candidates")
def candidates(limit: int = 100) -> dict[str, Any]:
    items = intelligence_context_snapshot(min(limit, 500))["candidates"]
    return {"candidates": items or _list_table("candidates", min(limit, 500))}


@router.post("/sync-obsidian")
def sync_obsidian_notes() -> dict[str, Any]:
    """Export research records into the configured academic and social vaults."""
    return sync_obsidian()


@router.get("/vaults/status")
def vaults_status() -> dict[str, Any]:
    return {"vaults": [_vault_info("academic"), _vault_info("social")]}


@router.put("/vaults/config")
def update_vaults_config(request: VaultsConfigRequest) -> dict[str, Any]:
    academic = Path(request.academic_path).expanduser().resolve()
    if not academic.is_dir():
        raise HTTPException(status_code=400, detail=f"学术知识库路径不存在或不可访问：{academic}")
    social = Path(request.social_path).expanduser().resolve()
    social.mkdir(parents=True, exist_ok=True)
    env_path = MEDIA_ROOT / ".env"
    text = env_path.read_text(encoding="utf-8") if env_path.is_file() else ""
    values = {
        ACADEMIC_VAULT_ENV: str(academic),
        SOCIAL_VAULT_ENV: str(social),
        # Preserve the legacy setting so old integrations continue to regard
        # the existing academic vault as their default read-only target.
        "OBSIDIAN_VAULT_PATH": str(academic),
    }
    for key, value in values.items():
        line = f"{key}={value}"
        text = re.sub(rf"(?m)^{re.escape(key)}=.*$", lambda _: line, text) if re.search(rf"(?m)^{re.escape(key)}=.*$", text) else text.rstrip() + "\n" + line + "\n"
        os.environ[key] = value
    env_path.write_text(text, encoding="utf-8")
    return {"vaults": [_vault_info("academic"), _vault_info("social")]}


@router.post("/vaults/sync")
def sync_vaults() -> dict[str, Any]:
    return sync_obsidian()


@router.get("/vaults/{kind}/notes")
def vault_notes_for_kind(kind: str, limit: int = 5000) -> dict[str, Any]:
    vault = _vault_path_for_kind(kind)
    rows: list[dict[str, Any]] = []
    # A topic tree is only useful when every generated item is returned.  The
    # social vault can hold several thousand comments, so retain a bounded but
    # sufficiently high ceiling rather than silently truncating a category.
    for path in sorted(vault.rglob("*.md"), key=lambda item: item.stat().st_mtime, reverse=True)[:min(limit, 10000)]:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            rows.append({"path": path.relative_to(vault).as_posix(), "title": next((line[2:].strip() for line in text.splitlines() if line.startswith("# ")), path.stem), "meta": _frontmatter(text), "modified_at": path.stat().st_mtime})
        except OSError:
            continue
    return {"notes": rows}


@router.get("/vaults/{kind}/note")
def vault_note_for_kind(kind: str, path: str) -> dict[str, Any]:
    note = _safe_vault_note_for_kind(kind, path)
    text = note.read_text(encoding="utf-8", errors="replace")
    return {"path": note.relative_to(_vault_path_for_kind(kind)).as_posix(), "content": text, "meta": _frontmatter(text)}


@router.post("/import-analysis")
def import_analysis_history() -> dict[str, Any]:
    """Backfill the unified store from existing local analysis files, then export notes."""
    result = import_existing_analyses()
    result["obsidian"] = sync_obsidian()
    return result


def _vault_path() -> Path:
    value = os.getenv("OBSIDIAN_VAULT_PATH", "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="尚未配置 Obsidian 知识库路径")
    path = Path(value).expanduser().resolve()
    if not path.is_dir():
        raise HTTPException(status_code=400, detail=f"知识库路径不存在或不可访问：{path}")
    return path


def _vault_env(kind: str) -> str:
    if kind == "academic":
        return ACADEMIC_VAULT_ENV
    if kind == "social":
        return SOCIAL_VAULT_ENV
    raise HTTPException(status_code=404, detail="未知知识库类型")


def _vault_path_for_kind(kind: str) -> Path:
    value = os.getenv(_vault_env(kind), "").strip()
    if not value and kind == "academic":
        value = os.getenv("OBSIDIAN_VAULT_PATH", "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="尚未配置知识库路径")
    path = Path(value).expanduser().resolve()
    if not path.is_dir():
        raise HTTPException(status_code=400, detail=f"知识库路径不存在或不可访问：{path}")
    return path


def _safe_vault_note_for_kind(kind: str, relative_path: str) -> Path:
    vault = _vault_path_for_kind(kind)
    path = (vault / relative_path).resolve()
    try:
        path.relative_to(vault)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="不允许访问知识库外的文件") from exc
    if path.suffix.lower() != ".md" or not path.is_file():
        raise HTTPException(status_code=404, detail="找不到 Markdown 笔记")
    return path


def _vault_info(kind: str) -> dict[str, Any]:
    value = os.getenv(_vault_env(kind), "").strip()
    if not value and kind == "academic":
        value = os.getenv("OBSIDIAN_VAULT_PATH", "").strip()
    path = Path(value).expanduser() if value else None
    available = bool(path and path.is_dir())
    return {"kind": kind, "path": value, "available": available, "note_count": len(list(path.rglob("*.md"))) if available and path else 0}


def _safe_vault_note(relative_path: str) -> Path:
    vault = _vault_path()
    path = (vault / relative_path).resolve()
    try:
        path.relative_to(vault)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="不允许访问知识库外的文件") from exc
    if path.suffix.lower() != ".md" or not path.is_file():
        raise HTTPException(status_code=404, detail="找不到 Markdown 笔记")
    return path


def _frontmatter(text: str) -> dict[str, Any]:
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end < 0:
        return {}
    result: dict[str, Any] = {}
    for line in text[4:end].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        result[key.strip()] = value.strip().strip('\"')
    return result


def _analysis_report_note(relative_path: str) -> Path:
    note = _safe_vault_note(relative_path)
    if _frontmatter(note.read_text(encoding="utf-8", errors="replace")).get("type") != "analysis-report":
        raise HTTPException(status_code=403, detail="仅允许管理分析结果报告")
    return note


@router.get("/vault/status")
def vault_status() -> dict[str, Any]:
    value = os.getenv("OBSIDIAN_VAULT_PATH", "").strip()
    path = Path(value).expanduser() if value else None
    available = bool(path and path.is_dir())
    notes = len(list(path.rglob("*.md"))) if available and path else 0
    return {"path": value, "available": available, "note_count": notes}


@router.put("/vault/config")
def update_vault_config(request: VaultConfigRequest) -> dict[str, Any]:
    vault = Path(request.path).expanduser().resolve()
    if not vault.is_dir():
        raise HTTPException(status_code=400, detail=f"知识库路径不存在或不可访问：{vault}")
    env_path = MEDIA_ROOT / ".env"
    text = env_path.read_text(encoding="utf-8") if env_path.is_file() else ""
    line = f"OBSIDIAN_VAULT_PATH={vault}"
    text = re.sub(r"(?m)^OBSIDIAN_VAULT_PATH=.*$", line, text) if re.search(r"(?m)^OBSIDIAN_VAULT_PATH=.*$", text) else text.rstrip() + "\n" + line + "\n"
    env_path.write_text(text, encoding="utf-8")
    os.environ["OBSIDIAN_VAULT_PATH"] = str(vault)
    return {"path": str(vault), "available": True, "note_count": len(list(vault.rglob("*.md")))}


@router.get("/vault/notes")
def vault_notes(limit: int = 500) -> dict[str, Any]:
    vault = _vault_path()
    rows: list[dict[str, Any]] = []
    for path in sorted(vault.rglob("*.md"), key=lambda item: item.stat().st_mtime, reverse=True)[:min(limit, 2000)]:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            rows.append({"path": path.relative_to(vault).as_posix(), "title": next((line[2:].strip() for line in text.splitlines() if line.startswith("# ")), path.stem), "meta": _frontmatter(text), "modified_at": path.stat().st_mtime})
        except OSError:
            continue
    return {"notes": rows}


@router.get("/vault/note")
def vault_note(path: str) -> dict[str, Any]:
    note = _safe_vault_note(path)
    text = note.read_text(encoding="utf-8", errors="replace")
    return {"path": note.relative_to(_vault_path()).as_posix(), "content": text, "meta": _frontmatter(text)}


@router.put("/vault/report")
def rename_vault_report(request: VaultReportRenameRequest) -> dict[str, Any]:
    note = _analysis_report_note(request.path)
    title = re.sub(r"\s+", " ", request.title).strip()
    if not title or re.search(r"[\\/:*?\"<>|]", title):
        raise HTTPException(status_code=400, detail="报告名称不能为空且不能包含文件名保留字符")
    text = note.read_text(encoding="utf-8", errors="replace")
    updated = re.sub(r"(?m)^# .+$", f"# {title}", text, count=1)
    override = f"title_override: {json.dumps(title, ensure_ascii=False)}\n"
    updated = re.sub(r"(?m)^title_override:.*\n", override, updated) if re.search(r"(?m)^title_override:.*\n", updated) else updated.replace("---\n", "---\n" + override, 1)
    slug = re.sub(r"\s+", "-", title).strip(".- ")
    target = note.with_name(f"分析-{slug}.md")
    number = 2
    while target.exists() and target != note:
        target = note.with_name(f"分析-{slug}-{number}.md")
        number += 1
    note.write_text(updated, encoding="utf-8")
    if target != note:
        note.replace(target)
    return {"path": target.relative_to(_vault_path()).as_posix(), "title": title}


@router.delete("/vault/report")
def delete_vault_report(path: str) -> dict[str, Any]:
    note = _analysis_report_note(path)
    name = note.name
    try:
        note.unlink()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"删除报告失败：{exc}") from exc
    return {"deleted": True, "name": name}


@router.post("/production")
def start_production(request: ProductionRequest) -> dict[str, Any]:
    preflight = _preflight()
    if not preflight["ready"]:
        messages = "；".join(check["message"] for check in preflight["errors"])
        raise HTTPException(status_code=409, detail=f"环境预检未通过：{messages}")
    idea_path, ideas = _load_ideas(request.idea_file)
    if request.idea_idx >= len(ideas):
        raise HTTPException(status_code=400, detail=f"idea_idx 超出范围：共 {len(ideas)} 个想法")
    if not isinstance(ideas[request.idea_idx], dict):
        raise HTTPException(status_code=400, detail="所选 Idea 格式无效")
    title = str(ideas[request.idea_idx].get("Title") or ideas[request.idea_idx].get("Name") or "Untitled idea")
    task_id = str(uuid.uuid4())
    runtime_idea_path, runtime_idea_idx, framework_repos = _runtime_idea_file(
        idea_path, ideas, request.idea_idx, request.base_project.strip(), task_id
    )
    command = _build_command(runtime_idea_path, request.mode, runtime_idea_idx, request.attempt_id)
    run_dir = scientist_root() / "research_platform" / "runs" / task_id
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "production.log"
    task = {
        "id": task_id,
        "candidate_id": "",
        "candidate_title": title,
        "mode": request.mode,
        "status": "queued",
        "created_at": utc_now(),
        "idea_file": runtime_idea_path,
        "source_idea_file": idea_path,
        "source_idea_idx": request.idea_idx,
        "base_project": request.base_project,
        "framework_repos": framework_repos,
        "attempt_id": request.attempt_id,
        "workshop_file": "",
        "log_path": str(log_path),
        "run_dir": str(run_dir),
        "command": command,
        "pid": None,
        "exit_code": None,
        "error": None,
        "cancel_requested": False,
        "started_at": None,
        "finished_at": None,
    }
    _save_task(task)
    _spawn_task(task)
    return task


def _spawn_task(task: dict[str, Any]) -> None:
    def worker() -> None:
        if task.get("cancel_requested"):
            task["status"] = "cancelled"
            task["finished_at"] = utc_now()
            _save_task(task)
            return
        task["status"] = "running"
        task["started_at"] = utc_now()
        _save_task(task)
        log_path = Path(task["log_path"])
        try:
            with log_path.open("w", encoding="utf-8") as log:
                process = subprocess.Popen(task["command"], cwd=scientist_root(), stdout=log, stderr=subprocess.STDOUT, text=True)
                task["pid"] = process.pid
                _save_task(task)
                with RUNNING_PROCESSES_LOCK:
                    RUNNING_PROCESSES[task["id"]] = process
                exit_code = process.wait()
            task["exit_code"] = exit_code
            task["status"] = "cancelled" if task.get("cancel_requested") else ("completed" if exit_code == 0 else "failed")
        except Exception as exc:
            task["status"] = "cancelled" if task.get("cancel_requested") else "failed"
            task["error"] = "任务已取消" if task.get("cancel_requested") else str(exc)
        finally:
            with RUNNING_PROCESSES_LOCK:
                RUNNING_PROCESSES.pop(task["id"], None)
            task["finished_at"] = utc_now()
            _save_task(task)

    threading.Thread(target=worker, name=f"scientist-production-{task['id']}", daemon=True).start()


@router.delete("/production/{task_id}")
def delete_production_task(task_id: str) -> dict[str, Any]:
    task = _get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="找不到生产任务")
    _reconcile_task(task)
    if task.get("status") in {"queued", "running"}:
        raise HTTPException(status_code=409, detail="任务正在执行，不能删除")

    run_dir = Path(str(task.get("run_dir") or "")).resolve()
    allowed_runs = (scientist_root() / "research_platform" / "runs").resolve()
    if run_dir.is_dir() and _is_within(run_dir, allowed_runs):
        shutil.rmtree(run_dir)
    conn = _connect()
    try:
        conn.execute("DELETE FROM production_tasks WHERE id = ?", (task_id,))
        conn.commit()
    finally:
        conn.close()
    return {"deleted": True, "id": task_id}


@router.post("/production/{task_id}/cancel")
def cancel_production_task(task_id: str) -> dict[str, Any]:
    task = _get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="找不到生产任务")
    _reconcile_task(task)
    if task.get("status") not in {"queued", "running"}:
        raise HTTPException(status_code=409, detail="只有排队中或运行中的任务可以取消")
    task["cancel_requested"] = True
    _save_task(task)
    with RUNNING_PROCESSES_LOCK:
        process = RUNNING_PROCESSES.get(task_id)
    if process and process.poll() is None:
        process.terminate()
    elif _pid_is_running(task.get("pid")):
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(task["pid"]), "/T", "/F"], check=False, capture_output=True, timeout=15)
            else:
                os.kill(int(task["pid"]), signal.SIGTERM)
        except OSError as exc:
            task["error"] = f"取消请求已记录，但终止进程失败：{exc}"
            _save_task(task)
    return task


@router.post("/production/{task_id}/retry")
def retry_production_task(task_id: str) -> dict[str, Any]:
    task = _get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="找不到生产任务")
    _reconcile_task(task)
    if task.get("status") not in {"failed", "cancelled"}:
        raise HTTPException(status_code=409, detail="只有失败或已取消的任务可以重试")
    source_file = str(task.get("source_idea_file") or task.get("idea_file") or "")
    return start_production(ProductionRequest(
        idea_file=source_file,
        idea_idx=int(task.get("source_idea_idx") or 0),
        mode=str(task.get("mode") or "experiment"),
        attempt_id=int(task.get("attempt_id") or 1) + 1,
        base_project=str(task.get("base_project") or ""),
    ))


@router.get("/production")
def production_tasks() -> dict[str, Any]:
    _reconcile_production_tasks()
    return {"tasks": _list_table("production_tasks", 50)}


@router.get("/production/{task_id}")
def production_task(task_id: str) -> dict[str, Any]:
    task = _get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="生产任务不存在")
    _reconcile_task(task)
    return task


@router.get("/production/{task_id}/log")
def production_log(task_id: str) -> dict[str, Any]:
    task = _get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="生产任务不存在")
    _reconcile_task(task)
    return {"log": _tail_log(task.get("log_path", "")), "task": task}
