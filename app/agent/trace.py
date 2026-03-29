from datetime import datetime, timezone
from typing import Literal
from pydantic import BaseModel
from langchain_core.messages import AIMessage, ToolMessage, HumanMessage


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


class AgentResult(BaseModel):
    answer: str
    trace: list[TraceStep]
    total_tokens_in: int
    total_tokens_out: int
    total_latency_ms: float


def parse_messages_to_trace(
    messages: list,
    llm_latencies: list[float] | None = None,
    tool_latencies: dict[str, list[float]] | None = None,
) -> tuple[list[TraceStep], int, int]:
    trace: list[TraceStep] = []
    total_tokens_in = 0
    total_tokens_out = 0
    step = 0

    # Work on copies so callers are not mutated
    llm_lat: list[float] = list(llm_latencies or [])
    tool_lat: dict[str, list[float]] = {k: list(v) for k, v in (tool_latencies or {}).items()}

    # Build a map of tool_call_id -> tool_name from AIMessages
    tool_call_id_to_name: dict[str, str] = {}

    for msg in messages:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            for tc in msg.tool_calls:
                tool_call_id_to_name[tc["id"]] = tc["name"]

    for i, msg in enumerate(messages):
        if isinstance(msg, HumanMessage):
            continue

        elif isinstance(msg, AIMessage):
            # Extract token usage split by direction
            t_in = t_out = 0
            if msg.usage_metadata:
                t_in = msg.usage_metadata.get("input_tokens", 0)
                t_out = msg.usage_metadata.get("output_tokens", 0)
                total_tokens_in += t_in
                total_tokens_out += t_out

            # Pop the latency for this LLM call (one entry per AIMessage)
            lm_ms = llm_lat.pop(0) if llm_lat else None

            # Emit reasoning step if content is non-empty and has tool calls
            if msg.content and msg.tool_calls:
                step += 1
                trace.append(TraceStep(
                    step=step,
                    type="reasoning",
                    content=str(msg.content),
                    tokens_in=t_in,
                    tokens_out=t_out,
                    latency_ms=lm_ms,
                    timestamp=datetime.now(timezone.utc),
                ))

            # Emit tool call steps
            if msg.tool_calls:
                for idx, tc in enumerate(msg.tool_calls):
                    step += 1
                    # When no reasoning step, attach tokens + LLM latency to first tool_call
                    first = idx == 0 and not msg.content
                    trace.append(TraceStep(
                        step=step,
                        type="tool_call",
                        content=f"Calling tool: {tc['name']}",
                        tool_name=tc["name"],
                        tool_input=tc.get("args", {}),
                        tokens_in=t_in if first else None,
                        tokens_out=t_out if first else None,
                        latency_ms=lm_ms if first else None,
                        timestamp=datetime.now(timezone.utc),
                    ))

            # Emit final answer
            elif msg.content:
                step += 1
                trace.append(TraceStep(
                    step=step,
                    type="final_answer",
                    content=str(msg.content),
                    tokens_in=t_in,
                    tokens_out=t_out,
                    latency_ms=lm_ms,
                    timestamp=datetime.now(timezone.utc),
                ))

        elif isinstance(msg, ToolMessage):
            step += 1
            tool_name = tool_call_id_to_name.get(msg.tool_call_id, "unknown")
            # Pop the latency for this specific tool (matched by name, in call order)
            tl_list = tool_lat.get(tool_name)
            tm_ms = tl_list.pop(0) if tl_list else None
            trace.append(TraceStep(
                step=step,
                type="tool_result",
                content=str(msg.content),
                tool_name=tool_name,
                latency_ms=tm_ms,
                timestamp=datetime.now(timezone.utc),
            ))

    return trace, total_tokens_in, total_tokens_out
