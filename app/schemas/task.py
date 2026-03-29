import uuid
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field


class TaskCreateRequest(BaseModel):
    task: str = Field(..., min_length=1, max_length=5000, description="Task for the agent to perform")
    conversation_id: str | None = Field(None, description="Conversation ID for multi-turn; omit for new conversation")


class TraceStep(BaseModel):
    step: int
    type: Literal["reasoning", "tool_call", "tool_result", "final_answer"]
    content: str
    tool_name: str | None = None
    tool_input: dict | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    latency_ms: float | None = None
    timestamp: datetime


class TaskResponse(BaseModel):
    task_id: str
    status: Literal["pending", "running", "completed", "failed"]
    task_input: str
    answer: str | None = None
    trace: list[TraceStep] = []
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    total_latency_ms: float = 0.0
    conversation_id: str
    created_at: datetime
    completed_at: datetime | None = None
    error_message: str | None = None

    model_config = {"from_attributes": True}
