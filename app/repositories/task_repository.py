import json
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.task import Task
from app.agent.trace import TraceStep


class TaskRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, task_input: str, conversation_id: str) -> Task:
        task = Task(
            task_input=task_input,
            conversation_id=conversation_id,
            status="pending",
        )
        self.db.add(task)
        await self.db.commit()
        await self.db.refresh(task)
        return task

    async def get_by_id(self, task_id: str) -> Task | None:
        result = await self.db.execute(select(Task).where(Task.id == task_id))
        return result.scalar_one_or_none()

    async def update_running(self, task: Task) -> Task:
        task.status = "running"
        await self.db.commit()
        await self.db.refresh(task)
        return task

    async def update_completed(
        self,
        task: Task,
        answer: str,
        trace: list[TraceStep],
        total_tokens_in: int,
        total_tokens_out: int,
        total_latency_ms: float,
    ) -> Task:
        task.status = "completed"
        task.answer = answer
        task.trace_json = json.dumps([step.model_dump(mode="json") for step in trace])
        task.total_tokens_in = total_tokens_in
        task.total_tokens_out = total_tokens_out
        task.total_latency_ms = total_latency_ms
        task.completed_at = datetime.now(timezone.utc)
        await self.db.commit()
        await self.db.refresh(task)
        return task

    async def update_failed(self, task: Task, error_message: str) -> Task:
        task.status = "failed"
        task.error_message = error_message
        task.completed_at = datetime.now(timezone.utc)
        await self.db.commit()
        await self.db.refresh(task)
        return task
