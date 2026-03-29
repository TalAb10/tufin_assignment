import pytest
import pytest_asyncio
import httpx


@pytest_asyncio.fixture
async def client():
    async with httpx.AsyncClient(base_url="http://localhost:8000", timeout=60.0) as c:
        yield c
