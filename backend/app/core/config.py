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
# 2026-07-30 — Added borrower-OTP params (OTP_LENGTH/OTP_TTL_SECONDS/
#   OTP_MAX_ATTEMPTS/OTP_RESEND_THROTTLE_SECONDS/OTP_MAX_SENDS, below Twilio):
#   config seam for OtpService's payment-verification OTP. 4 digits / 5-min
#   expiry are product decisions; a 4-digit code has only 10,000 combinations
#   so OTP_MAX_ATTEMPTS (3) is the real brute-force defence, and
#   RESEND_THROTTLE/MAX_SENDS bound SMS-bomb / cost abuse. See
#   prototype_to_product/30.07.md and /changelog.md.
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

    # Agency identity — shown on the public agent-verification page.
    AGENCY_NAME: str = "TIQ Financial Services Pvt. Ltd."
    AGENCY_RBI_REG: str = "RB-2024-0192"

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

    # Host the BROWSER uses for pre-signed upload/download URLs.
    #
    # A pre-signed URL is handed to the browser, which uploads to MinIO
    # directly, so it must carry a host the browser can actually resolve.
    # MINIO_ENDPOINT above is how the API reaches MinIO, which behind Docker is
    # an internal name like "minio:9000" — useless to a browser.
    #
    # Left blank these fall back to MINIO_ENDPOINT, which is correct for local
    # dev where both are localhost. Point it at a public media host (and set
    # MINIO_PUBLIC_SECURE=true behind TLS) and uploads work from anywhere, with
    # no code change.
    MINIO_PUBLIC_ENDPOINT: str = ""
    # Typed str, not bool — docker compose renders an unset ${VAR:-} as the
    # empty string, and pydantic rejects "" for a bool outright rather than
    # treating it as absent. Coerced in the property below instead.
    MINIO_PUBLIC_SECURE: str = ""

    @property
    def minio_public_endpoint(self) -> str:
        return self.MINIO_PUBLIC_ENDPOINT.strip() or self.MINIO_ENDPOINT

    @property
    def minio_public_secure(self) -> bool:
        raw = self.MINIO_PUBLIC_SECURE.strip().lower()
        if not raw:
            return self.MINIO_SECURE
        return raw in ("1", "true", "yes", "on")

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

    # RBI contact hours — the window collections contact is legally allowed.
    # Env-driven so a demo in a non-IST timezone can widen/shift it (see geo.py,
    # which now reads these instead of its own hardcoded copies).
    CONTACT_HOUR_START: int = 8
    CONTACT_HOUR_END: int = 19

    # Geo-fence radius (metres) an agent must be within to record a visit.
    # Moved out of geo.py so it is a .env change, not a code change.
    GEO_FENCE_METRES: int = 100

    # ─── Demo / Showcase seams ──────────────────────────────────────────────
    # Master switch. When true: (1) a fixed set of demo customers is re-placed
    # within DEMO_ANCHOR_RADIUS_M of the agent's live GPS on every check-in, so
    # the geo-fence passes wherever on earth the demo is run; (2) the demo
    # contact (DEMO0003) name/phone is synced from the vars below on startup.
    # Leave false in real deployments — none of this touches non-demo data.
    DEMO_MODE: bool = False
    # The showcase customer (DEMO0003). Swap the phone to your CEO's / manager's
    # number here — no reseed, no rebuild; a backend restart applies it.
    DEMO_CONTACT_NAME: str = "Balraj Singh"
    DEMO_CONTACT_PHONE: str = "8015935790"
    # customer_ref of that showcase customer. Also what _sync_demo_contact()
    # renames on startup, so the name/phone and the anchoring agree by
    # construction instead of by two copies of the same literal.
    DEMO_CONTACT_REF: str = "DEMO0003"
    # Which demo customers follow the agent's live location. Comma-separated
    # customer_refs. DEMO_CONTACT_REF does not need to be listed — it is always
    # anchored, and always first; see demo_anchor_refs_list.
    DEMO_ANCHOR_REFS: str = "DEMO0003,DEMO0002,DEMO0006,DEMO0007,DEMO0010"
    # Max spread (metres) of that cluster around the agent's live GPS.
    DEMO_ANCHOR_RADIUS_M: int = 80

    # How the manager's case list decides a case shows its "Visited" chip.
    #
    #   false (default) — a visit whose check_in_time falls on the wall-clock
    #                     day. Correct for a live deployment.
    #   true            — a visit whose check_in_time falls on the case's OWN
    #                     allocation_date, i.e. the value in the list's Date
    #                     column.
    #
    # The seed is run once and its activity is stamped with that day's date, so
    # by the day of the demo nothing matches "today" any more and every row
    # loses its chip. Matching the row's own date keeps the seeded story intact
    # however long after seeding it is shown. It reads the same on screen —
    # "this case was visited on the day it was allocated" — but it is a demo
    # seam, not the real rule, so it stays off unless asked for.
    DEMO_VISITED_BY_ALLOCATION_DATE: bool = False

    # Rehearsal mode. The demo is one case, and showing it records a visit that
    # moves it to Done — so the next audience finds nothing to demonstrate.
    # When true, the agent's check-in first rewinds that case to a stored
    # baseline, which makes the same run-through repeatable without reseeding.
    #
    # The visit still writes normally: submitting is several requests (visit →
    # payment carrying its id → PTP → transcription), so suppressing the writes
    # would break the receipt, the Done list and the beat counters. It is undone
    # afterwards instead. Nothing outside the one demo case is touched.
    #
    # Needs a baseline: python -m scripts.demo_reset --save
    DEMO_REHEARSAL_MODE: bool = False

    @property
    def demo_anchor_refs_list(self) -> List[str]:
        """Anchor refs with the showcase customer guaranteed first.

        The whole point of the demo contact is that you can call/WhatsApp a real
        person live, which needs their case inside the geo-fence. Editing
        DEMO_ANCHOR_REFS and forgetting to keep DEMO_CONTACT_REF in the list
        used to drop them out of range with no error — the demo just quietly
        stopped working. Forced in here so no .env edit can break it.

        First position is deliberate: offset[0] is the smallest of the
        deterministic offsets, so the showcase customer lands nearest the agent
        and therefore sorts to the top of the distance-ordered case list.
        """
        refs = [r.strip() for r in self.DEMO_ANCHOR_REFS.split(",") if r.strip()]
        contact = self.DEMO_CONTACT_REF.strip()
        if contact:
            refs = [contact] + [r for r in refs if r != contact]
        # De-duplicate, preserving order — a repeated ref would otherwise take
        # the offset of its first occurrence and stack two customers on one spot.
        seen: set[str] = set()
        return [r for r in refs if not (r in seen or seen.add(r))]

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

    # Default country code (no '+') for bare national phone numbers, used by
    # NotificationService.normalize_phone. Numbers entered in full '+<cc>...'
    # form are respected as-is, so a foreign demo number works when written that
    # way; this only fills in the code for a bare national number.
    DEFAULT_COUNTRY_CODE: str = "91"

    # Twilio
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_PHONE_NUMBER: str = ""
    TWILIO_WHATSAPP_FROM: str = "whatsapp:+14155238886"
    TWILIO_API_KEY_SID: str = ""
    TWILIO_API_KEY_SECRET: str = ""
    TWILIO_TWIML_APP_SID: str = ""

    # Borrower payment-verification OTP (see services/otp_service.py)
    OTP_LENGTH: int = 4                       # product decision: 4-digit code
    OTP_TTL_SECONDS: int = 300                # product decision: 5-minute expiry
    OTP_MAX_ATTEMPTS: int = 3                 # wrong tries before the code is burned (brute-force cap)
    OTP_RESEND_THROTTLE_SECONDS: int = 30     # min gap between two sends for the same collection
    OTP_MAX_SENDS: int = 4                    # 1 initial send + up to 3 resends per collection


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
