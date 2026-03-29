from app.tools.base import ToolFactory, BaseTool
from app.tools.database_query.tool import DatabaseQueryTool
from app.tools.registry import ToolRegistry


class DatabaseQueryFactory(ToolFactory):
    @property
    def tool_name(self) -> str:
        return "database_query"

    def create_tool(self) -> BaseTool:
        return DatabaseQueryTool()


ToolRegistry.register(DatabaseQueryFactory())
