# Architecture — Multi-Tool AI Agent

## Table of Contents

1. [System Overview](#1-system-overview)
2. [High-Level Architecture](#2-high-level-architecture)
3. [Request Lifecycle](#3-request-lifecycle)
4. [Agent Architecture — LangGraph ReAct](#4-agent-architecture--langgraph-react)
5. [Tool System — Abstract Factory Pattern](#5-tool-system--abstract-factory-pattern)
6. [Data Architecture](#6-data-architecture)
7. [Application Layers](#7-application-layers)
8. [Container & Deployment Architecture](#8-container--deployment-architecture)
9. [Multi-Turn Conversation Memory](#9-multi-turn-conversation-memory)
10. [Observability Design](#10-observability-design)

---

## 1. System Overview

The Multi-Tool AI Agent is a REST API that wraps a **LangGraph ReAct agent** powered by GPT-5.4. A client sends a natural-language task; the agent reasons over it, calls one or more tools in a loop, and returns a structured response that includes the final answer, a full reasoning trace, token usage, and latency.

**Core design goals:**

| Goal | How it is achieved |
|---|---|
| Extensible tools | Abstract Factory — adding a tool touches zero existing code |
| Full observability | Every task persisted to PostgreSQL with trace + tokens + latency |
| Multi-turn memory | LangGraph Redis checkpointer (`langgraph-checkpoint-redis`), keyed by `conversation_id` |
| No-key external APIs | wttr.in (weather), DuckDuckGo (search) |
| Container-ready | Multi-stage Docker build, non-root user, PostgreSQL + Redis sidecars |

---

## 2. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                          CLIENT LAYER                               │
│                                                                     │
│   Browser (frontend/index.html)   or   curl / any HTTP client       │
└────────────────────────────┬────────────────────────────────────────┘
                             │  HTTP  (port 8000)
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        FASTAPI APPLICATION                          │
│                                                                     │
│   GET  /              → serves frontend/index.html                  │
│   GET  /health        → ToolRegistry.count() + tool names           │
│   POST /api/v1/task   → TaskService.create_and_run()                │
│   GET  /api/v1/tasks/{id} → TaskService.get_task()                  │
│                                                                     │
│   Lifespan startup:                                                 │
│     1. setup_telemetry()  — OTel TracerProvider + OTLP exporter     │
│     2. configure_logging() — structlog JSON output                  │
│     3. init_db()          — creates tasks table in PostgreSQL       │
│     4. seed_catalog_db()  — seeds products + orders into PostgreSQL │
│     5. import factories   — triggers tool self-registration         │
│     6. build_agent()      — compiles LangGraph graph                │
└────────────┬──────────────────────────────────┬────────────────────┘
             │                                  │
             ▼                                  ▼
┌────────────────────────┐          ┌───────────────────────┐
│     TASK SERVICE       │          │    TOOL REGISTRY      │
│                        │          │                       │
│  1. Create task row    │          │  calculator           │
│  2. status = running   │          │  weather              │
│  3. run_agent()   ─────┼──┐       │  web_search           │
│  4. status = completed │  │       │  unit_converter       │
│  5. Persist trace      │  │       │  database_query       │
└────────────────────────┘  │       └───────────────────────┘
                             │                  │
                             ▼                  │ StructuredTools
             ┌───────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    LANGGRAPH REACT AGENT                            │
│                                                                     │
│   StateGraph compiled by create_react_agent()                       │
│                                                                     │
│   ┌─────────────┐     tool_calls      ┌──────────────────────┐     │
│   │  LLM Node   │ ─────────────────►  │  ToolExecutor Node   │     │
│   │  (GPT-5.4)   │ ◄─────────────────  │  (runs StructuredTool│     │
│   └─────────────┘    ToolMessages     └──────────────────────┘     │
│         │                                                           │
│         │ (no tool_calls → done)                                    │
│         ▼                                                           │
│   Final AIMessage  →  runner.py extracts answer + trace             │
│                                                                     │
│   Checkpointer: AsyncRedisSaver → Redis (langgraph-checkpoint-redis)│
└─────────────────────────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        EXTERNAL SERVICES                            │
│                                                                     │
│   OpenAI API (gpt-5.4)     — LLM inference                          │
│   wttr.in API             — weather (no key)                        │
│   DuckDuckGo Search       — web search (no key)                     │
└─────────────────────────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────────────┐
│                          DATA LAYER                                 │
│                                                                     │
│   PostgreSQL (agentdb) — tasks table + products + orders            │
│   Redis               — LangGraph conversation state (checkpoints)  │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 3. Request Lifecycle

A single `POST /api/v1/task` request travels through every layer:

```
Client
  │
  │  POST /api/v1/task
  │  { "task": "Weather in Tokyo and convert 25°C to Fahrenheit",
  │    "conversation_id": null }
  │
  ▼
[1] FastAPI router (api/routers/tasks.py)
    Validates request with Pydantic TaskCreateRequest
    Injects TaskService via dependency
  │
  ▼
[2] TaskService.create_and_run()
    ├─ Generates conversation_id = UUID4 (if none provided)
    ├─ INSERT tasks row  (status=pending)
    └─ UPDATE status=running
  │
  ▼
[3] run_agent(agent, task, conversation_id)   ← runner.py
    ├─ Wraps task in HumanMessage
    ├─ config = {"configurable": {"thread_id": conversation_id}}
    └─ await agent.ainvoke(input, config)  ← hands off to LangGraph
  │
  ▼
[4] LangGraph StateGraph loop
    ┌─────────────────────────────────────────┐
    │  Iteration 1                            │
    │  LLM sees: system prompt + HumanMessage │
    │  LLM emits: AIMessage(tool_calls=[      │
    │    {name:"weather", args:{city:"Tokyo"}}│
    │  ])                                     │
    │  ToolExecutor runs WeatherTool.execute()│
    │  → ToolMessage("25°C, Clear sky...")    │
    │                                         │
    │  Iteration 2                            │
    │  LLM sees: full history so far          │
    │  LLM emits: AIMessage(tool_calls=[      │
    │    {name:"unit_converter",              │
    │     args:{value:25,from:"celsius",      │
    │           to:"fahrenheit"}}             │
    │  ])                                     │
    │  ToolExecutor runs UnitConverterTool    │
    │  → ToolMessage("25 celsius = 77.00 f") │
    │                                         │
    │  Iteration 3                            │
    │  LLM emits: AIMessage(content="Tokyo   │
    │  is 25°C (77°F), clear sky.", calls=[])│
    │  → STOP (no tool_calls)                 │
    └─────────────────────────────────────────┘
  │
  ▼
[5] parse_messages_to_trace(messages)   ← trace.py
    Walks the messages list:
    ├─ AIMessage with tool_calls  → TraceStep(type="tool_call")
    ├─ ToolMessage                → TraceStep(type="tool_result")
    └─ Final AIMessage            → TraceStep(type="final_answer")
    Sums usage_metadata across AIMessages → total_tokens
  │
  ▼
[6] TaskService persists result
    UPDATE tasks SET
      status = "completed",
      answer = "...",
      trace_json = "[...]",
      total_tokens = 742,
      total_latency_ms = 3241.7,
      completed_at = now()
  │
  ▼
[7] TaskResponse returned to client
    {
      "task_id": "...",
      "status": "completed",
      "answer": "Tokyo: 25°C (77°F), clear sky.",
      "trace": [...],
      "total_tokens": 742,
      "total_latency_ms": 3241.7,
      "conversation_id": "abc-123"
    }
```

---

## 4. Agent Architecture — LangGraph ReAct

### What is ReAct?

ReAct (Reason + Act) is a prompting pattern where the LLM alternates between:
- **Reasoning** — thinking about what to do next
- **Acting** — calling a tool
- **Observing** — reading the tool result

LangGraph's `create_react_agent` compiles this into a `StateGraph` with two nodes:

```
           ┌──────────────────────────────────────┐
           │         StateGraph (compiled)         │
           │                                       │
  input ──►│  ┌──────────┐    tool_calls   ┌────────────────┐  │
           │  │  agent   │ ──────────────► │ tools          │  │
           │  │  (LLM)   │ ◄────────────── │ (ToolExecutor) │  │
           │  └──────────┘   ToolMessages  └────────────────┘  │
           │       │                                            │
           │       │ no tool_calls                              │
           │       ▼                                            │
           │    END  →  return final state                      │
           └──────────────────────────────────────┘
```

### State

LangGraph maintains a `MessagesState` — a list of all messages in the conversation thread. Each `ainvoke()` call appends to this list. The `AsyncRedisSaver` checkpointer (`langgraph-checkpoint-redis`) serializes and restores the list between calls using the `thread_id`.

### Message types in the state

| Type | Emitted by | Content |
|---|---|---|
| `HumanMessage` | Our `runner.py` | The user's task text |
| `AIMessage` (with tool_calls) | GPT-5.4 | Reasoning + which tools to call |
| `ToolMessage` | LangGraph ToolExecutor | The tool's string return value |
| `AIMessage` (no tool_calls) | GPT-5.4 | Final answer — loop terminates |

### Token flow

Each `AIMessage` carries `usage_metadata`:
```python
{
  "input_tokens": 412,   # tokens in the prompt sent to GPT-5.4
  "output_tokens": 87,   # tokens in GPT-5.4's response
}
```
`parse_messages_to_trace()` accumulates these separately across all `AIMessage`s in the thread to produce `total_tokens_in` and `total_tokens_out`.

---

## 5. Tool System — Abstract Factory Pattern

### Class hierarchy

```
BaseTool  (ABC)                    ToolFactory  (ABC)
  ├── name: str                      ├── tool_name: str
  ├── description: str               └── create_tool() → BaseTool
  ├── args_schema: type[BaseModel]
  ├── execute(**kwargs) → str
  └── to_langchain_tool() → StructuredTool

Concrete tools:                    Concrete factories:
  CalculatorTool                     CalculatorFactory
  WeatherTool                        WeatherFactory
  WebSearchTool                      WebSearchFactory
  UnitConverterTool                  UnitConverterFactory
  DatabaseQueryTool                  DatabaseQueryFactory
```

### Self-registration pattern

Each `factory.py` ends with one line that runs at import time:

```python
ToolRegistry.register(CalculatorFactory())
```

`main.py` lifespan imports all factory modules → each registers itself → `ToolRegistry` is populated before the agent is built. No central list of tools exists anywhere. **Adding a new tool requires zero changes to existing code.**

```
Import time (lifespan startup):

  import calculator.factory  ──► CalculatorFactory() ──► ToolRegistry._instances["calculator"]
  import weather.factory     ──► WeatherFactory()    ──► ToolRegistry._instances["weather"]
  import web_search.factory  ──► ...
  import unit_converter.factory
  import database_query.factory

  ToolRegistry.all_langchain_tools()
    └─► [tool.to_langchain_tool() for tool in _instances.values()]
        └─► List[StructuredTool]  passed to create_react_agent()
```

### Tool implementations

| Tool | Library | External call | Key detail |
|---|---|---|---|
| `calculator` | `simpleeval` | None | `EvalWithCompoundTypes` — no raw `eval()`, AST-safe |
| `weather` | `httpx` | wttr.in JSON API | Single HTTP call — city name accepted directly, no geocoding step |
| `web_search` | `duckduckgo-search` | DuckDuckGo | Sync library wrapped in `asyncio.run_in_executor` |
| `unit_converter` | Pure Python | None | Lookup table normalised to SI base units; temperature is a formula special case |
| `database_query` | `SQLAlchemy/asyncpg` | None | Read-only SELECT enforced; keyword blocklist blocks DROP/DELETE/INSERT/UPDATE etc. |

---

## 6. Data Architecture

### Two backing stores

```
PostgreSQL (agentdb)   — tasks table + products + orders (via SQLAlchemy async)
Redis                  — LangGraph conversation checkpoints (via langgraph-checkpoint-redis)
```

#### PostgreSQL — tasks table

Purpose: **observability log**. Every task ever run is recorded here. Managed by SQLAlchemy async (`asyncpg` driver).

```
tasks
┌──────────────────┬──────────────┬──────────────────────────────────────┐
│ Column           │ Type         │ Notes                                │
├──────────────────┼──────────────┼──────────────────────────────────────┤
│ id               │ String(36)   │ UUID4 primary key                    │
│ task_input       │ Text         │ Original user message                │
│ status           │ String(20)   │ pending → running → completed/failed │
│ answer           │ Text         │ Final LLM answer                     │
│ trace_json       │ Text         │ JSON array of TraceStep objects      │
│ error_message    │ Text         │ Set only on failure                  │
│ total_tokens_in  │ Integer      │ Sum of input_tokens across all AIMessages  │
│ total_tokens_out │ Integer      │ Sum of output_tokens across all AIMessages │
│ total_latency_ms │ Float        │ Wall time of ainvoke() call                │
│ conversation_id  │ String(36)   │ = LangGraph thread_id (indexed)      │
│ created_at       │ DateTime     │ Row creation time                    │
│ completed_at     │ DateTime     │ Set when status reaches terminal     │
└──────────────────┴──────────────┴──────────────────────────────────────┘
```

#### PostgreSQL — products + orders

Purpose: **demo data** for the `database_query` tool. Seeded at startup from `northwind.db` (SQLite source file, read once); all subsequent queries go to PostgreSQL.

```
products                           orders
┌────┬──────────────────┬──────┐   ┌────┬────────────┬──────────┬───────────────┐
│ id │ name             │price │   │ id │ product_id │ quantity │ customer_name │
│    │ category         │stock │   │    │ order_date │          │ total_price   │
└────┴──────────────────┴──────┘   └────┴────────────┴──────────┴───────────────┘
  ~77 rows, 8 Northwind categories    up to 500 rows, seeded on startup if empty
  (Beverages, Condiments, Seafood…)
```

#### Redis — LangGraph checkpoints

Purpose: **conversation memory**. Managed entirely by `langgraph-checkpoint-redis` (`AsyncRedisSaver`). Stores serialized `MessagesState` snapshots keyed by `thread_id`. No application schema — LangGraph internals manage all keys.

---

## 7. Application Layers

```
┌─────────────────────────────────────────────┐
│  API Layer  (app/api/)                       │
│  FastAPI routers, Pydantic validation,        │
│  dependency injection                        │
└──────────────────┬──────────────────────────┘
                   │
┌──────────────────▼──────────────────────────┐
│  Service Layer  (app/services/)              │
│  Orchestrates task lifecycle:                │
│  create → run → persist → respond           │
└──────────┬───────────────────┬──────────────┘
           │                   │
┌──────────▼──────┐   ┌────────▼─────────────┐
│  Repository     │   │  Agent Layer          │
│  (app/repos/)   │   │  (app/agent/)         │
│  SQLAlchemy     │   │  builder + runner +   │
│  CRUD on tasks  │   │  trace parser         │
└──────────┬──────┘   └────────┬─────────────┘
           │                   │
┌──────────▼───────────────────▼──────────────┐
│  Infrastructure Layer                        │
│  app/db/session.py  — async engine/session   │
│  app/core/config.py — pydantic-settings      │
│  app/core/logging.py — structlog JSON        │
└─────────────────────────────────────────────┘
```

### Dependency injection flow

```
Request
  │
  ▼
get_db()          → yields AsyncSession (one per request)
get_agent()       → reads app.state.agent (singleton, set at startup)
get_task_service() → constructs TaskService(db=..., agent=...)
  │
  ▼
Router handler receives TaskService, calls methods
```

---

## 8. Container & Deployment Architecture

### Multi-stage Dockerfile

```
┌─────────────────────────────────┐
│         Stage 1: builder        │
│  python:3.12-slim               │
│                                 │
│  pip install uv                 │
│  COPY pyproject.toml uv.lock    │
│  uv sync --no-install-project   │
│    → .venv/  (all deps)         │
└──────────────┬──────────────────┘
               │  COPY --from=builder /app/.venv
               ▼
┌─────────────────────────────────┐
│         Stage 2: runtime        │
│  python:3.12-slim               │
│                                 │
│  New non-root user: appuser     │
│  PATH includes .venv/bin        │
│  COPY app/ frontend/            │
│  USER appuser                   │
│  EXPOSE 8000                    │
│  CMD uvicorn ... --workers 1    │
└─────────────────────────────────┘
```

**Why `--workers 1`?** A single uvicorn worker keeps the asyncpg connection pool simple and avoids any risk of Redis connection storms at startup. PostgreSQL handles concurrent writes natively, but one worker is sufficient for this workload.

**Why multi-stage?** The builder stage installs build tools and `uv`. The runtime stage only copies the compiled `.venv`, keeping the final image small and free of build tooling.

### docker-compose services

```yaml
services:
  postgres:                          # tasks table + catalog data
    image: postgres:16-alpine
    volumes:
      - postgres_data:/var/lib/postgresql/data     # named volume, persists across restarts

  redis:                             # LangGraph conversation checkpoints
    image: redis/redis-stack-server:latest

  agent:
    build: .                         # builds from Dockerfile in repo root
    environment:                     # env vars from host .env
      - OPENAI_API_KEY
      - OPENAI_MODEL (default: gpt-5.4)
      - DATABASE_URL=postgresql+asyncpg://agent:secret@postgres:5432/agentdb
      - REDIS_URL=redis://redis:6379
      - LOG_LEVEL
    volumes:
      - ./data/northwind.db:/app/northwind.db:ro   # seed source, read-only
    depends_on:
      postgres: { condition: service_healthy }
      redis:    { condition: service_healthy }
      jaeger:   { condition: service_started }
    healthcheck:
      GET /health every 30s          # container marked unhealthy if 3 checks fail
    restart: unless-stopped

  jaeger:                            # distributed tracing backend
    image: jaegertracing/all-in-one:latest
    environment:
      - COLLECTOR_OTLP_ENABLED=true
    ports:
      - "16686:16686"                # Jaeger UI
      - "4318:4318"                  # OTLP HTTP receiver (traces from agent)
    restart: unless-stopped

  frontend:
    image: nginx:alpine              # serves static UI, proxies /api to agent
    ports:
      - "80:80"
```

### Persistence

```
Host filesystem              Container / service
postgres_data (volume)  ↔    PostgreSQL data directory (tasks + catalog)
Redis in-memory         ↔    Conversation checkpoints (lost on redis restart)
./data/northwind.db     →    /app/northwind.db  (seed source, read-only mount)
```

PostgreSQL state (tasks and catalog) survives `docker-compose down` and `docker-compose up` without data loss. Redis checkpoints are in-memory by default; conversations are lost on Redis restart.

### Networking

```
Internet / LAN
      │
      │ :80  (HTTP — frontend/nginx)
      │ :16686  (Jaeger UI)
      ▼
Docker host (port mappings)
      │
      │ :80 → frontend container (nginx)
      │         proxies /api → agent:8000 (internal)
      │ :16686 → jaeger container (UI)
      ▼
  agent container (port 8000 — internal only, not published)
      │
      │ HTTPS :443 (outbound only)
      ▼
  OpenAI API
  wttr.in API
  DuckDuckGo
```

Inbound public ports: 80 (nginx/frontend) and 16686 (Jaeger UI). Port 8000 is internal to the Docker network — the agent is only reachable via the nginx proxy. All LLM/API calls are outbound HTTPS from within the container.

---

## 9. Multi-Turn Conversation Memory

LangGraph's `AsyncRedisSaver` (`langgraph-checkpoint-redis`) stores the full `MessagesState` snapshot after every `ainvoke()` call, keyed by `thread_id`.

```
Turn 1  (conversation_id = "abc-123")
  ┌─────────────────────────────────┐
  │ thread: abc-123                 │
  │ messages: [                     │
  │   HumanMessage("Capital of France?"),  │
  │   AIMessage("Paris.")           │
  │ ]                               │
  └─────────────────────────────────┘
  → Redis checkpoint updated

Turn 2  (conversation_id = "abc-123")
  ainvoke called with thread_id="abc-123"
  LangGraph loads checkpoint → restores messages list
  Appends new HumanMessage("Weather there?")
  ┌─────────────────────────────────┐
  │ thread: abc-123                 │
  │ messages: [                     │
  │   HumanMessage("Capital of France?"),  │
  │   AIMessage("Paris."),          │
  │   HumanMessage("Weather there?"),      │
  │   AIMessage(tool_calls=[weather(city="Paris")]), │
  │   ToolMessage("15°C, Partly cloudy"),  │
  │   AIMessage("Paris is currently 15°C and partly cloudy.") │
  │ ]                               │
  └─────────────────────────────────┘
```

GPT-5.4 sees the full history transparently — no manual message reconstruction needed in application code.

**New conversation** = omit `conversation_id` in the request (or send `null`). The service generates a fresh UUID4 → LangGraph starts a new thread with an empty state.

---

## 10. Observability Design

Every task produces a structured `trace` that records each step of the agent's reasoning:

```
trace: [
  { step: 1, type: "tool_call",    tool_name: "weather",   tool_input: {city: "Tokyo"} },
  { step: 2, type: "tool_result",  tool_name: "weather",   content: "25°C, Clear sky" },
  { step: 3, type: "tool_call",    tool_name: "unit_converter", tool_input: {value:25,...} },
  { step: 4, type: "tool_result",  tool_name: "unit_converter", content: "25°C = 77.00°F" },
  { step: 5, type: "final_answer", content: "Tokyo is 25°C (77°F), clear sky." }
]
```

**Step types:**

| Type | Source | When emitted |
|---|---|---|
| `reasoning` | `AIMessage` with content + tool_calls | LLM narrates its thinking before calling a tool |
| `tool_call` | `AIMessage.tool_calls[]` | LLM decides to invoke a tool |
| `tool_result` | `ToolMessage` | Tool returns its result |
| `final_answer` | `AIMessage` with no tool_calls | LLM produces the final response |

The trace is serialized as JSON and stored in `tasks.trace_json`, making it queryable and replayable. The frontend renders each step colour-coded by type.

**Per-task metrics stored in PostgreSQL (`agentdb.tasks`):**

```
total_tokens_in  = sum of input_tokens across all AIMessages
total_tokens_out = sum of output_tokens across all AIMessages
total_latency_ms = wall-clock time of agent.ainvoke() call
status           = pending → running → completed | failed
error_message    = exception string if failed
```
