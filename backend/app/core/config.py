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
# 2026-08-21 — Added the repayment-likelihood block (REPAYMENT_*, at the end):
#   config seam for ml/repayment_scorecard.py and services/repayment_service.py.
#   Two of these are rollout gates and BOTH default to False, so a fresh
#   deployment behaves exactly as it does today: REPAYMENT_WRITE_RISK_SCORE
#   stops the scorer touching the legacy Customer.risk_score column, and
#   REPAYMENT_REPRICE_OPEN_CASES is off because nothing has ever recomputed
#   Case.priority on an existing case and turning that on is new behaviour,
#   not a like-for-like swap. Wiring the nightly task must not be what makes
#   either of them live.
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

    # ── LLM provider (2026-08-19) ────────────────────────────────────────
    # Six product features call an LLM. They all hardcoded gpt-4o-mini and
    # silently served a written-in answer when the key was missing, so a dead
    # integration was indistinguishable from a working one. See core/llm.py.
    #
    # "groq" | "openai" | "anthropic" | "none". Groq is OpenAI-compatible, so
    # groq and openai run through the same SDK and differ only by base_url and
    # model name; anthropic has its own SDK (block below). "none" is a kill
    # switch: nothing calls out, LLM_FALLBACK_PROVIDER included.
    LLM_PROVIDER: str = "groq"
    GROQ_API_KEY: str = ""
    GROQ_BASE_URL: str = "https://api.groq.com/openai/v1"

    # Deliberately configuration, never a literal in the call sites. Groq retires
    # models at short notice; a stale name must surface as MODEL_NOT_FOUND on the
    # health endpoint rather than as a silent fallback nobody notices.
    # Verified against the live key on 2026-08-19. The Llama models this was
    # first pointed at return 404 on this account, and of the models actually
    # served, gpt-oss-120b is the only one that honours JSON mode — which four
    # of the six call sites depend on. gpt-oss-20b and qwen3.6-27b answer prose
    # fine but error on response_format.
    LLM_MODEL: str = "openai/gpt-oss-120b"
    # Used when LLM_PROVIDER=openai, so switching provider does not also require
    # remembering to change the model.
    LLM_MODEL_OPENAI: str = "gpt-4o-mini"

    # gpt-oss is a reasoning model: it spends output budget thinking before it
    # answers, which truncated JSON mid-object and made Groq reject it with a
    # 400 json_validate_failed. These are structured-extraction prompts, not
    # problems needing deep reasoning, so "low" is both more reliable and
    # measurably faster. Empty string omits the parameter entirely, for models
    # and providers that do not accept it.
    LLM_REASONING_EFFORT: str = "low"

    LLM_TIMEOUT_SECONDS: float = 20.0
    # Retries apply to rate limits and transient upstream errors only — never to
    # an auth failure or an unknown model, which retrying cannot fix.
    LLM_MAX_RETRIES: int = 2
    # Answers are cached by (purpose, model, prompt). Not an optimisation: Groq's
    # limits are tight, and the same question was previously billed every time.
    LLM_CACHE_TTL_SECONDS: int = 3600

    # ── Anthropic provider (2026-09-24, F01) ─────────────────────────────
    # LLM_PROVIDER="anthropic" selects it. Two tiers, because the product makes
    # two kinds of call: short single-shot extraction/briefing prompts (the
    # six existing features, llm.complete) and multi-step tool-using agents
    # (llm.chat, for the agent runtime of §8.1). A cheap fast model for the
    # first and a stronger one for the second, each configuration rather than
    # a literal, for the same reason LLM_MODEL is.
    ANTHROPIC_API_KEY: str = ""
    # Ids as the plan names them (§8.1): Haiku pinned to its dated snapshot so
    # a high-volume path does not move under an alias; Sonnet 5 has no dated id.
    LLM_MODEL_ANTHROPIC: str = "claude-haiku-4-5-20251001"
    LLM_MODEL_ANTHROPIC_AGENT: str = "claude-sonnet-5"
    # Thinking depth for agent turns on models that accept `effort`. Valid
    # values depend on the model: low | medium | high everywhere effort exists
    # (Opus 4.5 stops there), + max on the 4.6 line, + xhigh from Opus 4.7 /
    # Sonnet 5 on. "medium" rather than the API's own default of "high": these
    # agents read KPI tables and draft briefs, and an agent that needs more can
    # pass effort= per call. Empty = API default. complete() does not use this;
    # it reuses LLM_REASONING_EFFORT ("low", valid on every effort model).
    LLM_AGENT_EFFORT: str = "medium"
    # chat() gets its own timeout: a Sonnet 5 turn with adaptive thinking and a
    # 16k-token ceiling can legitimately run well past the 20 s sized for short
    # complete() prompts — and TIMEOUT is a fallback trigger, so a too-short
    # value would switch provider in the middle of an agent's loop.
    LLM_AGENT_TIMEOUT_SECONDS: float = 120.0
    # A second provider tried once when the first is unusable or fails on its
    # side (no key, auth, rate limit, timeout, outage) — e.g. "groq" behind
    # "anthropic". Never tried for a caller-shaped failure (bad JSON, invalid
    # request, refusal): another model would not make those right. Empty = off.
    LLM_FALLBACK_PROVIDER: str = ""

    # Fraud / anomaly detection (2026-08-19)
    # Thresholds are deliberately conservative. A detector that cries wolf is
    # worse than none: it teaches the manager to dismiss the panel, and then the
    # real finding gets dismissed with the rest.
    #
    # 120 km/h is not "fast driving" — it is sustained motorway speed averaged
    # across an entire door-to-door gap including parking and walking, which no
    # NCR field round produces.
    FRAUD_MAX_SPEED_KMH: int = 120
    # A photo taken this far from the check-in point was not taken at the visit.
    # Generous enough to absorb poor urban GPS and a large apartment block.
    FRAUD_PHOTO_DRIFT_METRES: int = 250
    # Below this, nothing that could be called a collections conversation
    # happened at the door.
    FRAUD_MIN_VISIT_SECONDS: int = 60
    # Overlapping visits closer together than this are treated as a missed
    # check-out at one address, not a claim to be in two places at once.
    FRAUD_OVERLAP_MIN_METRES: int = 500
    # Multiplier on GEO_FENCE_METRES before a recorded distance is treated as a
    # fence breach rather than GPS scatter.
    FRAUD_FENCE_TOLERANCE: float = 1.5
    # Default look-back for a scan when no date range is given.
    FRAUD_SCAN_DAYS: int = 30
    # Trail checks. A visit is only contradicted when the agent was tracked
    # this many times during it and was never within this distance. Absence
    # of trail is never a finding — see fraud_service._trail_contradiction.
    FRAUD_TRAIL_MIN_POINTS: int = 3
    FRAUD_TRAIL_AWAY_METRES: int = 500

    # Repayment likelihood (2026-08-21)
    # Customer.risk_score was dpd/90*60 + (750-cibil)/750*40 — a relabelling of
    # two columns we already store, feeding collection_priority_score at 0.3
    # weight and therefore ordering field work. ml/repayment_scorecard.py
    # replaces it with a scorecard whose every point has a stated reason.
    #
    # The eleven factor WEIGHTS are deliberately NOT here. They are module
    # constants in the scorecard with prose justifications, because a weight
    # change is a model change: it must bump SCORECARD_VERSION, which is stamped
    # on every snapshot row. An env var could change what the score means while
    # leaving already-written rows claiming a version that no longer describes
    # them. What lives here is the business-tunable thresholds only.
    #
    # scorecard | command_center | model. Only "scorecard" is implemented; the
    # other two exist so adding a tier later is wiring rather than a rewrite.
    REPAYMENT_SCORER: str = "scorecard"
    # ROLLOUT GATE, and OFF by default. False -> the scorer computes and
    # snapshots exactly as normal but never writes Customer.risk_score, so the
    # legacy value stays where it is and every downstream consumer behaves as it
    # does today. Training data keeps accruing meanwhile, which is the point:
    # the table fills up whether or not the column is live.
    #
    # Defaulted False on 2026-08-21 (was True). Measured on the seeded book,
    # turning this on moves 424 of 425 customers and shifts 74.4% of them across
    # a RiskCategory band. A change of that size must be an explicit deployment
    # decision — it must NOT become active merely because the nightly Celery
    # task got wired up. Enabling and rolling back are then the same one-line
    # act: set it, restart the worker. No migration, nothing to undo.
    # ── Trained-model rollout gates (2026-09-08) ────────────────────────────
    # OFF by default, for the same reason the two gates below it are: an
    # artifact being committed is not a decision to change what the running
    # system does. With this False the product behaves exactly as it does
    # today — every surface keeps reading the hand-weighted scorecards — and
    # GET /manager/ml/health still reports what is loaded, so the models can be
    # inspected before they are trusted. Enabling and rolling back are the same
    # one-line act.
    # PROMOTED 2026-09-08, on a measured allocator change. Turning this on makes
    # the nightly SMART allocation score its pool with recovery_risk and use the
    # calibrated probability in prob_recovery, together with the rescaled value
    # transform. Measured over 8 seeds x 1,200 cases: realised recovery +28.0%
    # (8/8 seeds, +18.4% to +39.6%), realised recovery rate 0.2203 -> 0.2473, and
    # the BLOCKED set identical in every run.
    #
    # ROLLBACK IS THIS LINE. Set it False and the allocator returns to the
    # hand-weighted agent-side estimate and the original value transform, in the
    # same act — see PlannerService._ml_recovery_probabilities for why the two
    # cannot be rolled back separately.
    ML_SCORING_ENABLED: bool = True
    # Which artifact version the DecisionEngine loads. "champion" follows
    # app/ml/artifacts/<model>/champion.txt, which train_models.py only writes
    # when every gate passes.
    ML_MODEL_VERSION: str = "champion"
    # 2026-09-09 — the retraining lifecycle's only switch. True lets the 19:15
    # monitor open a CANDIDATE when it recommends a retrain; it can never
    # promote one, so the blast radius of True is a training job and a row
    # awaiting a person. Set False to keep monitoring and stop the automation.
    ML_AUTO_RETRAIN_ENABLED: bool = True
    # Which value transform the allocator's expected-recovery term uses.
    # "log_current" is the pre-2026-09-08 production behaviour and is kept as the
    # baseline every before/after comparison is measured against; do not delete
    # it. See GlobalAllocator.VALUE_TRANSFORMS.
    ALLOCATOR_VALUE_TRANSFORM: str = "log_rescaled"
    # Epsilon-greedy exploration: the share of each night's assignments handed to
    # a RANDOM ELIGIBLE agent instead of the best-scoring one.
    #
    # THIS IS THE ONE SETTING HERE THAT CHANGES WHAT AGENTS ARE ASKED TO DO, so
    # it is worth being explicit about what it buys. Every historical
    # (agent, case, outcome) row was produced by this allocator, so good agents
    # got good cases and any agent-fit model fitted on that history learns the
    # allocator rather than the agents. A randomised slice is the only way to
    # break that confound.
    #
    # SIZED FOR ONE QUESTION. At ~233 allocations/day over 30 agents, 10% yields
    # ~23 randomised visits a day and answers "does agent identity matter at
    # all?" — a variance component — in roughly 1.3-2.1 months. It does NOT
    # power a per-agent ranking: that needs ~820 randomised visits per agent,
    # 24,595 total, i.e. 2.9 years at this rate. Do not read the resulting data
    # as a leaderboard.
    #
    # Exploration NEVER reaches the hard gates: candidates come only from agents
    # that already passed DNC, hostility, female-agent, territory and PTP
    # fatigue, and capacity is preserved by swapping rather than moving.
    # Set to 0.0 to switch it off.
    ALLOCATOR_EXPLORATION_RATE: float = 0.10
    # Log every served score to model_predictions for the feedback loop. Cheap,
    # append-only, and it is the only way the monitor can compare what was
    # predicted against what happened. Separate from ML_SCORING_ENABLED so a
    # shadow deployment can record without acting.
    ML_LOG_PREDICTIONS: bool = True

    REPAYMENT_WRITE_RISK_SCORE: bool = False
    # KILL SWITCH, and OFF by design. Case.priority is written once at case
    # creation (seed_data.py:1577, ingest_daily.py:437) and has never been
    # recomputed for an existing case. Turning this on is NEW behaviour: it
    # would reprice open cases and change the nightly allocation order for work
    # already in flight. Left off so the default deployment matches today.
    REPAYMENT_REPRICE_OPEN_CASES: bool = False
    # How far back behavioural evidence is read. Six months matches the seeded
    # history depth and one full PTP cycle several times over.
    REPAYMENT_BEHAVIOUR_WINDOW_DAYS: int = 180
    # Below these, the corresponding factor ABSTAINS rather than scoring zero.
    # One resolved promise is an anecdote; zero is not evidence of anything.
    # Scoring an unknown borrower as a bad one makes every new case a defaulter.
    REPAYMENT_MIN_PTPS_FOR_HISTORY: int = 2
    REPAYMENT_MIN_VISITS_FOR_CONTACT: int = 2
    # Share of the scorecard's total weight that must have had evidence before
    # a NUMBER is shown rather than just a band. Silence is not evidence, and a
    # confident-looking figure resting on two factors is worse than no figure.
    #
    # 0.55 is DERIVED, not chosen for roundness. The six factors that are almost
    # always available (delinquency 30 + arrears 10 + bureau 8 + security 6 +
    # last-payment 5 + segment 4) total 63 of 124 = 0.5081 on their own, so the
    # previous 0.5 floor could never catch a borrower about whom nothing
    # BEHAVIOURAL was known — and on 2026-08-21, 17 of 18 such customers sailed
    # through it into LOW. Adding the cheapest behavioural factor
    # (contactability, 12) reaches 0.6048. Any floor in (0.5081, 0.6048] forces
    # at least one behavioural signal; 0.55 sits mid-band so a small weight
    # change cannot silently flip the property.
    REPAYMENT_MIN_COVERAGE_TO_SHOW: float = 0.55
    # How long after a score we wait before deciding what the borrower did.
    # 30 days is one billing cycle — long enough for a promise to come due.
    REPAYMENT_OUTCOME_HORIZON_DAYS: int = 30
    # Share of the case target that counts as REPAID rather than PARTIAL.
    REPAYMENT_FULL_RATIO: float = 0.9
    # Snapshot write policy. Scoring every loan every night would store ~191k
    # near-identical rows a year; the interesting rows are the ones next to a
    # change. A row is written on the first score, when the likelihood moves by
    # at least MIN_DELTA, when ANCHOR_DAYS have passed regardless, or when
    # ingest reported a state change on that loan.
    REPAYMENT_SNAPSHOT_MIN_DELTA: float = 1.0
    REPAYMENT_SNAPSHOT_ANCHOR_DAYS: int = 7
    # Unlabelled snapshots older than this are pruned. LABELLED rows are never
    # pruned at any age — they are the training set, which is the whole point
    # of the table. Unlike the location trail this is not employee-monitoring
    # data, so the window is generous rather than minimal.
    REPAYMENT_SNAPSHOT_RETENTION_DAYS: int = 400

    # ── Recovery potential (2026-08-24) ──────────────────────────────────────
    # Same split as the block above: weights and band edges live in
    # ml/recovery_scorecard.py because a weight change is a model change and must
    # move RECOVERY_SCORECARD_VERSION, which is stamped on every snapshot row.
    # Only business-tunable thresholds live here.
    #
    # scorecard | command_center | model. Only "scorecard" is implemented. The
    # other two are named because the brief asked whether we compute this label
    # or the bank sends it, and the honest answer is "we compute it today, and
    # here is where a supplied one would arrive". The bank's daily feed carries
    # 45 columns and recovery potential is not among them.
    RECOVERY_SCORER: str = "scorecard"
    # ROLLOUT GATE, and OFF by default. False -> the label is computed and
    # snapshotted exactly as normal but Loan.recovery_potential is never written.
    #
    # The manager surface reads the SNAPSHOT, not that column, so the feature is
    # fully visible and reviewable with this gate shut. What the gate protects is
    # the shared Loan column, which another system may one day read.
    #
    # It stays False through implementation and testing. Turning it on is a
    # deployment decision to be made once, deliberately, after a dry run and a
    # review of how the HIGH/MEDIUM/LOW distribution lands on the real book.
    # Enabling and rolling back are then the same one-line act.
    #
    # Note this differs from REPAYMENT_WRITE_RISK_SCORE in what it displaces:
    # that gate guards a real, if drifted, computed value. This one guards a
    # column currently filled by random.choices(). The gate is still closed by
    # default — the distribution review is worth having either way — but the
    # thing being replaced is noise, not signal.
    RECOVERY_WRITE_LABEL: bool = False
    # Which horizon the HIGH/MEDIUM/LOW label is banded on. 90 days, because
    # "how much can we realistically get back" is an EVENTUAL-recovery question:
    # banding on 30 would mark a slow-but-secured loan LOW and steer agents away
    # from money that is genuinely recoverable by quarter-end, which is the exact
    # misallocation this label exists to correct.
    #
    # Read by the API for display; the scorecard's own LABEL_HORIZON is what
    # actually bands the number, so the two must agree. See the assertion in
    # tests/test_recovery_scorecard.py.
    RECOVERY_LABEL_HORIZON_DAYS: int = 90
    # The horizons the labeller fills in. A row is revisited as each one matures,
    # so this list is also the set of `recovered_amount_*` columns that exist —
    # adding a horizon here without adding its column is a silent no-op.
    RECOVERY_OUTCOME_HORIZONS: list[int] = [30, 60, 90]

    # Location trail (2026-08-18)
    # How long a full-resolution trail is kept before the nightly sweep in
    # workers/tasks/location_retention.py deletes it. Movement history is
    # employee-monitoring data, so the retention window is a deliberate
    # policy setting rather than an implicit "forever".
    LOCATION_RETENTION_DAYS: int = 90
    # Client tracking parameters, served to the app so the cadence can be
    # tuned without shipping a new frontend build.
    LOCATION_MIN_MOVE_METRES: int = 50
    LOCATION_MAX_INTERVAL_SECONDS: int = 60

    # Rate limiting
    # Audit rows are kept for at least this long. A FLOOR the application
    # promises, not a backup guarantee: nothing in app/ deletes from audit_logs
    # at all (the retention sweep touches agent_locations only, and
    # tests/test_compliance_hardening.py trips if that ever changes), so today
    # the effective retention is "forever". The constant exists so the promise
    # has a name, a number and a test, and so a future pruning job — if one is
    # ever written — has a floor it cannot go under. Durability of the database
    # itself (backups, disk encryption) is a deployment concern and is not
    # claimed here. 1825 days = 5 years.
    AUDIT_LOG_RETENTION_DAYS: int = 1825

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
