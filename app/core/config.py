"""
Configuration module for the AI Voice Secretary backend.
Handles environment variables, validation, and settings management.
"""
from typing import List, Optional
from functools import lru_cache
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings with validation."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # =========================================================================
    # TWILIO CONFIGURATION
    # =========================================================================
    twilio_account_sid: str = Field(..., description="Twilio Account SID")
    twilio_auth_token: str = Field(..., description="Twilio Auth Token")
    twilio_phone_number: str = Field(..., description="Twilio Phone Number")
    twilio_webhook_base_url: str = Field(..., description="Base URL for webhooks")

    # =========================================================================
    # OPENAI CONFIGURATION
    # =========================================================================
    openai_api_key: str = Field(..., description="OpenAI API Key")
    openai_embedding_model: str = Field(default="text-embedding-3-small")
    openai_realtime_model: str = Field(default="gpt-4o-realtime-preview")

    # =========================================================================
    # SPEECH SERVICES (ElevenLabs TTS, OpenAI Whisper STT)
    # =========================================================================
    elevenlabs_api_key: str = Field(..., description="ElevenLabs API Key")
    elevenlabs_model: str = Field(default="eleven_turbo_v2_5")
    whisper_model: str = Field(default="gpt-4o-transcribe")

    # Voice IDs per supported language (fall back to French if unset)
    elevenlabs_voice_id_fr: str = Field(..., description="French voice ID")
    elevenlabs_voice_id_en: str = Field(..., description="English voice ID")
    elevenlabs_voice_id_ar: Optional[str] = Field(None, description="Arabic voice ID")
    elevenlabs_voice_id_it: Optional[str] = Field(None, description="Italian voice ID")
    elevenlabs_voice_id_es: Optional[str] = Field(None, description="Spanish voice ID")

    # =========================================================================
    # DATABASE CONFIGURATION (PostgreSQL + pgvector)
    # =========================================================================
    database_url: str = Field(..., description="PostgreSQL Database URL")
    database_pool_size: int = Field(default=20, ge=5, le=100)
    database_max_overflow: int = Field(default=10, ge=0, le=50)
    database_pool_recycle: int = Field(default=3600)
    database_pool_pre_ping: bool = Field(default=True)
    database_pool_timeout: int = Field(default=30, ge=5, le=120)
    database_echo: bool = Field(default=False)

    # =========================================================================
    # REDIS CONFIGURATION
    # =========================================================================
    redis_url: str = Field(..., description="Redis Connection URL")

    # =========================================================================
    # APPLICATION SETTINGS
    # =========================================================================
    app_env: str = Field(default="development", description="development/staging/production")
    app_host: str = Field(default="0.0.0.0")
    app_port: int = Field(default=8000, ge=1000, le=65535)
    cors_origins: str = Field(default="http://localhost:3000")

    # =========================================================================
    # CONVERSATION
    # =========================================================================
    supported_languages: str = Field(default="fr,en,ar,it,es")
    default_language: str = Field(default="fr")

    # =========================================================================
    # CALL RECORDING
    # =========================================================================
    feature_call_recording: bool = Field(default=True)

    # =========================================================================
    # RATE LIMITING
    # =========================================================================
    rate_limit_per_minute: int = Field(default=20, ge=1, le=1000)

    # =========================================================================
    # LOGGING
    # =========================================================================
    log_level: str = Field(default="INFO")
    log_format: str = Field(default="json")

    # =========================================================================
    # VALIDATORS
    # =========================================================================
    @field_validator("app_env")
    def validate_env(cls, v):
        allowed = ["development", "staging", "production"]
        if v not in allowed:
            raise ValueError(f"app_env must be one of {allowed}")
        return v

    @field_validator("log_level")
    def validate_log_level(cls, v):
        allowed = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        v_upper = v.upper()
        if v_upper not in allowed:
            raise ValueError(f"log_level must be one of {allowed}")
        return v_upper

    @field_validator("default_language")
    def validate_default_language(cls, v, info):
        try:
            data = info.data if hasattr(info, "data") else {}
            supported_val = data.get("supported_languages")
            if supported_val:
                supported = [s.strip() for s in str(supported_val).split(",")]
                if v not in supported:
                    raise ValueError("default_language must be in supported_languages")
        except Exception:
            pass
        return v

    # =========================================================================
    # COMPUTED PROPERTIES
    # =========================================================================
    @property
    def cors_origins_list(self) -> List[str]:
        return [origin.strip() for origin in self.cors_origins.split(",")]

    @property
    def supported_languages_list(self) -> List[str]:
        return [lang.strip() for lang in self.supported_languages.split(",")]

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def database_url_sync(self) -> str:
        """Synchronous database URL, for Alembic."""
        return self.database_url.replace("+asyncpg", "")

    def get_voice_id(self, language: str) -> str:
        """Get the ElevenLabs voice ID for a supported language, falling back to French."""
        voice_ids = {
            "fr": self.elevenlabs_voice_id_fr,
            "en": self.elevenlabs_voice_id_en,
            "ar": self.elevenlabs_voice_id_ar,
            "it": self.elevenlabs_voice_id_it,
            "es": self.elevenlabs_voice_id_es,
        }
        return voice_ids.get(language.lower()) or self.elevenlabs_voice_id_fr


@lru_cache()
def get_settings() -> Settings:
    """Cached settings instance (avoids re-reading .env on every call)."""
    return Settings()


settings = get_settings()
