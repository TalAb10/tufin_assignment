from __future__ import annotations

import time
from typing import Any
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.outputs import LLMResult
from opentelemetry.trace import Span, SpanKind, StatusCode

from app.core.telemetry import get_tracer, get_meter
from app.core.logging import logger


class OtelCallbackHandler(AsyncCallbackHandler):
    """
    LangChain async callback handler that creates OTel spans for every
    LLM call and tool call made by the LangGraph react agent.

    A new instance must be created per ainvoke() call to avoid run_id
    collisions between concurrent requests.

    Span hierarchy (parent attached via OTel contextvars propagation):

        invoke_agent  [set as current span by runner.py]
          ├── chat {model}             [CLIENT — each LLM reasoning step]
          ├── execute_tool web_search  [INTERNAL — each tool call]
          └── chat {model}             [CLIENT — final answer]
    """

    def __init__(self) -> None:
        super().__init__()
        self._tracer = get_tracer()
        self._meter = get_meter()

        self._llm_spans: dict[UUID, Span] = {}
        self._llm_model_names: dict[UUID, str] = {}
        self._llm_start_times: dict[UUID, float] = {}

        self._tool_spans: dict[UUID, Span] = {}
        self._tool_names: dict[UUID, str] = {}
        self._tool_start_times: dict[UUID, float] = {}

        # Accumulated latencies for trace annotation (ms, in completion order)
        self.llm_latencies: list[float] = []
        self.tool_latencies: dict[str, list[float]] = {}

        self._op_duration = self._meter.create_histogram(
            name="gen_ai.client.operation.duration",
            description="Duration of GenAI client operations",
            unit="s",
        )
        self._token_usage = self._meter.create_histogram(
            name="gen_ai.client.token.usage",
            description="Token usage per LLM request",
            unit="{token}",
        )

    # ── LLM events ───────────────────────────────────────────────────────────

    async def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list],
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        model_name: str = (
            serialized.get("kwargs", {}).get("model_name")
            or serialized.get("kwargs", {}).get("model")
            or "unknown"
        )

        span = self._tracer.start_span(
            name=f"chat {model_name}",
            kind=SpanKind.CLIENT,
            attributes={
                "gen_ai.operation.name": "chat",
                "gen_ai.system": "openai",
                "gen_ai.request.model": model_name,
            },
        )
        self._llm_spans[run_id] = span
        self._llm_model_names[run_id] = model_name
        self._llm_start_times[run_id] = time.perf_counter()
        logger.debug("otel_llm_span_started", run_id=str(run_id), model=model_name)

    async def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        span = self._llm_spans.pop(run_id, None)
        model_name = self._llm_model_names.pop(run_id, "unknown")
        start_time = self._llm_start_times.pop(run_id, None)
        if span is None:
            return

        duration_s = (time.perf_counter() - start_time) if start_time else 0.0
        self.llm_latencies.append(round(duration_s * 1000, 2))

        input_tokens = 0
        output_tokens = 0
        finish_reasons: list[str] = []

        for gen_list in response.generations:
            for gen in gen_list:
                msg = getattr(gen, "message", None)
                if msg and hasattr(msg, "usage_metadata") and msg.usage_metadata:
                    input_tokens += msg.usage_metadata.get("input_tokens", 0)
                    output_tokens += msg.usage_metadata.get("output_tokens", 0)
                gen_info = getattr(gen, "generation_info", None) or {}
                if gen_info.get("finish_reason"):
                    finish_reasons.append(gen_info["finish_reason"])

        span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
        span.set_attribute("gen_ai.usage.output_tokens", output_tokens)
        if finish_reasons:
            span.set_attribute("gen_ai.response.finish_reasons", finish_reasons)
        span.set_status(StatusCode.OK)
        span.end()

        metric_attrs = {
            "gen_ai.operation.name": "chat",
            "gen_ai.system": "openai",
            "gen_ai.request.model": model_name,
        }
        self._op_duration.record(duration_s, attributes=metric_attrs)
        if input_tokens:
            self._token_usage.record(
                input_tokens,
                attributes={**metric_attrs, "gen_ai.token.type": "input"},
            )
        if output_tokens:
            self._token_usage.record(
                output_tokens,
                attributes={**metric_attrs, "gen_ai.token.type": "output"},
            )

        logger.debug(
            "otel_llm_span_ended",
            run_id=str(run_id),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            duration_s=round(duration_s, 3),
        )

    async def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        span = self._llm_spans.pop(run_id, None)
        self._llm_model_names.pop(run_id, None)
        self._llm_start_times.pop(run_id, None)
        if span is None:
            return
        span.record_exception(error)
        span.set_status(StatusCode.ERROR, str(error))
        span.end()

    # ── Tool events ──────────────────────────────────────────────────────────

    async def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        tool_name: str = serialized.get("name") or "unknown_tool"

        span = self._tracer.start_span(
            name=f"execute_tool {tool_name}",
            kind=SpanKind.INTERNAL,
            attributes={
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": tool_name,
                "gen_ai.tool.input": input_str[:1000],
            },
        )
        self._tool_spans[run_id] = span
        self._tool_names[run_id] = tool_name
        self._tool_start_times[run_id] = time.perf_counter()
        logger.debug("otel_tool_span_started", run_id=str(run_id), tool=tool_name)

    async def on_tool_end(
        self,
        output: Any,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        span = self._tool_spans.pop(run_id, None)
        tool_name = self._tool_names.pop(run_id, "unknown_tool")
        start_time = self._tool_start_times.pop(run_id, None)
        if span is None:
            return

        duration_s = (time.perf_counter() - start_time) if start_time else 0.0
        self.tool_latencies.setdefault(tool_name, []).append(round(duration_s * 1000, 2))
        span.set_attribute("gen_ai.tool.output", str(output)[:2000])
        span.set_status(StatusCode.OK)
        span.end()

        self._op_duration.record(
            duration_s,
            attributes={
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": tool_name,
            },
        )
        logger.debug("otel_tool_span_ended", run_id=str(run_id), duration_s=round(duration_s, 3))

    async def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        span = self._tool_spans.pop(run_id, None)
        self._tool_names.pop(run_id, None)
        self._tool_start_times.pop(run_id, None)
        if span is None:
            return
        span.record_exception(error)
        span.set_status(StatusCode.ERROR, str(error))
        span.end()
