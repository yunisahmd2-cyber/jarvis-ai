"""Compatibility entrypoint for the modular Jarvis backend."""

from backend.app.main import app


if __name__ == "__main__":
    import uvicorn

    from backend.app.core.config import get_settings

    settings = get_settings()
    uvicorn.run(
        "main_v7_backend:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=settings.app_env == "development",
    )
