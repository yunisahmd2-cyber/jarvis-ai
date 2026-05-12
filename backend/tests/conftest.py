from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    db_path = tmp_path / "jarvis-test.db"
    audio_dir = tmp_path / "audio"
    workspace_dir = tmp_path / "workspace"
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    os.environ["AUDIO_DIR"] = str(audio_dir)
    os.environ["WORKSPACE_DIR"] = str(workspace_dir)
    os.environ["FRONTEND_BACKEND_URL"] = "http://127.0.0.1:8000"
    os.environ["LLM_PRIMARY_PROVIDER"] = "ollama"
    os.environ["OLLAMA_MODEL"] = "llama3.1:8b"
    os.environ.pop("MISTRAL_API_KEY", None)

    from backend.app.core.config import reset_settings_cache

    reset_settings_cache()

    main = importlib.import_module("backend.app.main")
    main = importlib.reload(main)
    with TestClient(main.app) as test_client:
        yield test_client
