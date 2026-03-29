from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db
from app.services.task_service import TaskService


def get_agent(request: Request):
    return request.app.state.agent


async def get_task_service(
    db: AsyncSession = Depends(get_db),
    agent=Depends(get_agent),
) -> TaskService:
    return TaskService(db=db, agent=agent)
