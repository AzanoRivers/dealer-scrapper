import os
import shutil
import tempfile
from typing import Generator

import pytest
from fastapi.testclient import TestClient

# Override settings BEFORE importing the app
TEST_API_KEY = "test-key-12345678901234567890123456"

# Create a temp dir for tests
_TEST_TMP_DIR = tempfile.mkdtemp(prefix="dealerscrapper_test_")


def pytest_configure(config: pytest.Config) -> None:
    """Set env vars before any module is imported."""
    os.environ["API_KEY"] = TEST_API_KEY
    os.environ["JOB_BASE_DIR"] = _TEST_TMP_DIR
    os.environ["DOWNLOAD_IMAGES"] = "false"
    os.environ["RESULT_TTL_MINUTES"] = "15"
    os.environ["JOB_MAX_DURATION_SECONDS"] = "1800"
    # Disable real pipeline execution during integration tests (POST /scrape)
    # to prevent background pipeline tasks from interfering with other tests.
    # F06+ unit tests call run_pipeline() directly and are unaffected by this.
    os.environ["ENABLE_PIPELINE"] = "0"


def pytest_unconfigure(config: pytest.Config) -> None:
    """Clean up temp dir after all tests."""
    shutil.rmtree(_TEST_TMP_DIR, ignore_errors=True)


@pytest.fixture(scope="session")
def client() -> Generator[TestClient, None, None]:
    from app.main import app
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


@pytest.fixture
def api_headers() -> dict[str, str]:
    return {"X-API-Key": TEST_API_KEY}


@pytest.fixture
def tmp_job_dir() -> Generator[str, None, None]:
    """Provides a fresh temp directory per test."""
    d = tempfile.mkdtemp(dir=_TEST_TMP_DIR, prefix="job_")
    yield d
    shutil.rmtree(d, ignore_errors=True)
