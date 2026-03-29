import httpx
from pydantic import BaseModel, Field
from app.tools.base import BaseTool


class WeatherInput(BaseModel):
    city: str = Field(..., description="City name to get weather for, e.g. 'London' or 'Tokyo'")


class WeatherTool(BaseTool):
    @property
    def name(self) -> str:
        return "weather"

    @property
    def description(self) -> str:
        return (
            "Gets current weather for a city. "
            "Returns temperature in Celsius, wind speed, and weather condition. "
            "No API key required."
        )

    @property
    def args_schema(self) -> type[BaseModel]:
        return WeatherInput

    async def execute(self, city: str) -> str:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"https://wttr.in/{city}",
                    params={"format": "j1"},
                    headers={"Accept": "application/json"},
                )
                resp.raise_for_status()
                data = resp.json()

                condition = data["current_condition"][0]
                temp_c = condition["temp_C"]
                wind_kmh = condition["windspeedKmph"]
                description = condition["weatherDesc"][0]["value"]

                return (
                    f"Weather in {city}: "
                    f"{temp_c}°C, {description}, Wind: {wind_kmh} km/h"
                )
        except httpx.HTTPError as e:
            return f"Error fetching weather: {type(e).__name__}: {e}"
        except (KeyError, IndexError) as e:
            return f"Unexpected response format: {e}"
        except Exception as e:
            return f"Unexpected error: {type(e).__name__}: {e}"
