from app.tools.base import ToolFactory, BaseTool
from app.tools.unit_converter.tool import UnitConverterTool
from app.tools.registry import ToolRegistry


class UnitConverterFactory(ToolFactory):
    @property
    def tool_name(self) -> str:
        return "unit_converter"

    def create_tool(self) -> BaseTool:
        return UnitConverterTool()


ToolRegistry.register(UnitConverterFactory())
