from fastapi import APIRouter, Depends, HTTPException
from app.api.dependencies import get_task_service
from app.schemas.task import TaskCreateRequest, TaskResponse
from app.services.task_service import TaskService

router = APIRouter(prefix="/api/v1")


@router.post("/task", response_model=TaskResponse, status_code=201)
async def create_task(
    request: TaskCreateRequest,
    service: TaskService = Depends(get_task_service),
) -> TaskResponse:
    return await service.create_and_run(
        task_input=request.task,
        conversation_id=request.conversation_id,
    )


@router.get("/tasks/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: str,
    service: TaskService = Depends(get_task_service),
) -> TaskResponse:
    task = await service.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")
    return task
