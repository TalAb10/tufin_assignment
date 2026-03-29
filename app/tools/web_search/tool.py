import asyncio
from pydantic import BaseModel, Field
from app.tools.base import BaseTool


class WebSearchInput(BaseModel):
    query: str = Field(..., description="Search query to look up on the web")
    max_results: int = Field(5, description="Maximum number of results to return", ge=1, le=10)


class WebSearchTool(BaseTool):
    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return (
            "Searches the web using DuckDuckGo. "
            "Returns titles and snippets of top results. "
            "Use for current events, recent information, or factual lookups."
        )

    @property
    def args_schema(self) -> type[BaseModel]:
        return WebSearchInput

    async def execute(self, query: str, max_results: int = 5) -> str:
        try:
            loop = asyncio.get_running_loop()
            results = await loop.run_in_executor(None, self._search_sync, query, max_results)
            return results
        except Exception as e:
            return f"Search error: {e}"

    def _search_sync(self, query: str, max_results: int) -> str:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))

        if not results:
            return f"No results found for '{query}'."

        formatted = []
        for i, r in enumerate(results, 1):
            title = r.get("title", "No title")
            body = r.get("body", "No description")
            href = r.get("href", "")
            formatted.append(f"{i}. {title}\n   {body}\n   URL: {href}")

        return f"Search results for '{query}':\n\n" + "\n\n".join(formatted)
