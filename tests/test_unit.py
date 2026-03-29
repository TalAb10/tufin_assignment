"""
Unit tests — no HTTP client, no database, no network required.

Covers:
  - Tools (calculator, unit converter, database query security, registry)
  - OTel callback handler (span attributes, error handling, concurrent run_ids)
  - run_agent() span creation (invoke_agent root span via InMemorySpanExporter)
"""

import pytest
from uuid import uuid4

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import LLMResult, ChatGeneration
from langchain_core.tools import StructuredTool

from app.tools.calculator.tool import CalculatorTool
from app.tools.unit_converter.tool import UnitConverterTool
from app.tools.registry import ToolRegistry
from app.agent.otel_callback import OtelCallbackHandler


# ── Shared OTel fixture ───────────────────────────────────────────────────────

@pytest.fixture
def span_exporter():
    """
    Build an in-memory tracer and patch get_tracer() at the module level in
    both otel_callback and runner so all span creation goes to the in-memory
    exporter.

    We cannot replace the global TracerProvider once it has been set (the OTel
    SDK rejects overrides), so we patch the call site instead.
    SimpleSpanProcessor is synchronous — spans are queryable immediately after
    span.end() with no flush needed.
    """
    from unittest.mock import patch

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    test_tracer = provider.get_tracer("test")

    with patch("app.agent.otel_callback.get_tracer", return_value=test_tracer), \
         patch("app.agent.runner.get_tracer", return_value=test_tracer):
        yield exporter

    exporter.clear()


def _llm_result(input_tokens: int = 100, output_tokens: int = 50, finish_reason: str = "stop") -> LLMResult:
    # langchain-core requires total_tokens in usage_metadata
    msg = AIMessage(
        content="assistant reply",
        usage_metadata={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
    )
    gen = ChatGeneration(message=msg, text="assistant reply", generation_info={"finish_reason": finish_reason})
    return LLMResult(generations=[[gen]])


# ── Calculator ────────────────────────────────────────────────────────────────

class TestCalculator:
    @pytest.mark.asyncio
    async def test_addition(self):
        assert await CalculatorTool().execute(expression="2 + 2") == "4"

    @pytest.mark.asyncio
    async def test_percentage(self):
        assert await CalculatorTool().execute(expression="15 * 200 / 100") == "30.0"

    @pytest.mark.asyncio
    async def test_division(self):
        assert await CalculatorTool().execute(expression="10 / 4") == "2.5"

    @pytest.mark.asyncio
    async def test_invalid_expression_returns_error(self):
        result = await CalculatorTool().execute(expression="abc + !")
        assert "error" in result.lower()


# ── Unit Converter ────────────────────────────────────────────────────────────

class TestUnitConverter:
    @pytest.mark.asyncio
    async def test_km_to_miles(self):
        result = await UnitConverterTool().execute(value=100, from_unit="km", to_unit="miles")
        assert "62.1371" in result

    @pytest.mark.asyncio
    async def test_celsius_to_fahrenheit(self):
        result = await UnitConverterTool().execute(value=0, from_unit="celsius", to_unit="fahrenheit")
        assert "32.00" in result

    @pytest.mark.asyncio
    async def test_kg_to_lbs(self):
        result = await UnitConverterTool().execute(value=10, from_unit="kg", to_unit="lbs")
        assert "22.0462" in result

    @pytest.mark.asyncio
    async def test_unknown_unit_returns_error(self):
        result = await UnitConverterTool().execute(value=1, from_unit="parsecs", to_unit="km")
        assert "Unknown unit" in result


# ── Database Query — security / input validation ──────────────────────────────

class TestDatabaseQuerySecurity:
    @pytest.mark.asyncio
    async def test_non_select_rejected(self):
        from app.tools.database_query.tool import DatabaseQueryTool
        result = await DatabaseQueryTool().execute(
            query="INSERT INTO products VALUES (1, 'x', 'y', 1.0, 1)"
        )
        assert "Only SELECT" in result

    @pytest.mark.asyncio
    async def test_drop_keyword_blocked(self):
        from app.tools.database_query.tool import DatabaseQueryTool
        result = await DatabaseQueryTool().execute(
            query="SELECT * FROM products; DROP TABLE products"
        )
        assert "blocked" in result.lower()

    @pytest.mark.asyncio
    async def test_delete_keyword_blocked(self):
        from app.tools.database_query.tool import DatabaseQueryTool
        result = await DatabaseQueryTool().execute(
            query="SELECT * FROM products; DELETE FROM products"
        )
        assert "blocked" in result.lower()


# ── Tool Registry ─────────────────────────────────────────────────────────────

class TestToolRegistry:
    def _register_all(self):
        import app.tools.calculator.factory       # noqa: F401
        import app.tools.weather.factory          # noqa: F401
        import app.tools.web_search.factory       # noqa: F401
        import app.tools.unit_converter.factory   # noqa: F401
        import app.tools.database_query.factory   # noqa: F401

    def test_all_tools_are_structured_tools(self):
        self._register_all()
        assert all(isinstance(t, StructuredTool) for t in ToolRegistry.all_langchain_tools())

    def test_all_five_tools_registered(self):
        self._register_all()
        names = {t.name for t in ToolRegistry.all_langchain_tools()}
        assert {"calculator", "weather", "web_search", "unit_converter", "database_query"}.issubset(names)


# ── OTel callback handler — chat (LLM) spans ─────────────────────────────────

class TestChatSpans:
    @pytest.mark.asyncio
    async def test_span_name_and_attributes(self, span_exporter):
        handler = OtelCallbackHandler()
        run_id = uuid4()
        await handler.on_chat_model_start({"kwargs": {"model_name": "gpt-4o"}}, [[]], run_id=run_id)
        await handler.on_llm_end(_llm_result(input_tokens=100, output_tokens=50), run_id=run_id)

        spans = span_exporter.get_finished_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.name == "chat gpt-4o"
        assert span.attributes["gen_ai.operation.name"] == "chat"
        assert span.attributes["gen_ai.system"] == "openai"
        assert span.attributes["gen_ai.request.model"] == "gpt-4o"
        assert span.attributes["gen_ai.usage.input_tokens"] == 100
        assert span.attributes["gen_ai.usage.output_tokens"] == 50
        assert span.status.status_code == StatusCode.OK

    @pytest.mark.asyncio
    async def test_finish_reason_recorded(self, span_exporter):
        handler = OtelCallbackHandler()
        run_id = uuid4()
        await handler.on_chat_model_start({"kwargs": {"model_name": "gpt-4o"}}, [[]], run_id=run_id)
        await handler.on_llm_end(_llm_result(finish_reason="tool_calls"), run_id=run_id)
        span = span_exporter.get_finished_spans()[0]
        assert "tool_calls" in str(span.attributes["gen_ai.response.finish_reasons"])

    @pytest.mark.asyncio
    async def test_model_name_fallback_to_model_key(self, span_exporter):
        handler = OtelCallbackHandler()
        run_id = uuid4()
        await handler.on_chat_model_start({"kwargs": {"model": "gpt-3.5-turbo"}}, [[]], run_id=run_id)
        await handler.on_llm_end(_llm_result(), run_id=run_id)
        span = span_exporter.get_finished_spans()[0]
        assert span.name == "chat gpt-3.5-turbo"

    @pytest.mark.asyncio
    async def test_model_name_unknown_when_absent(self, span_exporter):
        handler = OtelCallbackHandler()
        run_id = uuid4()
        await handler.on_chat_model_start({"kwargs": {}}, [[]], run_id=run_id)
        await handler.on_llm_end(_llm_result(), run_id=run_id)
        assert span_exporter.get_finished_spans()[0].name == "chat unknown"

    @pytest.mark.asyncio
    async def test_error_sets_error_status(self, span_exporter):
        handler = OtelCallbackHandler()
        run_id = uuid4()
        await handler.on_chat_model_start({"kwargs": {"model_name": "gpt-4o"}}, [[]], run_id=run_id)
        await handler.on_llm_error(ValueError("rate limit exceeded"), run_id=run_id)

        span = span_exporter.get_finished_spans()[0]
        assert span.status.status_code == StatusCode.ERROR
        assert "rate limit exceeded" in span.status.description

    @pytest.mark.asyncio
    async def test_error_cleans_up_state(self, span_exporter):
        handler = OtelCallbackHandler()
        run_id = uuid4()
        await handler.on_chat_model_start({"kwargs": {"model_name": "gpt-4o"}}, [[]], run_id=run_id)
        await handler.on_llm_error(RuntimeError("boom"), run_id=run_id)
        assert run_id not in handler._llm_spans
        assert run_id not in handler._llm_model_names
        assert run_id not in handler._llm_start_times


# ── OTel callback handler — tool spans ───────────────────────────────────────

class TestToolSpans:
    @pytest.mark.asyncio
    async def test_span_name_and_attributes(self, span_exporter):
        handler = OtelCallbackHandler()
        run_id = uuid4()
        await handler.on_tool_start({"name": "calculator"}, '{"expression": "2+2"}', run_id=run_id)
        await handler.on_tool_end("4", run_id=run_id)

        spans = span_exporter.get_finished_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.name == "execute_tool calculator"
        assert span.attributes["gen_ai.operation.name"] == "execute_tool"
        assert span.attributes["gen_ai.tool.name"] == "calculator"
        assert span.attributes["gen_ai.tool.input"] == '{"expression": "2+2"}'
        assert span.attributes["gen_ai.tool.output"] == "4"
        assert span.status.status_code == StatusCode.OK

    @pytest.mark.asyncio
    async def test_input_truncated_at_1kb(self, span_exporter):
        handler = OtelCallbackHandler()
        run_id = uuid4()
        await handler.on_tool_start({"name": "web_search"}, "x" * 2000, run_id=run_id)
        await handler.on_tool_end("result", run_id=run_id)
        assert len(span_exporter.get_finished_spans()[0].attributes["gen_ai.tool.input"]) == 1000

    @pytest.mark.asyncio
    async def test_output_truncated_at_2kb(self, span_exporter):
        handler = OtelCallbackHandler()
        run_id = uuid4()
        await handler.on_tool_start({"name": "web_search"}, "query", run_id=run_id)
        await handler.on_tool_end("y" * 5000, run_id=run_id)
        assert len(span_exporter.get_finished_spans()[0].attributes["gen_ai.tool.output"]) == 2000

    @pytest.mark.asyncio
    async def test_error_sets_error_status(self, span_exporter):
        handler = OtelCallbackHandler()
        run_id = uuid4()
        await handler.on_tool_start({"name": "web_search"}, "query", run_id=run_id)
        await handler.on_tool_error(RuntimeError("timeout"), run_id=run_id)
        assert span_exporter.get_finished_spans()[0].status.status_code == StatusCode.ERROR

    @pytest.mark.asyncio
    async def test_error_cleans_up_state(self, span_exporter):
        handler = OtelCallbackHandler()
        run_id = uuid4()
        await handler.on_tool_start({"name": "calculator"}, "1+1", run_id=run_id)
        await handler.on_tool_error(RuntimeError("boom"), run_id=run_id)
        assert run_id not in handler._tool_spans
        assert run_id not in handler._tool_names
        assert run_id not in handler._tool_start_times


# ── OTel callback handler — concurrent run_ids ────────────────────────────────

class TestConcurrentRunIds:
    @pytest.mark.asyncio
    async def test_two_llm_calls_dont_collide(self, span_exporter):
        handler = OtelCallbackHandler()
        id1, id2 = uuid4(), uuid4()
        await handler.on_chat_model_start({"kwargs": {"model_name": "gpt-4o"}}, [[]], run_id=id1)
        await handler.on_chat_model_start({"kwargs": {"model_name": "gpt-4o"}}, [[]], run_id=id2)
        await handler.on_llm_end(_llm_result(input_tokens=10), run_id=id1)
        await handler.on_llm_end(_llm_result(input_tokens=20), run_id=id2)

        spans = span_exporter.get_finished_spans()
        assert len(spans) == 2
        assert {s.attributes["gen_ai.usage.input_tokens"] for s in spans} == {10, 20}

    @pytest.mark.asyncio
    async def test_llm_and_tool_spans_coexist(self, span_exporter):
        handler = OtelCallbackHandler()
        llm_id, tool_id = uuid4(), uuid4()
        await handler.on_chat_model_start({"kwargs": {"model_name": "gpt-4o"}}, [[]], run_id=llm_id)
        await handler.on_tool_start({"name": "calculator"}, "1+1", run_id=tool_id)
        await handler.on_llm_end(_llm_result(), run_id=llm_id)
        await handler.on_tool_end("2", run_id=tool_id)

        names = {s.name for s in span_exporter.get_finished_spans()}
        assert names == {"chat gpt-4o", "execute_tool calculator"}


# ── run_agent() — invoke_agent root span ─────────────────────────────────────

class TestInvokeAgentSpan:
    @pytest.mark.asyncio
    async def test_span_created_with_correct_attributes(self, span_exporter):
        from app.agent.runner import run_agent

        class MockAgent:
            async def ainvoke(self, input_msg, config=None):
                return {"messages": [
                    HumanMessage(content="What is 2+2?"),
                    AIMessage(content="4", usage_metadata={"input_tokens": 50, "output_tokens": 5, "total_tokens": 55}),
                ]}

        result = await run_agent(
            MockAgent(), task="What is 2+2?", conversation_id="conv-123", task_id="task-abc"
        )

        invoke_spans = [s for s in span_exporter.get_finished_spans() if s.name == "invoke_agent multi-tool-agent"]
        assert len(invoke_spans) == 1
        span = invoke_spans[0]
        assert span.attributes["gen_ai.operation.name"] == "invoke_agent"
        assert span.attributes["gen_ai.conversation.id"] == "conv-123"
        assert span.attributes["task_id"] == "task-abc"
        assert span.attributes["gen_ai.usage.input_tokens"] == 50
        assert span.attributes["gen_ai.usage.output_tokens"] == 5
        assert span.status.status_code == StatusCode.OK
        assert result.answer == "4"

    @pytest.mark.asyncio
    async def test_task_id_omitted_when_not_provided(self, span_exporter):
        from app.agent.runner import run_agent

        class MockAgent:
            async def ainvoke(self, input_msg, config=None):
                return {"messages": [HumanMessage(content="hi"), AIMessage(content="hello")]}

        await run_agent(MockAgent(), task="hi", conversation_id="conv-1")
        invoke_spans = [s for s in span_exporter.get_finished_spans() if s.name == "invoke_agent multi-tool-agent"]
        assert "task_id" not in invoke_spans[0].attributes

    @pytest.mark.asyncio
    async def test_span_error_on_agent_exception(self, span_exporter):
        from app.agent.runner import run_agent

        class FailingAgent:
            async def ainvoke(self, input_msg, config=None):
                raise RuntimeError("LLM unavailable")

        with pytest.raises(RuntimeError, match="LLM unavailable"):
            await run_agent(FailingAgent(), task="test", conversation_id="conv-err")

        invoke_spans = [s for s in span_exporter.get_finished_spans() if s.name == "invoke_agent multi-tool-agent"]
        assert invoke_spans[0].status.status_code == StatusCode.ERROR
