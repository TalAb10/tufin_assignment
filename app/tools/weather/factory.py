from app.tools.base import ToolFactory, BaseTool
from app.tools.weather.tool import WeatherTool
from app.tools.registry import ToolRegistry


class WeatherFactory(ToolFactory):
    @property
    def tool_name(self) -> str:
        return "weather"

    def create_tool(self) -> BaseTool:
        return WeatherTool()


ToolRegistry.register(WeatherFactory())
