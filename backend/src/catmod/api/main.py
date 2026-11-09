"""ASGI entrypoint: ``uvicorn catmod.api.main:app``."""

from catmod.api.app import create_app

app = create_app()
