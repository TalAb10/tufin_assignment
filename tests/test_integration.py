"""
Integration tests — require the real stack (docker compose up).

All tests hit http://localhost:8000 with a real LLM, real PostgreSQL, and real Redis.
Assertions are behavioral: correct tool used, key numbers in answer, task lifecycle.
Exact LLM phrasing is never asserted.
"""

import pytest


# ── Health ────────────────────────────────────────────────────────────────────

class TestHealth:
    @pytest.mark.asyncio
    async def test_returns_healthy(self, client):
        data = (await client.get("/health")).json()
        assert data["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_all_five_tools_registered(self, client):
        data = (await client.get("/health")).json()
        assert data["tools_registered"] == 5
        assert set(data["tool_names"]) == {
            "calculator", "weather", "web_search", "unit_converter", "database_query"
        }


# ── Task lifecycle ────────────────────────────────────────────────────────────

class TestTaskLifecycle:
    @pytest.mark.asyncio
    async def test_post_returns_201_with_required_fields(self, client):
        resp = await client.post("/api/v1/task", json={"task": "What is 1 + 1?"})
        assert resp.status_code == 201
        data = resp.json()
        assert data["task_id"]
        assert data["status"] == "completed"
        assert isinstance(data["answer"], str) and data["answer"]
        assert isinstance(data["trace"], list) and len(data["trace"]) >= 1
        assert data["total_tokens_in"] > 0
        assert data["total_tokens_out"] > 0
        assert data["total_latency_ms"] > 0
        assert data["conversation_id"]

    @pytest.mark.asyncio
    async def test_get_retrieves_same_task(self, client):
        post = await client.post("/api/v1/task", json={"task": "What is 2 + 3?"})
        assert post.status_code == 201
        task_id = post.json()["task_id"]

        get = await client.get(f"/api/v1/tasks/{task_id}")
        assert get.status_code == 200
        data = get.json()
        assert data["task_id"] == task_id
        assert data["status"] == "completed"
        assert data["answer"] == post.json()["answer"]

    @pytest.mark.asyncio
    async def test_get_unknown_id_returns_404(self, client):
        resp = await client.get("/api/v1/tasks/does-not-exist")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_explicit_conversation_id_preserved(self, client):
        conv_id = "lifecycle-test-conv-001"
        data = (await client.post(
            "/api/v1/task", json={"task": "Hello", "conversation_id": conv_id}
        )).json()
        assert data["conversation_id"] == conv_id


# ── Calculator tool ───────────────────────────────────────────────────────────

class TestCalculatorTool:
    def _find_tool_step(self, trace: list, tool_name: str) -> dict | None:
        return next(
            (s for s in trace if s.get("type") == "tool_call" and s.get("tool_name") == tool_name),
            None,
        )

    @pytest.mark.asyncio
    async def test_addition_uses_calculator_and_returns_correct_result(self, client):
        data = (await client.post("/api/v1/task", json={"task": "What is 25 + 75?"})).json()
        assert data["status"] == "completed"
        print(f"\n[PLAN]: {data.get('plan', '(none)')}")
        print(f"[TRACE]: {[(s['type'], s.get('tool_name'), s['content'][:60]) for s in data['trace']]}")
        step = self._find_tool_step(data["trace"], "calculator")
        assert step is not None, "expected a calculator tool_call in trace"
        assert "100" in data["answer"]

    @pytest.mark.asyncio
    async def test_tip_calculation_correct(self, client):
        # 18% of 74.50 = 13.41
        data = (await client.post(
            "/api/v1/task", json={"task": "What is an 18% tip on a $74.50 bill?"}
        )).json()
        assert data["status"] == "completed"
        step = self._find_tool_step(data["trace"], "calculator")
        assert step is not None, "expected a calculator tool_call in trace"
        assert "13.41" in data["answer"]


# ── Unit converter tool ───────────────────────────────────────────────────────

class TestUnitConverterTool:
    def _find_tool_step(self, trace: list, tool_name: str) -> dict | None:
        return next(
            (s for s in trace if s.get("type") == "tool_call" and s.get("tool_name") == tool_name),
            None,
        )

    @pytest.mark.asyncio
    async def test_km_to_miles_uses_converter_and_returns_correct_result(self, client):
        data = (await client.post(
            "/api/v1/task", json={"task": "Convert 100 km to miles"}
        )).json()
        assert data["status"] == "completed"
        step = self._find_tool_step(data["trace"], "unit_converter")
        assert step is not None, "expected a unit_converter tool_call in trace"
        assert "62" in data["answer"]

    @pytest.mark.asyncio
    async def test_celsius_to_fahrenheit_correct(self, client):
        data = (await client.post(
            "/api/v1/task", json={"task": "Convert 0 degrees Celsius to Fahrenheit"}
        )).json()
        assert data["status"] == "completed"
        step = self._find_tool_step(data["trace"], "unit_converter")
        assert step is not None, "expected a unit_converter tool_call in trace"
        assert "32" in data["answer"]


# ── Weather tool ──────────────────────────────────────────────────────────────

class TestWeatherTool:
    def _find_tool_step(self, trace: list, tool_name: str) -> dict | None:
        return next(
            (s for s in trace if s.get("type") == "tool_call" and s.get("tool_name") == tool_name),
            None,
        )

    @pytest.mark.asyncio
    async def test_weather_query_uses_weather_tool(self, client):
        data = (await client.post(
            "/api/v1/task", json={"task": "What is the current weather in London?"}
        )).json()
        assert data["status"] == "completed"
        step = self._find_tool_step(data["trace"], "weather")
        assert step is not None, "expected a weather tool_call in trace"
        assert isinstance(data["answer"], str) and len(data["answer"]) > 0


# ── Multi-tool tasks ─────────────────────────────────────────────────────────

class TestMultiTool:
    def _tool_names_used(self, trace: list) -> set[str]:
        return {s["tool_name"] for s in trace if s.get("type") == "tool_call"}

    @pytest.mark.asyncio
    async def test_calculator_and_unit_converter_both_called(self, client):
        # "20% of 500" → calculator → 100; "convert 100 km to miles" → unit_converter → 62.14
        data = (await client.post(
            "/api/v1/task",
            json={"task": "What is 20% of 500? Then convert that number of km to miles."},
        )).json()
        assert data["status"] == "completed"
        tools_used = self._tool_names_used(data["trace"])
        assert "calculator" in tools_used, f"calculator not in trace tools: {tools_used}"
        assert "unit_converter" in tools_used, f"unit_converter not in trace tools: {tools_used}"
        assert "62" in data["answer"]

    @pytest.mark.asyncio
    async def test_weather_and_unit_converter_both_called(self, client):
        # Current weather returns a live temperature the LLM cannot know from memory,
        # so it must call weather first, then unit_converter on the result.
        data = (await client.post(
            "/api/v1/task",
            json={"task": "What is the current temperature in Berlin? Give me the answer in both Celsius and Fahrenheit."},
        )).json()
        assert data["status"] == "completed"
        print(f"\n[PLAN]: {data.get('plan', '(none)')}")
        print(f"[TRACE STEPS]: {[(s['type'], s.get('tool_name'), s['content'][:60]) for s in data['trace']]}")
        tools_used = self._tool_names_used(data["trace"])
        assert "weather" in tools_used, f"weather not in trace tools: {tools_used}"
        assert "unit_converter" in tools_used, f"unit_converter not in trace tools: {tools_used}"
        assert len(data["trace"]) >= 4  # at least 2 tool_calls + 2 tool_results


# ── Multi-turn conversation ───────────────────────────────────────────────────

class TestMultiTurn:
    @pytest.mark.asyncio
    async def test_context_maintained_across_turns(self, client):
        conv_id = "integration-multiturn-001"

        turn1 = (await client.post(
            "/api/v1/task",
            json={"task": "My favourite city is Tokyo. Remember that.", "conversation_id": conv_id},
        )).json()
        assert turn1["status"] == "completed"
        assert turn1["conversation_id"] == conv_id

        turn2 = (await client.post(
            "/api/v1/task",
            json={"task": "What is my favourite city?", "conversation_id": conv_id},
        )).json()
        assert turn2["status"] == "completed"
        assert turn2["conversation_id"] == conv_id
        assert "Tokyo" in turn2["answer"]
