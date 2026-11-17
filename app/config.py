"""
app/config.py
─────────────
Central configuration for the Orthopedic AI Voice Agent.
Secrets (API keys, tokens) live ONLY in .env, never in this file.
Loads settings from the .env file. Other files use:
from app.config import settings
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Application ───────────────────────────────────────────────
    APP_ENV: str = "development"

    # ── PostgreSQL ────────────────────────────────────────────────
    # Works with either postgresql:// or postgresql+psycopg:// in .env
    # DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5433/orthopedic_voice"
    DATABASE_URL: str = "postgresql+psycopg://appuser:localpass@localhost:5432/orthopedic_voice"
        
    # ── Public URL (your ngrok / server HTTPS URL, used by the phone provider) ──
    # PUBLIC_BASE_URL: str = "http://localhost:8000"
    PUBLIC_BASE_URL: str = "https://voice.indiahealthsystem.com"

    # ── Telephony: active provider ("plivo" is the only one built so far) ──
    TELEPHONY_PROVIDER: str = "plivo"
    
    # ── Language ──────────────────────────────────────────────────
    DEFAULT_LANGUAGE: str = "or"            # greeting and fixed-line language until the caller's language is known (en / hi / or)
    
    # ── Telephony (Twilio) ──────────────────────────────────────────────────
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_NUMBER: str = ""
    TWILIO_VALIDATE_SIGNATURE: bool = False
    TWILIO_VOICE: str = "Polly.Aditi"
    SPEECH_LANGUAGE: str = "en-IN"

    # ── Telephony (Plivo) ─────────────────────────────────────────
    PLIVO_AUTH_ID: str = ""
    PLIVO_AUTH_TOKEN: str = ""
    PLIVO_NUMBER: str = ""     # your Plivo number in E.164 (+91...), fill when you own one
    PLIVO_VALIDATE_SIGNATURE: bool = False  # turn on after your first successful real call
    PLIVO_SPEAK_VOICE: str = ""             # optional, only for the fallback <Speak> line
    PLIVO_SPEAK_LANGUAGE: str = ""          # optional, e.g. en-IN

    # ── Bodhan AI (speech - Paused) ────────────────────────────────────────
    BODHAN_API_BASE_URL: str = "https://api.bodhan.ai/v1"
    BODHAN_TRANSCRIBE_API_KEY: str = ""   # key for indic-transcribe
    BODHAN_SPEAK_API_KEY: str = ""        # key for indic-speak
    BODHAN_VOICE: str = "Kavya"             # exact, case-sensitive voice name
    BODHAN_TTS_STYLE: str = ""              # optional speaking style
    BODHAN_STT_LANGUAGE: str = ""           # empty = let Bodhan detect (en / hi / or)

    # ── Sarvam AI (speech) ────────────────────────────────────────
    SARVAM_API_BASE_URL: str = "https://api.sarvam.ai"
    SARVAM_API_KEY: str = ""
    SARVAM_STT_MODEL: str = "saaras:v3"
    SARVAM_STT_MODE: str = "codemix"        # transcribe | codemix (keeps English words in English)
    SARVAM_STT_LANGUAGE: str = "unknown"    # unknown = auto-detect, or od-IN / hi-IN / en-IN
    SARVAM_TTS_MODEL: str = "bulbul:v3"
    SARVAM_TTS_SPEAKER: str = "ritu"        # exact, case-sensitive; compare with test_sarvam.py
    SARVAM_TTS_PACE: float = 1.0            # 0.5 to 2.0
    SARVAM_TTS_TEMPERATURE: float = 0.6     # higher = more expressive
    
    # ── Speech providers ──────────────────────────────────────────
    STT_PROVIDER: str = "sarvam"            # sarvam | bodhan
    TTS_PROVIDER: str = "sarvam"            # sarvam | bodhan
    TTS_CACHE_DIR: str = ""                 # empty = <project>/.cache/tts (a volume in Docker)
    TTS_CONCURRENCY: int = 3                # speech requests at the same time
    
    # ── Voice pipeline tuning ─────────────────────────────────────
    VAD_RMS_THRESHOLD: int = 450            # lower = more sensitive to quiet voices
    VAD_END_SILENCE_MS: int = 600           # silence that means "caller finished"
    VAD_MIN_SPEECH_MS: int = 300            # shorter sounds are ignored as noise
    VAD_MAX_UTTERANCE_S: int = 25           # Bodhan STT accepts max 30 s per request
    BARGE_IN_MS: int = 400                  # caller speech needed to interrupt the bot           # silence that means "caller finished" (was 800)
    LOG_TRANSCRIPTS: bool = False           # True prints what the caller and the bot say (testing only)
        
    # ── Latency ───────────────────────────────────────────────────
    FILLER_ENABLED: bool = True             # short acknowledgement while a slow reply is prepared
    FILLER_AFTER_MS: int = 2500             # wait this long before acknowledging
    FILLER_MIN_SPEECH_S: float = 2.5        # only for long caller sentences, never for short answers
    FILLER_MAX_PER_CALL: int = 2            # at most this many per call
    REPLY_MAX_TOKENS: int = 220
    
    # ── AI provider ───────────────────────────────────────────────
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-5.4-mini"      # the model used for the agent's replies

    # ── Clinic ────────────────────────────────────────────────────
    DOCTOR_NAME: str = "Dr. Pushpak Samal"
    ESCALATION_PHONE: str = "" # doctor's personal number (used for call transfer)
    BACKUP_PHONE: str = ""                  # backup contact if the doctor does not answer (receptionist)
    TRANSFER_RING_SECONDS: int = 25

    # ── Derived values ────────────────────────────────────────────

    # LangGraph checkpointer + asyncpg
    @property
    def psycopg_url(self) -> str:
        """Plain postgresql:// URL, used by asyncpg and the LangGraph checkpointer."""
        return self.DATABASE_URL.replace("postgresql+psycopg://", "postgresql://", 1)

    # SQLAlchemy engine
    @property
    def sqlalchemy_url(self) -> str:
        """postgresql+psycopg:// URL, used by SQLAlchemy (forces the psycopg v3 driver)."""
        return self.psycopg_url.replace("postgresql://", "postgresql+psycopg://", 1)

    @property
    def stt_api_key(self) -> str:
        """Key for speech-to-text (the indic-transcribe key)."""
        return self.BODHAN_TRANSCRIBE_API_KEY

    @property
    def tts_api_key(self) -> str:
        """Key for text-to-speech (the indic-speak key)."""
        return self.BODHAN_SPEAK_API_KEY
    
    @property
    def bodhan_ready(self) -> bool:
        """True when the speech keys and a voice are set (enough to pre-generate audio)."""
        return bool(self.stt_api_key and self.tts_api_key and self.BODHAN_VOICE)

    def missing_voice_settings(self) -> list[str]:
        """Names of voice settings that are still empty (used for a startup warning)."""
        required = {
            "BODHAN_TRANSCRIBE_API_KEY": self.stt_api_key,
            "BODHAN_SPEAK_API_KEY": self.tts_api_key,
            "BODHAN_VOICE": self.BODHAN_VOICE,
            "ESCALATION_PHONE": self.ESCALATION_PHONE,
        }
        if self.TELEPHONY_PROVIDER.lower() == "twilio":
            required.update({"TWILIO_ACCOUNT_SID": self.TWILIO_ACCOUNT_SID,
                             "TWILIO_AUTH_TOKEN": self.TWILIO_AUTH_TOKEN})
        else:
            required.update({"PLIVO_AUTH_ID": self.PLIVO_AUTH_ID,
                             "PLIVO_AUTH_TOKEN": self.PLIVO_AUTH_TOKEN,
                             "PLIVO_NUMBER": self.PLIVO_NUMBER})
        return [name for name, value in required.items() if not value]

@lru_cache
def get_settings() -> Settings:
    """Load settings once and reuse them."""
    return Settings()


settings = get_settings()