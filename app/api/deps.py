import os
import hmac
import hashlib
from typing import Optional

from fastapi import Header, HTTPException, Request
from twilio.request_validator import RequestValidator
import redis

from app.db.session import get_db  # Expose DB dependency for routes
from app.core.config import settings


def get_redis_client() -> Optional[redis.Redis]:
	url = getattr(settings, "redis_url", None) or os.getenv("REDIS_URL")
	if not url:
		return None
	try:
		return redis.from_url(url)
	except Exception:
		return None


async def verify_twilio_signature(
	request: Request,
	x_twilio_signature: str | None = Header(default=None),
) -> None:
	"""Verify Twilio request signature for webhooks.

	Requires `TWILIO_AUTH_TOKEN` in environment. Uses full URL & body params.
	"""
	# Allow temporary skip in local testing (e.g., when using ngrok/debugging)
	if os.getenv("SKIP_TWILIO_SIGNATURE_VERIFICATION", "false").lower() in {"1", "true", "yes"}:
		return

	# Read Twilio auth token from application settings (.env-backed)
	auth_token = settings.twilio_auth_token if hasattr(settings, "twilio_auth_token") else os.getenv("TWILIO_AUTH_TOKEN")
	if not auth_token:
		raise HTTPException(status_code=500, detail="Twilio auth token not configured")
	if not x_twilio_signature:
		raise HTTPException(status_code=401, detail="Missing Twilio signature")

	validator = RequestValidator(auth_token)

	# Twilio signs the exact URL that Twilio hits (public URL);
	# In development behind proxies, ensure correct host or disable verification.
	# Build the original public URL Twilio hit, accounting for proxies/ngrok
	# Prefer forwarded proto/host when present, else fall back to request.url
	try:
		headers = request.headers
		host = headers.get("x-forwarded-host") or headers.get("host") or request.url.hostname
		proto = headers.get("x-forwarded-proto") or request.url.scheme
		path = request.url.path
		query = request.url.query
		url = f"{proto}://{host}{path}"
		if query:
			url = f"{url}?{query}"
	except Exception:
		url = str(request.url)

	# Collect body form params for signature verification
	if request.headers.get("content-type", "").startswith("application/x-www-form-urlencoded"):
		form = await request.form()
		params = {k: str(v) for k, v in form.items()}
	else:
		params = {}

	if not validator.validate(url, params, x_twilio_signature):
		raise HTTPException(status_code=401, detail="Invalid Twilio signature")


async def rate_limit(
	request: Request,
	key_prefix: str = "rl:default",
	limit: int = 20,
	window_seconds: int = 60,
) -> None:
	"""Simple Redis-backed sliding window rate limit per IP and path."""
	try:
		client = get_redis_client()
		if client is None:
			# If Redis not configured, skip limiting in dev
			return
		ip = request.client.host if request.client else "unknown"
		key = f"{key_prefix}:{request.url.path}:{ip}"
		pipe = client.pipeline()
		pipe.incr(key, 1)
		pipe.expire(key, window_seconds)
		count, _ = pipe.execute()
		if int(count) > limit:
			raise HTTPException(status_code=429, detail="Rate limit exceeded")
	except HTTPException:
		raise
	except Exception:
		# Fail open in case of Redis errors (connection refused, etc.)
		return
