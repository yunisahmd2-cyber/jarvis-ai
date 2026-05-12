from __future__ import annotations

from fastapi import APIRouter, Query

from backend.app.models.schemas import MemoryItem, MemorySearchResponse, MemoryStoreRequest, PreferenceUpdateRequest
from backend.app.services.memory.service import memory_service


router = APIRouter(prefix="/memory", tags=["memory"])


@router.post("/store", response_model=MemoryItem)
async def store_memory(request: MemoryStoreRequest) -> MemoryItem:
    data = memory_service.store_memory(request.key, request.value, request.category)
    return MemoryItem(**data)


@router.get("/search", response_model=MemorySearchResponse)
async def search_memory(q: str = Query(default="")) -> MemorySearchResponse:
    return MemorySearchResponse(results=[MemoryItem(**item) for item in memory_service.search_memory(q)])


@router.get("/preferences", response_model=list[MemoryItem])
async def list_preferences() -> list[MemoryItem]:
    return [MemoryItem(**item) for item in memory_service.list_preferences()]


@router.post("/preferences", response_model=MemoryItem)
async def set_preference(request: PreferenceUpdateRequest) -> MemoryItem:
    data = memory_service.store_memory(request.key, request.value, "preference")
    return MemoryItem(**data)
