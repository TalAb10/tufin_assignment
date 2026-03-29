from app.tools.base import ToolFactory, BaseTool
from app.tools.calculator.tool import CalculatorTool
from app.tools.registry import ToolRegistry


class CalculatorFactory(ToolFactory):
    @property
    def tool_name(self) -> str:
        return "calculator"

    def create_tool(self) -> BaseTool:
        return CalculatorTool()


ToolRegistry.register(CalculatorFactory())
