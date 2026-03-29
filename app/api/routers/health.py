from fastapi import APIRouter
from app.tools.registry import ToolRegistry

router = APIRouter()


@router.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "tools_registered": ToolRegistry.count(),
        "tool_names": ToolRegistry.tool_names(),
    }
