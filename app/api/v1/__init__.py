"""API v1 endpoints."""
from fastapi import APIRouter

from . import health, voice

api_router = APIRouter()

# Include all v1 routers
api_router.include_router(health.router, tags=["health"])
api_router.include_router(voice.router, tags=["voice"])
