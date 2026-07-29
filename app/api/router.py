"""Main API router that includes all versioned routers."""
from fastapi import APIRouter

from app.api.v1 import api_router as v1_router

api_router = APIRouter()

# Include v1 API
api_router.include_router(v1_router, prefix="/v1")
