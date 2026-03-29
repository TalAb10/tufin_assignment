from abc import ABC, abstractmethod
from pydantic import BaseModel
from langchain_core.tools import StructuredTool


class BaseTool(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    @property
    @abstractmethod
    def args_schema(self) -> type[BaseModel]: ...

    @abstractmethod
    async def execute(self, **kwargs) -> str: ...

    def to_langchain_tool(self) -> StructuredTool:
        return StructuredTool.from_function(
            coroutine=self.execute,
            name=self.name,
            description=self.description,
            args_schema=self.args_schema,
        )


class ToolFactory(ABC):
    @property
    @abstractmethod
    def tool_name(self) -> str: ...

    @abstractmethod
    def create_tool(self) -> BaseTool: ...
