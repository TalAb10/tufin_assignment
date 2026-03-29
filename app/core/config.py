from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    openai_api_key: str
    openai_model: str = "gpt-5.4"
    max_agent_iterations: int = 10
    database_url: str = "postgresql+asyncpg://agent:secret@localhost:5432/agentdb"
    redis_url: str = "redis://localhost:6379"
    log_level: str = "INFO"
    debug: bool = False
    otlp_endpoint: str = "http://jaeger:4318"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
