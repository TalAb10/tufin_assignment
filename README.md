# Multi-Tool AI Agent

A production-quality REST API that wraps a **LangGraph ReAct agent** (GPT-5.4) with 5 integrated tools, full request observability via OpenTelemetry, and persistent multi-turn conversation memory.

---

## Features

- **5 tools** — calculator, weather, web search, unit converter, product database query
- **Full trace** — every task stores each reasoning step, tool call, and tool result in PostgreSQL
- **Multi-turn memory** — conversations persist across requests via LangGraph + Redis checkpointing
- **OpenTelemetry** — every request produces nested spans (`invoke_agent` → `chat` → `execute_tool`) with `gen_ai.*` semantic attributes, exported to Jaeger
- **No-key external APIs** — weather (wttr.in) and search (DuckDuckGo) require no API key
- **Abstract Factory tool system** — adding a new tool touches zero existing code
- **Docker-ready** — multi-stage build, non-root user, all services in docker-compose

---

## Architecture Overview

```
                          ┌─────────────────────────────────────────────┐
  Client (browser/curl)   │               Docker Compose                │
        │                 │                                              │
        ▼                 │   ┌─────────┐      ┌──────────────────────┐ │
  ┌──────────┐            │   │  Nginx  │─────▶│  FastAPI (app/main)  │ │
  │  Browser │───────────▶│   │  :80    │      │                      │ │
  └──────────┘            │   └─────────┘      │  POST /api/v1/task   │ │
                          │                    │    TaskService        │ │
                          │                    │      │                │ │
                          │                    │      ▼                │ │
                          │                    │  run_agent()          │ │
                          │                    │  LangGraph ReAct      │ │
                          │                    │  Agent                │ │
                          │                    │  ├─ calculator        │ │
                          │                    │  ├─ weather           │ │
                          │                    │  ├─ web_search        │ │
                          │                    │  ├─ unit_converter    │ │
                          │                    │  └─ database_query    │ │
                          │                    └──────────────────────┘ │
                          │                             │  │             │
                          │   ┌────────────┐            │  │             │
                          │   │ PostgreSQL │◀───────────┘  │             │
                          │   │  tasks +   │  task rows    │ OTel spans  │
                          │   │  catalog   │               │             │
                          │   └────────────┘    ┌──────────┴──────────┐ │
                          │                     │       Jaeger         │ │
                          │   ┌────────┐        │  traces :16686       │ │
                          │   │ Redis  │◀───────┴──────────────────────┘ │
                          │   │ :6379  │  conv. checkpoints              │
                          │   └────────┘                                 │
                          └─────────────────────────────────────────────┘
```

**Layers:**

| Layer | Responsibility |
|---|---|
| **Nginx** | Reverse proxy; serves the static frontend on `/`, routes `/api/*` and `/docs` to FastAPI |
| **FastAPI** | HTTP API, dependency injection, request validation, lifespan startup (OTel, DB, tools) |
| **TaskService** | Orchestrates task lifecycle: creates DB row → runs agent → persists result |
| **LangGraph ReAct Agent** | Drives the reasoning loop; calls tools; history stored in Redis per `conversation_id` |
| **Tool Registry** | Abstract-factory system; 5 tools self-register at import time |
| **PostgreSQL** | Persists `tasks` rows (input, answer, trace JSON, tokens, latency) + product catalog |
| **Redis** | Stores LangGraph message-state checkpoints for multi-turn conversations |
| **Jaeger** | Receives OTLP spans; provides trace UI at `:16686` |

---

## Quick Start

### Prerequisites

- Docker + Docker Compose
- OpenAI API key

```bash
# Set OPENAI_API_KEY in .env

docker compose up --build
```

To stop:

```bash
docker compose down
```

| Service | URL |
|---|---|
| Frontend UI | http://localhost |
| API docs | http://localhost/docs |
| Jaeger traces | http://localhost:16686 |

---

## Running Tests

Unit tests require no external services. Integration tests require the full stack (real LLM, PostgreSQL, Redis).

```bash
# Start the stack first
docker compose up -d --build

# Run all tests
docker compose exec agent python3 -m pytest tests/ -v --asyncio-mode=auto
```

### Test files

| File | What it tests |
|---|---|
| `tests/test_unit.py` | Tools, OTel callback handler, `run_agent()` spans — no HTTP, no DB, no LLM |
| `tests/test_integration.py` | HTTP endpoints, task lifecycle, real tool behavior (requires running stack) |

---

## API Reference

### `POST /api/v1/task`

Run a task. Returns when the agent completes (synchronous).

**Request**
```json
{
  "task": "What is 15% tip on $47.50?",
  "conversation_id": null
}
```

| Field | Required | Description |
|---|---|---|
| `task` | Yes | Natural-language task |
| `conversation_id` | No | Omit to start a new conversation; pass a prior value to continue one |

**Response**
```json
{
  "task_id": "3f2e1a0b-...",
  "status": "completed",
  "task_input": "What is 15% tip on $47.50?",
  "answer": "A 15% tip on $47.50 is $7.13.",
  "trace": [
    { "step": 1, "type": "tool_call", "tool_name": "calculator", "tool_input": { "expression": "47.50 * 0.15" }, "content": "Calling tool: calculator", "timestamp": "..." },
    { "step": 2, "type": "tool_result", "tool_name": "calculator", "content": "7.125", "timestamp": "..." },
    { "step": 3, "type": "final_answer", "content": "A 15% tip on $47.50 is $7.13.", "tokens_in": 75, "tokens_out": 12, "timestamp": "..." }
  ],
  "total_tokens_in": 255,
  "total_tokens_out": 87,
  "total_latency_ms": 1823.5,
  "conversation_id": "3f2e1a0b-...",
  "created_at": "...",
  "completed_at": "..."
}
```

---

### `GET /api/v1/tasks/{task_id}`

Retrieve a previously run task by ID. Returns `404` if not found.

---

### `GET /health`

```json
{
  "status": "healthy",
  "tools_registered": 5,
  "tool_names": ["calculator", "weather", "web_search", "unit_converter", "database_query"]
}
```

---

## Multi-Turn Conversations

Every response includes a `conversation_id`. Pass it back in the next request to continue the same conversation.

```bash
# Turn 1 — start a new conversation
curl -X POST http://localhost/api/v1/task \
  -H "Content-Type: application/json" \
  -d '{"task": "What is the capital of France?"}'
# → "conversation_id": "abc-123", "answer": "Paris."

# Turn 2 — continue the same conversation
curl -X POST http://localhost/api/v1/task \
  -H "Content-Type: application/json" \
  -d '{"task": "What is the weather there?", "conversation_id": "abc-123"}'
# Agent knows "there" means Paris
```

---

## Example Tasks

Five representative tasks with expected outputs and execution traces. Token counts and timestamps are illustrative; actual values vary.

---

### Example 1 — Calculator

**Task:** `"What is 15% tip on $47.50?"`

**Expected answer:** `"A 15% tip on $47.50 is $7.13."`

**Trace:**
```json
[
  { "step": 1, "type": "tool_call",    "tool_name": "calculator", "tool_input": { "expression": "47.50 * 0.15" }, "content": "Calling tool: calculator", "tokens_in": 700, "tokens_out": 91, "timestamp": "..." },
  { "step": 2, "type": "tool_result",  "tool_name": "calculator", "content": "7.125", "timestamp": "..." },
  { "step": 3, "type": "final_answer", "content": "A 15% tip on $47.50 is $7.13.", "tokens_in": 735, "tokens_out": 87, "timestamp": "..." }
]
```

> **Note:** OpenAI models typically emit tool calls with no accompanying text, so a `reasoning` step only appears when the LLM includes content alongside the tool call. When absent, the token count is attached to the first `tool_call` step instead.

---

### Example 2 — Unit Converter

**Task:** `"Convert 100 km to miles"`

**Expected answer:** `"100 kilometers is equal to 62.14 miles."`

**Trace:**
```json
[
  { "step": 1, "type": "reasoning",    "content": "The user wants to convert 100 km to miles. I'll use the unit converter tool.", "timestamp": "..." },
  { "step": 2, "type": "tool_call",    "tool_name": "unit_converter", "tool_input": { "value": 100, "from_unit": "km", "to_unit": "miles" }, "content": "Calling tool: unit_converter", "timestamp": "..." },
  { "step": 3, "type": "tool_result",  "tool_name": "unit_converter", "content": "100 km = 62.1371 miles", "timestamp": "..." },
  { "step": 4, "type": "final_answer", "content": "100 kilometers is equal to 62.14 miles.", "tokens_in": 60, "tokens_out": 14, "timestamp": "..." }
]
```

---

### Example 3 — Weather + Unit Converter (multi-tool)

**Task:** `"What is the current temperature in London? Give me the answer in both Celsius and Fahrenheit."`

**Expected answer:** `"The current temperature in London is 14°C (57.2°F), with partly cloudy conditions."`

**Trace:**
```json
[
  { "step": 1, "type": "reasoning",    "content": "I need to fetch the weather for London, then convert the temperature to Fahrenheit.", "timestamp": "..." },
  { "step": 2, "type": "tool_call",    "tool_name": "weather",        "tool_input": { "city": "London" }, "content": "Calling tool: weather", "timestamp": "..." },
  { "step": 3, "type": "tool_result",  "tool_name": "weather",        "content": "Weather in London, United Kingdom: 14°C, Partly cloudy, Wind: 18 km/h", "timestamp": "..." },
  { "step": 4, "type": "reasoning",    "content": "Now I'll convert 14°C to Fahrenheit using the unit converter.", "timestamp": "..." },
  { "step": 5, "type": "tool_call",    "tool_name": "unit_converter", "tool_input": { "value": 14, "from_unit": "celsius", "to_unit": "fahrenheit" }, "content": "Calling tool: unit_converter", "timestamp": "..." },
  { "step": 6, "type": "tool_result",  "tool_name": "unit_converter", "content": "14 celsius = 57.20 fahrenheit", "timestamp": "..." },
  { "step": 7, "type": "final_answer", "content": "The current temperature in London is 14°C (57.2°F), with partly cloudy conditions.", "tokens_in": 175, "tokens_out": 18, "timestamp": "..." }
]
```

---

### Example 4 — Database Query

**Task:** `"List all products in the Beverages category"`

**Expected answer:** `"The Beverages category contains 12 products, including Chai ($18.00), Chang ($19.00), Ipoh Coffee ($46.00), and Côte de Blaye ($263.50)."`

**Trace:**
```json
[
  { "step": 1, "type": "reasoning",    "content": "I'll query the product catalog for all Beverages items.", "timestamp": "..." },
  { "step": 2, "type": "tool_call",    "tool_name": "database_query", "tool_input": { "query": "SELECT name, price FROM products WHERE category = 'Beverages'" }, "content": "Calling tool: database_query", "timestamp": "..." },
  { "step": 3, "type": "tool_result",  "tool_name": "database_query", "content": "name | price\n------------\nChai | 18.00\nChang | 19.00\nGuaraná Fantástica | 4.50\nSasquatch Ale | 14.00\n...", "timestamp": "..." },
  { "step": 4, "type": "final_answer", "content": "The Beverages category contains 12 products, including Chai ($18.00), Chang ($19.00), Ipoh Coffee ($46.00), and Côte de Blaye ($263.50).", "tokens_in": 105, "tokens_out": 16, "timestamp": "..." }
]
```

---

### Example 5 — Multi-Turn Conversation

Two requests sharing the same `conversation_id`. The agent remembers Turn 1 context in Turn 2.

**Turn 1 task:** `"My favourite city is Tokyo. Remember that."`

**Turn 1 answer:** `"Got it! I'll remember that your favourite city is Tokyo."`

**Turn 1 trace:**
```json
[
  { "step": 1, "type": "final_answer", "content": "Got it! I'll remember that your favourite city is Tokyo.", "tokens_in": 35, "tokens_out": 7, "timestamp": "..." }
]
```

**Turn 2 task:** `"What is the weather in my favourite city?"` *(same `conversation_id`)*

**Turn 2 answer:** `"The current weather in Tokyo is 22°C, with clear skies and a light breeze of 10 km/h."`

**Turn 2 trace:**
```json
[
  { "step": 1, "type": "reasoning",    "content": "The user's favourite city is Tokyo (from earlier in this conversation). I'll check the weather there.", "timestamp": "..." },
  { "step": 2, "type": "tool_call",    "tool_name": "weather",       "tool_input": { "city": "Tokyo" }, "content": "Calling tool: weather", "timestamp": "..." },
  { "step": 3, "type": "tool_result",  "tool_name": "weather",       "content": "Weather in Tokyo, Japan: 22°C, Clear sky, Wind: 10 km/h", "timestamp": "..." },
  { "step": 4, "type": "final_answer", "content": "The current weather in Tokyo is 22°C, with clear skies and a light breeze of 10 km/h.", "tokens_in": 85, "tokens_out": 13, "timestamp": "..." }
]
```

---

## Tools

| Tool | Description |
|---|---|
| `calculator` | Safe AST-based math evaluation via `simpleeval`. Handles arithmetic, percentages, expressions. |
| `weather` | Current weather for any city via wttr.in (no API key). Single HTTP call — city name accepted directly, no geocoding step. Returns temperature in Celsius, wind speed, and condition. |
| `web_search` | Web search via DuckDuckGo (no API key). Returns titles, snippets, and URLs. |
| `unit_converter` | Converts between length, mass, speed, volume, data size, and temperature units. |
| `database_query` | Read-only SELECT queries against a PostgreSQL product catalog. Blocks all write operations. |

---

## Observability

Every request produces a trace in **Jaeger** at `http://localhost:16686`.

**Span hierarchy per request:**
```
POST /api/v1/task                        [HTTP SERVER]
  └── invoke_agent multi-tool-agent      [INTERNAL]
        ├── chat gpt-5.4                 [CLIENT]  gen_ai.usage.input_tokens / output_tokens
        ├── execute_tool calculator      [INTERNAL] gen_ai.tool.name / input / output
        └── chat gpt-5.4                 [CLIENT]  gen_ai.response.finish_reasons=["stop"]
```

Attributes follow the [OpenTelemetry GenAI semantic conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/) (`gen_ai.*` namespace).

---

## Agent Reasoning Loop Design

The agent uses the **ReAct** (Reason + Act) pattern implemented by LangGraph's `create_react_agent`. Each task executes the following loop:

```
  ┌─────────────────────────────────────────────────────────┐
  │                   ReAct Loop                            │
  │                                                         │
  │  ┌──────────┐    tool_calls?     ┌──────────────────┐  │
  │  │   LLM    │──── yes ──────────▶│  Tool Executor   │  │
  │  │ (Think)  │                    │  (Act)           │  │
  │  │          │◀─── ToolMessage ───│                  │  │
  │  │ (Observe)│    (tool result)   └──────────────────┘  │
  │  │          │                                          │
  │  │          │──── no tool calls ──────────────────────▶│ END
  │  │          │    (final answer)                        │
  │  └──────────┘                                         │
  └─────────────────────────────────────────────────────────┘
```

**Step-by-step:**

1. **Think** — The LLM receives the system prompt + full conversation history. It decides whether to call a tool or produce a final answer. If it needs a tool, it emits an `AIMessage` with `tool_calls=[{name, args}]`.

2. **Act** — LangGraph's built-in `ToolExecutor` calls the matching `StructuredTool` and emits a `ToolMessage(content=result)` back into the message list.

3. **Observe** — The LLM sees the `ToolMessage` and repeats step 1: it may chain another tool call or decide it has enough information to answer.

4. **Repeat** — The loop continues until the LLM returns an `AIMessage` with no `tool_calls` (the final answer), or `MAX_AGENT_ITERATIONS` is reached.

5. **Persist** — The entire message list is checkpointed in Redis under `thread_id = conversation_id` by `AsyncRedisSaver`. The next request with the same `conversation_id` resumes from this state, giving the agent full context of prior turns.

**Trace extraction** (`app/agent/trace.py`):

After `ainvoke()` returns, `parse_messages_to_trace()` walks every message in order and emits a `TraceStep` for each event:

| Message type | TraceStep type |
|---|---|
| `AIMessage` with `tool_calls` | `reasoning` (thought) + `tool_call` (per tool) |
| `ToolMessage` | `tool_result` |
| `AIMessage` without `tool_calls` | `final_answer` |

The resulting trace is stored as JSON in PostgreSQL alongside the task record and returned in every API response.

---

## Project Structure

```
multi-tool-agent/
├── app/
│   ├── main.py                        # FastAPI app + lifespan startup + OTel init
│   ├── core/
│   │   ├── config.py                  # Settings via pydantic-settings + .env
│   │   ├── logging.py                 # structlog JSON structured logging
│   │   └── telemetry.py               # OTel TracerProvider + OTLP exporter setup
│   ├── api/
│   │   ├── dependencies.py            # get_db, get_agent, get_task_service
│   │   └── routers/
│   │       ├── tasks.py               # POST /api/v1/task, GET /api/v1/tasks/{id}
│   │       └── health.py              # GET /health
│   ├── agent/
│   │   ├── builder.py                 # create_react_agent + AsyncRedisSaver
│   │   ├── runner.py                  # run_agent() — invoke_agent span + ainvoke
│   │   ├── otel_callback.py           # LangChain callback → chat / execute_tool spans
│   │   ├── trace.py                   # TraceStep + parse_messages_to_trace()
│   │   └── prompts.py                 # System prompt
│   ├── tools/
│   │   ├── base.py                    # BaseTool + ToolFactory ABCs
│   │   ├── registry.py                # ToolRegistry — self-registration dict
│   │   ├── calculator/
│   │   ├── weather/
│   │   ├── web_search/
│   │   ├── unit_converter/
│   │   └── database_query/
│   ├── services/task_service.py       # task lifecycle orchestration
│   ├── repositories/task_repository.py
│   ├── models/task.py                 # SQLAlchemy ORM model
│   ├── schemas/task.py                # Pydantic request/response schemas
│   └── db/session.py                  # async engine + init_db()
├── tests/
│   ├── conftest.py                    # Shared client fixture (async HTTP to localhost:8000)
│   ├── test_unit.py                   # Tools, OTel callback, run_agent span (no HTTP/DB)
│   └── test_integration.py            # HTTP endpoints, task lifecycle (requires running stack)
├── frontend/
│   └── index.html                     # single-page UI
├── data/
│   └── northwind.db                   # seed data for product catalog (read-only mount)
├── .env.example
├── pyproject.toml                     # uv project + dependencies
├── Dockerfile                         # multi-stage build
└── docker-compose.yml                 # postgres, redis, agent, jaeger, nginx
```

---

## Configuration

All configuration via environment variables or `.env` file.

| Variable | Default | Description |
|---|---|---|
| `OPENAI_API_KEY` | — | **Required.** Your OpenAI API key. |
| `OPENAI_MODEL` | `gpt-5.4` | OpenAI model to use. |
| `MAX_AGENT_ITERATIONS` | `10` | Max ReAct loop iterations before stopping. |
| `DATABASE_URL` | `postgresql+asyncpg://agent:secret@localhost:5432/agentdb` | PostgreSQL connection string for the tasks DB. Overridden to `postgres` host in docker-compose. |
| `REDIS_URL` | `redis://localhost:6379` | Redis connection for LangGraph conversation checkpoints. |
| `OTLP_ENDPOINT` | `http://jaeger:4318` | OTLP HTTP endpoint for trace export. |
| `LOG_LEVEL` | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`). |
| `DEBUG` | `false` | Enables SQLAlchemy query echo when `true`. |

---

## Adding a New Tool

1. Create `app/tools/my_tool/tool.py` — implement `BaseTool` (`name`, `description`, `args_schema`, `execute`)
2. Create `app/tools/my_tool/factory.py` — implement `ToolFactory`, end with `ToolRegistry.register(MyToolFactory())`
3. Add one import in `app/main.py` lifespan: `importlib.import_module("app.tools.my_tool.factory")`

No other files need to change.
