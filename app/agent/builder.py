from langgraph.prebuilt import create_react_agent
from langchain_openai import ChatOpenAI
from app.tools.registry import ToolRegistry
from app.agent.prompts import SYSTEM_PROMPT
from app.core.config import settings
from app.core.logging import logger


async def build_agent(checkpointer):
    llm = ChatOpenAI(
        model=settings.openai_model,
        api_key=settings.openai_api_key,
    )
    tools = ToolRegistry.all_langchain_tools()
    logger.info("building_agent", tool_count=len(tools), model=settings.openai_model)

    agent = create_react_agent(
        llm,
        tools,
        checkpointer=checkpointer,
        prompt=SYSTEM_PROMPT,
    )
    return agent
