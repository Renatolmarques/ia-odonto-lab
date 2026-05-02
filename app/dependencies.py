# app/dependencies.py
"""
IA Odonto Lab — Shared FastAPI Dependencies

Kept in a dedicated module to avoid circular imports between app.main
and routers that need the same authentication dependency.
"""
import logging
import os

from fastapi import Header, HTTPException

logger = logging.getLogger(__name__)


def verify_api_key(x_api_key: str = Header(None, alias="X-API-Key")) -> str:
    """Dependency: validates the X-API-Key header on protected endpoints."""
    expected = os.getenv("LINA_API_KEY", "")
    if not expected or x_api_key != expected:
        logger.warning("Unauthorized request — invalid API key")
        raise HTTPException(status_code=401, detail="Unauthorized")
    return x_api_key
