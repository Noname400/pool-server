"""
Shared test fixtures for pool_server tests.

Requires:
  - A running Redis/KeyDB on localhost:6379 (or KEYDB_URL env)
  - pytest, pytest-asyncio, httpx
"""
import asyncio
import os
import tempfile

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-prod")
os.environ.setdefault("TRAINER_AUTH_TOKEN", "test-trainer-token")
os.environ.setdefault("EXPORT_TOKEN", "test-export-token")


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def app():
    """Create a fresh app instance with temporary SQLite DB."""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["DATA_DIR"] = tmpdir
        # Clear cached settings
        from app.config import get_settings
        get_settings.cache_clear()

        from app.main import app as _app
        from app.main import lifespan

        async with lifespan(_app):
            yield _app

        get_settings.cache_clear()


@pytest_asyncio.fixture
async def client(app):
    """Async HTTP client for testing the FastAPI app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def trainer_headers():
    """Standard trainer headers for authenticated requests."""
    return {
        "Authorization": "test-trainer-token",
        "X-Machine-Id": "test-machine-001",
        "X-Hostname": "test-host",
        "X-GPU-Name": "RTX 4090",
        "X-GPU-Count": "4",
        "X-GPU-Mem": "24576",
        "X-Version": "1.0.0",
    }
