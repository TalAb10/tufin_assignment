from app.tools.base import ToolFactory, BaseTool
from app.tools.web_search.tool import WebSearchTool
from app.tools.registry import ToolRegistry


class WebSearchFactory(ToolFactory):
    @property
    def tool_name(self) -> str:
        return "web_search"

    def create_tool(self) -> BaseTool:
        return WebSearchTool()


ToolRegistry.register(WebSearchFactory())
