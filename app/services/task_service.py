import uuid
import json
from sqlalchemy.ext.asyncio import AsyncSession
from app.repositories.task_repository import TaskRepository
from app.agent.runner import run_agent
from app.schemas.task import TaskResponse, TraceStep as SchemaTraceStep
from app.models.task import Task
from app.core.logging import logger


def _task_to_response(task: Task) -> TaskResponse:
    trace = []
    if task.trace_json:
        try:
            raw_trace = json.loads(task.trace_json)
            trace = [SchemaTraceStep(**step) for step in raw_trace]
        except Exception as exc:
            logger.warning("trace_deserialize_failed", task_id=task.id, error=str(exc))

    return TaskResponse(
        task_id=task.id,
        status=task.status,
        task_input=task.task_input,
        answer=task.answer,
        trace=trace,
        total_tokens_in=task.total_tokens_in,
        total_tokens_out=task.total_tokens_out,
        total_latency_ms=task.total_latency_ms,
        conversation_id=task.conversation_id,
        created_at=task.created_at,
        completed_at=task.completed_at,
        error_message=task.error_message,
    )


class TaskService:
    def __init__(self, db: AsyncSession, agent):
        self.db = db
        self.agent = agent
        self.repo = TaskRepository(db)

    async def create_and_run(self, task_input: str, conversation_id: str | None = None) -> TaskResponse:
        if conversation_id is None:
            conversation_id = str(uuid.uuid4())

        task = await self.repo.create(task_input, conversation_id)
        logger.info("task_created", task_id=task.id, conversation_id=conversation_id)

        task = await self.repo.update_running(task)

        try:
            result = await run_agent(self.agent, task_input, conversation_id, task_id=task.id)
            task = await self.repo.update_completed(
                task,
                answer=result.answer,
                trace=result.trace,
                total_tokens_in=result.total_tokens_in,
                total_tokens_out=result.total_tokens_out,
                total_latency_ms=result.total_latency_ms,
            )
            logger.info("task_completed", task_id=task.id)
        except Exception as e:
            error_msg = str(e)
            logger.error("task_failed", task_id=task.id, error=error_msg)
            task = await self.repo.update_failed(task, error_msg)

        return _task_to_response(task)


    async def get_task(self, task_id: str) -> TaskResponse | None:
        task = await self.repo.get_by_id(task_id)
        if task is None:
            return None
        return _task_to_response(task)
