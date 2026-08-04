# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-07-13 — Added OSRM_BASE_URL (line 50) and AVG_VISIT_DURATION_MINUTES
#   (line 57): config seam so routing.py's OSRM host and per-visit service-
#   time estimate are env-driven instead of hardcoded — a production self-
#   hosted OSRM instance is now a .env change, not a code change.
# 2026-07-14 — Added TRANSCRIPTION_PROVIDER/WHISPER_MODEL_SIZE/WHISPER_DEVICE
#   (below OPENAI_API_KEY): config seam so core/transcription.py can pick
#   hosted OpenAI Whisper vs. self-hosted faster-whisper. Default unchanged
#   ("openai") — existing behavior is preserved until explicitly switched.
#   Full detail + why for both days: /changelog.md
# ───────────────────────────────────────────────────────────────────────────
from functools import lru_cache
from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # App
    APP_NAME: str = "TIQCollect"
    APP_ENV: str = "development"
    DEBUG: bool = False
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # Database
    DATABASE_URL: str

    # Redis
    REDIS_URL: str = "redis://localhost:16379/0"
    CELERY_BROKER_URL: str = "redis://localhost:16379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:16379/2"

    # MinIO
    MINIO_ENDPOINT: str = "localhost:19000"
    MINIO_ACCESS_KEY: str
    MINIO_SECRET_KEY: str
    MINIO_BUCKET_DOCUMENTS: str = "tiq-documents"
    MINIO_SECURE: bool = False

    # Google Maps
    GOOGLE_MAPS_API_KEY: str = ""

    # OSRM routing — public demo server by default; point this at a self-hosted
    # OSRM instance (or paid provider) in production via .env, no code change needed.
    OSRM_BASE_URL: str = "http://router.project-osrm.org"

    # Estimated time an agent spends actually doing a visit (verifying identity,
    # discussing payment, filling the visit form) — added to travel time when
    # checking time-window feasibility, so windows reflect real elapsed time,
    # not just driving time. No live checkout event exists yet to compute a
    # true historical average from, so this is a tunable estimate for now.
    AVG_VISIT_DURATION_MINUTES: int = 15

    # Firebase
    FIREBASE_CREDENTIALS_PATH: str = "./firebase-credentials.json"

    # CORS
    ALLOWED_ORIGINS: str = "http://localhost:5473"

    @property
    def allowed_origins_list(self) -> List[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",")]

    # Bank Command Centre
    COMMAND_CENTRE_API_KEY: str

    # Gate the /api/field-ops/* contract endpoints behind COMMAND_CENTRE_API_KEY.
    # Off by default so a local Command Centre works with no configuration —
    # its proxy sends no auth header. Turn on in any deployment reachable
    # beyond localhost: those endpoints expose live agent GPS and collections
    # figures. See api/v1/endpoints/field_ops.py.
    FIELD_OPS_REQUIRE_API_KEY: bool = False

    # OpenAI (Whisper transcription)
    OPENAI_API_KEY: str = ""

    # Transcription provider seam — "openai" (hosted Whisper API, paid, uses
    # OPENAI_API_KEY above) or "local" (self-hosted faster-whisper, free).
    # Both implement the same bytes-in/English-text-out contract in
    # core/transcription.py, so switching is a config change, not a code
    # change. Default stays "openai" so existing behavior is unchanged.
    TRANSCRIPTION_PROVIDER: str = "openai"
    WHISPER_MODEL_SIZE: str = "small"
    WHISPER_DEVICE: str = "cpu"

    # RBI contact hours
    CONTACT_HOUR_START: int = 8
    CONTACT_HOUR_END: int = 19

    # SOS
    SOS_EMERGENCY_CONTACTS: str = ""

    @property
    def sos_contacts_list(self) -> List[str]:
        return [c.strip() for c in self.SOS_EMERGENCY_CONTACTS.split(",") if c.strip()]

    # Rate limiting
    RATE_LIMIT_PER_MINUTE: int = 60
    AUTH_RATE_LIMIT_PER_MINUTE: int = 10

    # Razorpay
    RAZORPAY_TEST_API: str = ""
    RAZORPAY_TEST_KEY_SECRET: str = ""

    # Twilio
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_PHONE_NUMBER: str = ""
    TWILIO_WHATSAPP_FROM: str = "whatsapp:+14155238886"
    TWILIO_API_KEY_SID: str = ""
    TWILIO_API_KEY_SECRET: str = ""
    TWILIO_TWIML_APP_SID: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
