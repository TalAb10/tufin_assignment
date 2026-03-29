import time
from langchain_core.messages import HumanMessage
from opentelemetry import trace
from opentelemetry.trace import StatusCode
from app.agent.trace import parse_messages_to_trace, AgentResult
from app.agent.otel_callback import OtelCallbackHandler
from app.core.telemetry import get_tracer
from app.core.logging import logger


async def run_agent(
    agent,
    task: str,
    conversation_id: str,
    task_id: str = "",
) -> AgentResult:
    tracer = get_tracer()
    input_msg = {"messages": [HumanMessage(content=task)]}

    logger.info("agent_run_start", conversation_id=conversation_id, task_preview=task[:100])

    with tracer.start_as_current_span(
        "invoke_agent multi-tool-agent",
        kind=trace.SpanKind.INTERNAL,
    ) as agent_span:
        agent_span.set_attribute("gen_ai.operation.name", "invoke_agent")
        agent_span.set_attribute("gen_ai.system", "openai")
        agent_span.set_attribute("gen_ai.conversation.id", conversation_id)
        if task_id:
            agent_span.set_attribute("task_id", task_id)

        otel_handler = OtelCallbackHandler()
        config = {
            "configurable": {"thread_id": conversation_id},
            "callbacks": [otel_handler],
        }

        start = time.perf_counter()
        try:
            final_state = await agent.ainvoke(input_msg, config=config)
        except Exception as exc:
            agent_span.record_exception(exc)
            agent_span.set_status(StatusCode.ERROR, str(exc))
            raise

        total_latency_ms = (time.perf_counter() - start) * 1000

        messages = final_state["messages"]
        trace_steps, total_tokens_in, total_tokens_out = parse_messages_to_trace(
            messages,
            llm_latencies=otel_handler.llm_latencies,
            tool_latencies=otel_handler.tool_latencies,
        )

        # Last message should be the final AIMessage
        answer = ""
        for msg in reversed(messages):
            if hasattr(msg, "content") and msg.content and not getattr(msg, "tool_calls", None):
                answer = str(msg.content)
                break

        agent_span.set_attribute("gen_ai.usage.input_tokens", total_tokens_in)
        agent_span.set_attribute("gen_ai.usage.output_tokens", total_tokens_out)
        agent_span.set_status(StatusCode.OK)

    logger.info(
        "agent_run_complete",
        conversation_id=conversation_id,
        total_tokens_in=total_tokens_in,
        total_tokens_out=total_tokens_out,
        latency_ms=round(total_latency_ms, 2),
        trace_steps=len(trace_steps),
    )

    return AgentResult(
        answer=answer,
        trace=trace_steps,
        total_tokens_in=total_tokens_in,
        total_tokens_out=total_tokens_out,
        total_latency_ms=total_latency_ms,
    )
