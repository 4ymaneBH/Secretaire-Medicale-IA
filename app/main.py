import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.deps import get_redis_client
from app.api.router import api_router
from app.core.config import settings
from app.core.logging_config import get_logger

app = FastAPI(title="AI Voice Secretary")
logger = get_logger(__name__)


@app.middleware("http")
async def add_request_id(request: Request, call_next):
    """Attach a request ID for traceability."""
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


@app.get("/")
def read_root():
    return {"message": "AI Voice Secretary backend"}


@app.get("/health")
async def health_check():
    """Health check for load balancers / uptime monitoring."""
    from sqlalchemy import text
    from app.db.session import SessionLocal

    checks = {}
    healthy = True

    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = "healthy"
    except Exception as exc:
        logger.error(f"[HEALTH] Database check failed: {exc}")
        checks["database"] = "unhealthy"
        healthy = False

    try:
        redis_client = get_redis_client()
        if redis_client:
            redis_client.ping()
            checks["redis"] = "healthy"
        else:
            checks["redis"] = "not configured"
    except Exception as exc:
        logger.error(f"[HEALTH] Redis check failed: {exc}")
        checks["redis"] = "unhealthy"
        healthy = False

    return {"status": "ok" if healthy else "degraded", "checks": checks}


cors_origins = settings.cors_origins_list if settings.is_production else ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID", "x-webhook-signature"],
    expose_headers=["X-Request-ID"],
    max_age=3600,
)


@app.on_event("startup")
async def startup_validation():
    """Validate connectivity at startup."""
    from sqlalchemy import text
    from app.db.session import SessionLocal

    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
        logger.info("Database connection verified")
    except Exception as exc:
        logger.error(f"Database connection failed: {exc}")
        raise

    try:
        redis_client = get_redis_client()
        if redis_client:
            redis_client.ping()
            logger.info("Redis connection verified")
    except Exception as exc:
        logger.warning(f"Redis connection failed: {exc}")

    logger.info(f"Application started in {settings.app_env} mode")


@app.on_event("shutdown")
async def shutdown_event():
    """Graceful shutdown to close external connections."""
    logger.info("Shutting down application...")

    try:
        from app.db.session import engine
        await engine.dispose()
        logger.info("Database connections closed")
    except Exception as exc:
        logger.error(f"Error closing database: {exc}")

    try:
        redis_client = get_redis_client()
        if redis_client:
            redis_client.close()
    except Exception as exc:
        logger.error(f"Error closing Redis: {exc}")

    logger.info("Application shutdown complete")


app.include_router(api_router, prefix="/api")
