# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/api/routers/data.py
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#
# 声明：本代码仅供学习和研究目的使用。使用者应遵守以下原则：
# 1. 不得用于任何商业用途。
# 2. 使用时应遵守目标平台的使用条款和robots.txt规则。
# 3. 不得进行大规模爬取或对平台造成运营干扰。
# 4. 应合理控制请求频率，避免给目标平台带来不必要的负担。
# 5. 不得用于任何非法或不当的用途。
#
# 详细许可条款请参阅项目根目录下的LICENSE文件。
# 使用本代码即表示您同意遵守上述原则和LICENSE中的所有条款。

import os
import json
import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from pipeline.analysis_service import analyze_file, available_themes, prepare_merged_input
from pipeline.ai_response import AIResponseError

router = APIRouter(prefix="/data", tags=["data"])


class AnalysisRequest(BaseModel):
    path: str
    limit: int = Field(default=500, ge=1, le=5000)
    selected_themes: list[str] | None = Field(default=None, max_length=20)
    custom_themes: list[str] = Field(default_factory=list, max_length=10)


class BatchAnalysisRequest(BaseModel):
    paths: list[str] = Field(min_length=1, max_length=20)
    limit: int = Field(default=500, ge=1, le=5000)
    selected_themes: list[str] | None = Field(default=None, max_length=20)
    custom_themes: list[str] = Field(default_factory=list, max_length=10)
    merge: bool = False

class AnalysisConfigRequest(BaseModel):
    model: str = Field(min_length=1, max_length=100)

AI_MODELS = [
    {"value": "deepseek-chat", "label": "DeepSeek Chat"},
    {"value": "deepseek-reasoner", "label": "DeepSeek Reasoner"},
]

DEFAULT_MODEL = "deepseek-chat"

def _env_path() -> Path:
    return DATA_DIR.parent / ".env"

@router.get("/analysis-config")
async def get_analysis_config():
    configured = os.getenv("AI_MODEL", DEFAULT_MODEL)
    if configured not in {item["value"] for item in AI_MODELS}:
        configured = DEFAULT_MODEL
    return {"models": AI_MODELS, "model": configured, "ai_enabled": bool(os.getenv("AI_API_KEY", "").strip()), "themes": available_themes()}

@router.put("/analysis-config")
async def update_analysis_config(request: AnalysisConfigRequest):
    allowed = {item["value"] for item in AI_MODELS}
    if request.model not in allowed:
        raise HTTPException(status_code=400, detail="不支持的模型")
    path = _env_path()
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    line = f"AI_MODEL={request.model}"
    if re.search(r"(?m)^AI_MODEL=.*$", text):
        text = re.sub(r"(?m)^AI_MODEL=.*$", line, text)
    else:
        text = text.rstrip() + "\n" + line + "\n"
    path.write_text(text, encoding="utf-8")
    os.environ["AI_MODEL"] = request.model
    return {"model": request.model}

# Data directory
DATA_DIR = Path(__file__).parent.parent.parent / "data"


@router.post("/analyze")
async def analyze_data_file(request: AnalysisRequest):
    """Clean, deduplicate and enrich one crawler output file."""
    try:
        return await analyze_file(request.path, request.limit, request.selected_themes, request.custom_themes)
    except AIResponseError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Data file not found") from exc


@router.post("/analyze/batch")
async def analyze_data_files(request: BatchAnalysisRequest):
    """Analyze multiple crawler output files sequentially and keep partial results."""
    paths = list(dict.fromkeys(request.paths))
    if request.merge:
        try:
            merged_path = prepare_merged_input(paths, request.limit)
            result = await analyze_file(merged_path, request.limit * len(paths), request.selected_themes, request.custom_themes)
            return {"results": [{"path": "已合并 " + str(len(paths)) + " 个文件", "status": "completed", "result": result}], "completed": 1, "failed": 0, "merged": True}
        except (ValueError, FileNotFoundError) as exc:
            return {"results": [{"path": "合并文件", "status": "failed", "error": str(exc)}], "completed": 0, "failed": 1, "merged": True}
    results = []
    for path in paths:
        try:
            result = await analyze_file(path, request.limit, request.selected_themes, request.custom_themes)
            results.append({"path": path, "status": "completed", "result": result})
        except (ValueError, FileNotFoundError) as exc:
            results.append({"path": path, "status": "failed", "error": str(exc)})
        except Exception as exc:
            results.append({"path": path, "status": "failed", "error": f"分析异常：{exc}"})
    completed = sum(item["status"] == "completed" for item in results)
    return {"results": results, "completed": completed, "failed": len(results) - completed}


def get_file_info(file_path: Path) -> dict:
    """Get file information"""
    stat = file_path.stat()
    record_count = None

    # Try to get record count
    try:
        if file_path.suffix == ".json":
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    record_count = len(data)
        elif file_path.suffix == ".csv":
            with open(file_path, "r", encoding="utf-8") as f:
                record_count = sum(1 for _ in f) - 1  # Subtract header row
        elif file_path.suffix.lower() == ".jsonl":
            with open(file_path, "r", encoding="utf-8") as f:
                record_count = sum(1 for line in f if line.strip())
    except Exception:
        pass

    return {
        "name": file_path.name,
        "path": str(file_path.relative_to(DATA_DIR)),
        "size": stat.st_size,
        "modified_at": stat.st_mtime,
        "record_count": record_count,
        "type": file_path.suffix[1:] if file_path.suffix else "unknown"
    }


@router.get("/files")
async def list_data_files(platform: Optional[str] = None, file_type: Optional[str] = None, include_analysis: bool = True):
    """Get data file list"""
    if not DATA_DIR.exists():
        return {"files": []}

    files = []
    supported_extensions = {".json", ".jsonl", ".csv", ".xlsx", ".xls"}

    for root, dirs, filenames in os.walk(DATA_DIR):
        root_path = Path(root)
        for filename in filenames:
            file_path = root_path / filename
            if file_path.suffix.lower() not in supported_extensions:
                continue

            if not include_analysis and {"analysis", "scientist"}.intersection(file_path.relative_to(DATA_DIR).parts):
                continue

            # Platform filter
            if platform:
                rel_path = str(file_path.relative_to(DATA_DIR))
                if platform.lower() not in rel_path.lower():
                    continue

            # Type filter
            if file_type and file_path.suffix[1:].lower() != file_type.lower():
                continue

            try:
                files.append(get_file_info(file_path))
            except Exception:
                continue

    # Sort by modification time (newest first)
    files.sort(key=lambda x: x["modified_at"], reverse=True)

    return {"files": files}


@router.get("/files/{file_path:path}")
async def get_file_content(file_path: str, preview: bool = True, limit: int = 100):
    """Get file content or preview"""
    full_path = DATA_DIR / file_path

    if not full_path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    if not full_path.is_file():
        raise HTTPException(status_code=400, detail="Not a file")

    # Security check: ensure within DATA_DIR
    try:
        full_path.resolve().relative_to(DATA_DIR.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied")

    if preview:
        # Return preview data
        try:
            if full_path.suffix == ".json":
                with open(full_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        return {"data": data[:limit], "total": len(data)}
                    return {"data": data, "total": 1}
            elif full_path.suffix.lower() == ".jsonl":
                rows = []
                total = 0
                with open(full_path, "r", encoding="utf-8") as f:
                    for line in f:
                        if not line.strip():
                            continue
                        total += 1
                        if len(rows) < limit:
                            try:
                                value = json.loads(line)
                                rows.append(value if isinstance(value, dict) else {"value": value})
                            except json.JSONDecodeError:
                                rows.append({"value": line.strip()})
                return {"data": rows, "total": total}
            elif full_path.suffix == ".csv":
                import csv
                with open(full_path, "r", encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    rows = []
                    for i, row in enumerate(reader):
                        if i >= limit:
                            break
                        rows.append(row)
                    # Re-read to get total count
                    f.seek(0)
                    total = sum(1 for _ in f) - 1
                    return {"data": rows, "total": total}
            elif full_path.suffix.lower() in (".xlsx", ".xls"):
                import pandas as pd
                # Read first limit rows
                df = pd.read_excel(full_path, nrows=limit)
                # Get total row count (only read first column to save memory)
                df_count = pd.read_excel(full_path, usecols=[0])
                total = len(df_count)
                # Convert to list of dictionaries, handle NaN values
                rows = df.where(pd.notnull(df), None).to_dict(orient='records')
                return {
                    "data": rows,
                    "total": total,
                    "columns": list(df.columns)
                }
            else:
                raise HTTPException(status_code=400, detail="Unsupported file type for preview")
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid JSON file")
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    else:
        # Return file download
        return FileResponse(
            path=full_path,
            filename=full_path.name,
            media_type="application/octet-stream"
        )


@router.delete("/files/{file_path:path}")
async def delete_data_file(file_path: str):
    """Delete one user-selected crawler output file inside the data directory."""
    full_path = (DATA_DIR / file_path).resolve()
    try:
        full_path.relative_to(DATA_DIR.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Access denied") from exc
    if not full_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    if not full_path.is_file():
        raise HTTPException(status_code=400, detail="Not a file")
    if full_path.suffix.lower() not in {".json", ".jsonl", ".csv", ".xlsx", ".xls"}:
        raise HTTPException(status_code=400, detail="Unsupported file type")
    name = full_path.name
    try:
        full_path.unlink()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Unable to delete file: {exc}") from exc
    return {"deleted": True, "name": name}


@router.get("/download/{file_path:path}")
async def download_file(file_path: str):
    """Download file"""
    full_path = DATA_DIR / file_path

    if not full_path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    if not full_path.is_file():
        raise HTTPException(status_code=400, detail="Not a file")

    # Security check
    try:
        full_path.resolve().relative_to(DATA_DIR.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied")

    return FileResponse(
        path=full_path,
        filename=full_path.name,
        media_type="application/octet-stream"
    )


@router.get("/stats")
async def get_data_stats():
    """Get data statistics"""
    if not DATA_DIR.exists():
        return {"total_files": 0, "total_size": 0, "by_platform": {}, "by_type": {}}

    stats = {
        "total_files": 0,
        "total_size": 0,
        "by_platform": {},
        "by_type": {}
    }

    supported_extensions = {".json", ".jsonl", ".csv", ".xlsx", ".xls"}

    for root, dirs, filenames in os.walk(DATA_DIR):
        root_path = Path(root)
        for filename in filenames:
            file_path = root_path / filename
            if file_path.suffix.lower() not in supported_extensions:
                continue

            try:
                stat = file_path.stat()
                stats["total_files"] += 1
                stats["total_size"] += stat.st_size

                # Statistics by type
                file_type = file_path.suffix[1:].lower()
                stats["by_type"][file_type] = stats["by_type"].get(file_type, 0) + 1

                # Statistics by platform (inferred from path)
                rel_path = str(file_path.relative_to(DATA_DIR))
                for platform in ["xhs", "dy", "ks", "bili", "wb", "tieba", "zhihu", "github", "arxiv"]:
                    if platform in rel_path.lower():
                        stats["by_platform"][platform] = stats["by_platform"].get(platform, 0) + 1
                        break
            except Exception:
                continue

    return stats
