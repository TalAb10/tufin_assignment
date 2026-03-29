from pydantic import BaseModel, Field
from app.tools.base import BaseTool


class UnitConverterInput(BaseModel):
    value: float = Field(..., description="Numeric value to convert")
    from_unit: str = Field(..., description="Source unit (e.g. 'km', 'celsius', 'kg', 'mph')")
    to_unit: str = Field(..., description="Target unit (e.g. 'miles', 'fahrenheit', 'lb', 'kmh')")


# Conversion table — all relative to a base SI unit
CONVERSIONS: dict[str, dict[str, float]] = {
    # Length (base: meters)
    "m": 1.0, "km": 1000.0, "cm": 0.01, "mm": 0.001,
    "miles": 1609.344, "mile": 1609.344,
    "yards": 0.9144, "yard": 0.9144,
    "feet": 0.3048, "foot": 0.3048, "ft": 0.3048,
    "inches": 0.0254, "inch": 0.0254, "in": 0.0254,
    # Mass (base: kg)
    "kg": 1.0, "g": 0.001, "lb": 0.453592, "lbs": 0.453592,
    "oz": 0.0283495, "t": 1000.0,
    # Speed (base: m/s)
    "m/s": 1.0, "kmh": 1/3.6, "km/h": 1/3.6,
    "mph": 0.44704, "knots": 0.514444,
    # Volume (base: liters)
    "l": 1.0, "liter": 1.0, "liters": 1.0,
    "ml": 0.001, "gallon": 3.78541, "gallons": 3.78541,
    "fl oz": 0.0295735,
    # Data (base: bytes)
    "bytes": 1, "kb": 1024, "mb": 1024**2, "gb": 1024**3, "tb": 1024**4,
}

TEMPERATURE_UNITS = {"celsius", "fahrenheit", "kelvin", "c", "f", "k"}


class UnitConverterTool(BaseTool):
    @property
    def name(self) -> str:
        return "unit_converter"

    @property
    def description(self) -> str:
        return (
            "Converts values between units. Supports length, mass, speed, volume, "
            "data sizes, and temperature. Example: 100 km to miles, 0 celsius to fahrenheit."
        )

    @property
    def args_schema(self) -> type[BaseModel]:
        return UnitConverterInput

    async def execute(self, value: float, from_unit: str, to_unit: str) -> str:
        from_unit_lower = from_unit.lower().strip()
        to_unit_lower = to_unit.lower().strip()

        # Temperature special case
        if from_unit_lower in TEMPERATURE_UNITS or to_unit_lower in TEMPERATURE_UNITS:
            return self._convert_temperature(value, from_unit_lower, to_unit_lower)

        if from_unit_lower not in CONVERSIONS:
            return f"Unknown unit: '{from_unit}'. Supported: {', '.join(sorted(CONVERSIONS.keys()))}"
        if to_unit_lower not in CONVERSIONS:
            return f"Unknown unit: '{to_unit}'. Supported: {', '.join(sorted(CONVERSIONS.keys()))}"

        # Convert via base unit
        base_value = value * CONVERSIONS[from_unit_lower]
        result = base_value / CONVERSIONS[to_unit_lower]
        return f"{value} {from_unit} = {result:.4f} {to_unit}"

    def _convert_temperature(self, value: float, from_unit: str, to_unit: str) -> str:
        # Normalize
        from_u = from_unit[0] if len(from_unit) > 1 else from_unit
        to_u = to_unit[0] if len(to_unit) > 1 else to_unit

        # Convert to Celsius first
        if from_u == "c":
            celsius = value
        elif from_u == "f":
            celsius = (value - 32) * 5 / 9
        elif from_u == "k":
            celsius = value - 273.15
        else:
            return f"Unknown temperature unit: '{from_unit}'"

        # Convert from Celsius to target
        if to_u == "c":
            result = celsius
        elif to_u == "f":
            result = celsius * 9 / 5 + 32
        elif to_u == "k":
            result = celsius + 273.15
        else:
            return f"Unknown temperature unit: '{to_unit}'"

        return f"{value} {from_unit} = {result:.2f} {to_unit}"
