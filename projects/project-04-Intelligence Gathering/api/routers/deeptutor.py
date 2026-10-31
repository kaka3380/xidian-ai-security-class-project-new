"""Project 04 archive -> project 03 learning integration."""

from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

from api.services import deeptutor_bridge as bridge
from .scientist import _vault_info, _vault_path_for_kind

router = APIRouter(tags=["deeptutor"])


@router.get("/deeptutor", include_in_schema=False)
def bridge_page():
    return RedirectResponse("/?view=learning")


@router.get("/api/deeptutor/status")
async def bridge_status():
    try:
        result = await bridge.status()
    except HTTPException as exc:
        result = {"available": False, "reason": exc.detail, "knowledge_bases": {}}
    result["vaults"] = [_vault_info(kind) for kind in bridge.KB_NAMES]
    return result


@router.post("/api/deeptutor/sync/{kind}")
async def sync_to_deeptutor(kind: Literal["academic", "social"]):
    return await bridge.sync(kind, _vault_path_for_kind(kind))
