from langchain_core.tools import StructuredTool
from app.tools.base import BaseTool, ToolFactory
from app.core.logging import logger


class ToolRegistry:
    _instances: dict[str, BaseTool] = {}

    @classmethod
    def register(cls, factory: ToolFactory) -> None:
        tool = factory.create_tool()
        cls._instances[factory.tool_name] = tool
        logger.info("tool_registered", tool_name=factory.tool_name)

    @classmethod
    def get_tool(cls, name: str) -> BaseTool:
        if name not in cls._instances:
            raise KeyError(f"Tool '{name}' not registered")
        return cls._instances[name]

    @classmethod
    def all_langchain_tools(cls) -> list[StructuredTool]:
        return [tool.to_langchain_tool() for tool in cls._instances.values()]

    @classmethod
    def count(cls) -> int:
        return len(cls._instances)

    @classmethod
    def tool_names(cls) -> list[str]:
        return list(cls._instances.keys())
