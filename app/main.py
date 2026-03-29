from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import FileResponse
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from app.core.config import settings
from app.core.logging import configure_logging, logger
from app.core.telemetry import setup_telemetry, shutdown_telemetry
from app.db.session import init_db
from app.tools.database_query.seed import seed_catalog_db
from app.agent.builder import build_agent
from app.api.routers import health, tasks


@asynccontextmanager
async def lifespan(fastapi_app: FastAPI):
    setup_telemetry()
    configure_logging(settings.log_level)
    logger.info("startup_begin", model=settings.openai_model)

    # Init task DB
    await init_db()
    logger.info("task_db_initialized")

    # Seed catalog tables into Postgres
    await seed_catalog_db()

    # Trigger tool self-registration by importing factory modules
    # Use importlib to avoid shadowing the `fastapi_app` or `app` names
    import importlib
    importlib.import_module("app.tools.calculator.factory")
    importlib.import_module("app.tools.weather.factory")
    importlib.import_module("app.tools.web_search.factory")
    importlib.import_module("app.tools.unit_converter.factory")
    importlib.import_module("app.tools.database_query.factory")

    # Build LangGraph agent with Redis checkpointer
    from langgraph.checkpoint.redis.aio import AsyncRedisSaver
    async with AsyncRedisSaver.from_conn_string(settings.redis_url) as checkpointer:
        await checkpointer.asetup()
        fastapi_app.state.agent = await build_agent(checkpointer)
        logger.info("agent_ready")
        yield

    logger.info("shutdown")
    shutdown_telemetry()


app = FastAPI(
    title="Multi-Tool AI Agent",
    description="Production-quality AI agent with observability, multi-turn conversations, and 5 integrated tools.",
    version="1.0.0",
    lifespan=lifespan,
)

FastAPIInstrumentor.instrument_app(app)

app.include_router(health.router)
app.include_router(tasks.router)

# Serve frontend
frontend_path = Path("frontend")
if frontend_path.exists():
    @app.get("/", include_in_schema=False)
    async def serve_frontend():
        return FileResponse("frontend/index.html")
