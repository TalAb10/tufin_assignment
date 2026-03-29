from pydantic import BaseModel, Field
from sqlalchemy import text
from app.tools.base import BaseTool
from app.db.session import engine

BLOCKED_KEYWORDS = {"drop", "delete", "insert", "update", "create", "alter", "truncate", "pragma", "attach", "detach"}


class DatabaseQueryInput(BaseModel):
    query: str = Field(
        ...,
        description=(
            "A read-only SQL SELECT query against the product catalog tables in PostgreSQL. "
            "Tables: products(id, name, category, price, stock), "
            "orders(id, product_id, quantity, customer_name, order_date, total_price). "
            "Example: SELECT * FROM products WHERE category = 'Beverages'"
        )
    )


class DatabaseQueryTool(BaseTool):
    @property
    def name(self) -> str:
        return "database_query"

    @property
    def description(self) -> str:
        return (
            "Query the product catalog in PostgreSQL. "
            "Read-only SELECT queries only. "
            "Tables: products(id, name, category, price, stock), "
            "orders(id, product_id, quantity, customer_name, order_date, total_price)."
        )

    @property
    def args_schema(self) -> type[BaseModel]:
        return DatabaseQueryInput

    async def execute(self, query: str) -> str:
        query_lower = query.lower().strip()

        if not query_lower.startswith("select"):
            return "Error: Only SELECT queries are allowed."

        for keyword in BLOCKED_KEYWORDS:
            if f" {keyword} " in f" {query_lower} " or query_lower.startswith(keyword):
                return f"Error: Query contains blocked keyword '{keyword}'."

        try:
            async with engine.connect() as conn:
                result = await conn.execute(text(query))
                rows = result.fetchmany(100)

                if not rows:
                    return "Query returned no results."

                columns = list(result.keys())
                result_lines = [" | ".join(columns)]
                result_lines.append("-" * len(result_lines[0]))
                for row in rows:
                    result_lines.append(" | ".join(str(val) for val in row))

                return "\n".join(result_lines)
        except Exception as e:
            return f"Database error: {e}"
