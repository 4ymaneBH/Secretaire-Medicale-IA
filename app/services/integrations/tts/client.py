"""Text-to-speech client using ElevenLabs."""
import hashlib
from typing import Optional, Dict, Any
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.api.deps import get_redis_client

settings = get_settings()
logger = get_logger(__name__)


class TTSError(Exception):
    pass


class TTSService:
    def __init__(self):
        self.api_key = settings.elevenlabs_api_key
        self.model = settings.elevenlabs_model
        self.voice_id_fr = settings.elevenlabs_voice_id_fr
        self.voice_id_en = settings.elevenlabs_voice_id_en
        self.base_url = "https://api.elevenlabs.io/v1"
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"xi-api-key": self.api_key, "Content-Type": "application/json"},
            timeout=30.0,
        )
        self._audio_cache: Dict[str, bytes] = {}
        self._cache_max_size = 100
        self._redis_cache_prefix = "tts_cache:"
        self._redis_cache_ttl = 86400 * 30  # 30 days

    async def close(self):
        await self.client.aclose()

    def _get_voice_id(self, language: str) -> str:
        return settings.get_voice_id(language)

    def _prepare_text_for_tts(self, text: str, language: str) -> str:
        """Hook for language-specific pronunciation tweaks before synthesis."""
        return text

    def _get_cache_key(self, text: str, language: str) -> str:
        return hashlib.md5(f"{text}:{language}:{self.model}".encode()).hexdigest()

    async def _get_cached_audio(self, text: str, language: str) -> Optional[bytes]:
        """Get audio from cache (in-memory first, then Redis)."""
        cache_key = self._get_cache_key(text, language)

        # Check in-memory cache first (fastest)
        if cache_key in self._audio_cache:
            logger.debug(f"[TTS] Cache hit (memory): {cache_key[:8]}...")
            return self._audio_cache[cache_key]

        # Check Redis cache (persistent across instances)
        try:
            redis_client = get_redis_client()
            if redis_client:
                redis_key = f"{self._redis_cache_prefix}{cache_key}"
                cached_data = redis_client.get(redis_key)

                if cached_data:
                    logger.debug(f"[TTS] Cache hit (Redis): {cache_key[:8]}...")
                    # Store in memory cache too
                    self._audio_cache[cache_key] = cached_data
                    return cached_data
        except Exception as e:
            logger.debug(f"[TTS] Redis cache check failed: {e}")

        return None

    async def _cache_audio(self, text: str, language: str, audio_data: bytes):
        """Cache audio in both memory and Redis."""
        cache_key = self._get_cache_key(text, language)

        # In-memory cache (with LRU eviction)
        if len(self._audio_cache) >= self._cache_max_size:
            self._audio_cache.pop(next(iter(self._audio_cache)))
        self._audio_cache[cache_key] = audio_data

        # Redis cache (persistent, 30-day TTL)
        try:
            redis_client = get_redis_client()
            if redis_client:
                redis_key = f"{self._redis_cache_prefix}{cache_key}"
                redis_client.setex(redis_key, self._redis_cache_ttl, audio_data)
                logger.debug(f"[TTS] Cached to Redis: {cache_key[:8]}...")
        except Exception as e:
            logger.debug(f"[TTS] Redis cache store failed: {e}")

    async def synthesize_speech(
        self, text: str, language: str = "fr", use_cache: bool = True, 
        voice_settings: Optional[Dict[str, Any]] = None, use_ssml: bool = False,
        intent_type: Optional[str] = None  # NEW: Pass intent for dynamic prosody
    ) -> bytes:
        """Synthesize speech with ElevenLabs, with optional SSML enhancement.
        
        Args:
            use_ssml: If True, enhance text with pauses and prosody for natural speech
            intent_type: Intent type for dynamic prosody (empathy, success, neutral, etc.)
        """
        if not text.strip():
            raise TTSError("Text cannot be empty")
        if use_cache:
            cached = await self._get_cached_audio(text, language)
            if cached:
                return cached
        voice_id = self._get_voice_id(language)
        effective_text = self._prepare_text_for_tts(text, language)
        
        # Apply SSML enhancement for natural speech patterns
        if use_ssml:
            try:
                from app.services.integrations.tts.ssml_processor import SSMLProcessor
                ssml = SSMLProcessor()
                # Pass intent_type for intent-aware SSML coloring (empathy, alerts, etc.)
                effective_text = ssml.enhance(effective_text, language, intent_type=intent_type)
                logger.debug(f"[TTS] SSML enhanced text (intent: {intent_type or 'neutral'})")
            except Exception as e:
                logger.warning(f"[TTS] SSML enhancement failed, using plain text: {e}")
        
        # Dynamic prosody based on intent for emotional appropriateness
        # This makes the assistant sound empathetic when needed, energetic when celebrating, etc.
        # 
        # VOICE NATURALNESS TUNING:
        # - Lower stability (0.25-0.35) = more organic micro-variations (breathing simulation)
        # - Speed varies by intent: empathy slower (1.05x), alerts slightly faster (1.12x)
        # - Base speed 1.10x is optimal for phone conversations (warm but not sluggish)
        
        if not voice_settings:
            # Empathy/concern intents: softer, slower, calmer, reassuring
            # For patient care calls, technical issues, apologies
            if intent_type in ["empathy", "apology", "concern", "technical_issue", "patient_care"]:
                settings_payload = {
                    "stability": 0.25,           # More variation for warmth & breathing
                    "similarity_boost": 0.75,
                    "style": 0.15,               # Softer, gentler delivery
                    "use_speaker_boost": True,
                }
                speech_speed = 1.05  # Slower for warmth and clarity
                
            # Alert/urgency intents: clear, slightly faster, focused
            # For missed dose alerts, low battery warnings, urgent follow-ups
            elif intent_type in ["alert", "missed_dose", "low_battery", "urgent", "reminder"]:
                settings_payload = {
                    "stability": 0.40,           # More stable for clarity
                    "similarity_boost": 0.80,
                    "style": 0.35,               # Focused but friendly
                    "use_speaker_boost": True,
                }
                speech_speed = 1.12  # Slightly faster for urgency
                
            # Success/positive intents: more energetic, upbeat
            # For confirmations, resolutions, greetings
            elif intent_type in ["success", "resolution", "greeting", "confirmation", "farewell"]:
                settings_payload = {
                    "stability": 0.40,           # Stable but with life
                    "similarity_boost": 0.75,
                    "style": 0.55,               # More expressive, warm
                    "use_speaker_boost": True,
                }
                speech_speed = 1.10  # Natural, friendly pace
                
            # Support/informational: balanced, conversational
            # For patient support, questions, explanations
            elif intent_type in ["support", "explanation", "information", "question"]:
                settings_payload = {
                    "stability": 0.32,           # Natural variation
                    "similarity_boost": 0.75,
                    "style": 0.40,               # Conversational
                    "use_speaker_boost": True,
                }
                speech_speed = 1.08  # Comfortable listening pace
                
            # Default neutral: balanced, warm conversational
            else:
                settings_payload = {
                    "stability": 0.30,           # Natural micro-variations (breathing)
                    "similarity_boost": 0.75,
                    "style": 0.35,               # Conversational warmth
                    "use_speaker_boost": True,
                }
                speech_speed = 1.10  # Optimal phone conversation pace
        else:
            settings_payload = voice_settings
            speech_speed = 1.10  # Default if custom settings provided
            
        payload = {
            "text": effective_text, 
            "model_id": self.model, 
            "voice_settings": settings_payload,
            "speed": speech_speed,
        }
        
        # Wrap the actual API call with circuit breaker protection
        return await self._call_elevenlabs_api(voice_id, payload, text, language, use_cache)
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, max=5),
        retry=retry_if_exception_type(httpx.HTTPError),
        reraise=True,
    )
    async def _call_elevenlabs_api(
        self, 
        voice_id: str, 
        payload: Dict[str, Any],
        original_text: str,
        language: str,
        use_cache: bool
    ) -> bytes:
        """Make the actual ElevenLabs API call with circuit breaker protection."""
        try:
            response = await self.client.post(f"/text-to-speech/{voice_id}", json=payload)
            response.raise_for_status()
            audio_data = response.content
            if use_cache:
                await self._cache_audio(original_text, language, audio_data)
            return audio_data
        except httpx.HTTPStatusError as exc:
            # If FR voice ID is invalid/missing, try fallback to EN voice ID.
            status = getattr(exc.response, "status_code", None)
            body = getattr(exc.response, "text", "")
            logger.error(f"ElevenLabs API error: {status} - {body}")
            if status == 404 and "voice_not_found" in body and language.lower() == "fr" and self.voice_id_en:
                try:
                    logger.warning("FR voice not found; falling back to EN voice ID for TTS.")
                    fallback_payload = payload.copy()
                    resp_fb = await self.client.post(f"/text-to-speech/{self.voice_id_en}", json=fallback_payload)
                    resp_fb.raise_for_status()
                    audio_data = resp_fb.content
                    if use_cache:
                        await self._cache_audio(original_text, language, audio_data)
                    return audio_data
                except Exception as fb_exc:
                    logger.error(f"Fallback TTS failed: {fb_exc}")
            raise TTSError(f"Failed to synthesize speech: HTTP {status}")

    async def synthesize_batch(self, texts: list[tuple[str, str]], use_cache: bool = True) -> list[bytes]:
        results = []
        for text, lang in texts:
            try:
                results.append(await self.synthesize_speech(text, lang, use_cache))
            except Exception:
                results.append(b"")
        return results

    def _preprocess_text(self, text: str, language: str) -> str:
        """Hook for per-language pronunciation fixes before synthesis."""
        return text

    async def get_available_voices(self) -> list[Dict[str, Any]]:
        try:
            response = await self.client.get("/voices")
            response.raise_for_status()
            return response.json().get("voices", [])
        except Exception as exc:
            logger.error(f"Failed to get voices: {exc}")
            return []

    def clear_cache(self):
        self._audio_cache.clear()

    def get_cache_stats(self) -> Dict[str, Any]:
        return {"size": len(self._audio_cache), "max_size": self._cache_max_size}


def get_tts_service() -> TTSService:
    return TTSService()
