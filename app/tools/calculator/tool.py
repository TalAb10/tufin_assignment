from pydantic import BaseModel, Field
from simpleeval import simple_eval, EvalWithCompoundTypes
from app.tools.base import BaseTool


class CalculatorInput(BaseModel):
    expression: str = Field(..., description="Mathematical expression to evaluate, e.g. '2 + 2' or '15 * 200 / 100'")


class CalculatorTool(BaseTool):
    @property
    def name(self) -> str:
        return "calculator"

    @property
    def description(self) -> str:
        return (
            "Evaluates mathematical expressions safely. "
            "Use for arithmetic, percentages, and basic math. "
            "Example: '15 * 200 / 100' for 15% of 200."
        )

    @property
    def args_schema(self) -> type[BaseModel]:
        return CalculatorInput

    async def execute(self, expression: str) -> str:
        try:
            evaluator = EvalWithCompoundTypes()
            result = evaluator.eval(expression)
            return str(result)
        except Exception as e:
            return f"Error evaluating expression '{expression}': {e}"
