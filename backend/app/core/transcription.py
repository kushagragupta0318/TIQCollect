# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-07-14 — New file. Provider seam for speech-to-text: transcribe() picks
#   between the existing hosted OpenAI Whisper API and a new self-hosted
#   faster-whisper model based on settings.TRANSCRIPTION_PROVIDER, so the
#   caller (workers/tasks/transcription.py) never needs to know which one
#   ran. MinIO storage / recording upload / mp4 keys are untouched — this
#   only replaces the transcription call itself.
# 2026-07-14 (later same day) — Bug fix in _transcribe_openai (line ~61):
#   was calling client.audio.transcriptions.create(..., task="translate"),
#   a parameter that doesn't exist on that endpoint (openai==1.57.4) — every
#   real call raised TypeError, pre-dating this session (carried over
#   verbatim from the original agent.py code during today's extraction).
#   Fixed to call client.audio.translations.create(...) instead — the
#   actual "always output English" endpoint. Found by testing with a real
#   synthesized .wav rather than trusting an earlier garbage-payload test.
# 2026-07-14 (again) — Added vad_filter=True to _transcribe_local's
#   model.transcribe() call. Real browser-mic audio (unlike clean
#   synthesized test audio) hallucinated a fully unrelated, plausible-
#   sounding sentence instead of the actual speech — a well-documented
#   Whisper failure mode on quiet/noisy/silent segments, not specific to
#   this integration. vad_filter strips non-speech audio before decoding.
# 2026-07-14 (again) — _local_model() now passes compute_type="int8" on
#   CPU instead of the "default" auto-selection (which was silently falling
#   back to float32 — the slowest option — since this CPU can't do
#   efficient float16). ~2.6x faster on a real test clip (9s -> 3.4s warm),
#   same transcript quality.
# 2026-07-14 (again) — Hallucination persisted on very short real-mic clips
#   even with vad_filter. Added condition_on_previous_text=False (stops one
#   uncertain segment's decode from anchoring the next) and post-decode
#   filtering on seg.no_speech_prob >= 0.6 (drops segments the model itself
#   flagged as probably-not-speech rather than trusting their hallucinated
#   text).
# 2026-07-14 (again) — Added _INITIAL_PROMPT (collections-domain vocabulary
#   hint) to _transcribe_local's model.transcribe() call — faster-whisper's
#   equivalent of OpenAI's `prompt` param, biases decoding toward terms that
#   actually come up in a visit instead of phonetically-similar guesses.
#   Deliberately left `language` unset (auto-detect) rather than hardcoded —
#   agents switch between Hindi/English per visit; a fixed hint would help
#   one and hurt the other. Confirmed on this session's OpenAI project: zero
#   audio-model access at all (whisper-1, gpt-4o-transcribe, and
#   gpt-4o-mini-transcribe all 403 model_not_found) — the hosted/hybrid-
#   fallback path isn't an option on this account regardless of model.
# 2026-07-14 (again) — Real regression from the two filters above: on real
#   mic audio they could drop every segment, making _transcribe_local
#   return "" — which the frontend's `if (text) onText(text)` check then
#   silently no-ops on, reading as "the mic button doesn't work" with no
#   error shown. Both filters are now fallbacks, not hard gates: if
#   vad_filter=True yields zero segments, retry once without it; if
#   no_speech_prob filtering would empty an otherwise non-empty result,
#   return the unfiltered segments instead. Either always returns the
#   model's best guess rather than risking silence.
#   Full detail + why: /changelog.md
# ───────────────────────────────────────────────────────────────────────────
"""
Speech-to-text for visit recordings.

transcribe(audio_bytes, filename) -> str is the contract both providers
implement (bytes in, English text out — task="translate" on both paths, so
a Hindi/bilingual recording is transcribed *and* translated in one call,
same behavior either way). Selected via settings.TRANSCRIPTION_PROVIDER:
  - "openai" (default) — hosted Whisper API, paid, needs OPENAI_API_KEY.
  - "local"             — self-hosted faster-whisper, free, needs the
                          `faster-whisper` package + ffmpeg on PATH.
"""
from __future__ import annotations

import os
import tempfile
from functools import lru_cache

from app.core.config import settings


@lru_cache(maxsize=1)
def _local_model():
    """Load the faster-whisper model once per process, not per request —
    model load is the expensive part; inference on an already-loaded model
    is what actually needs to happen per call."""
    from faster_whisper import WhisperModel
    # compute_type="int8" on CPU: without this, faster-whisper's "default"
    # tries float16 first, silently falls back to float32 on CPUs that can't
    # do efficient float16 (this one can't — confirmed by a runtime warning),
    # and float32 is the slowest option, not the most accurate one worth
    # paying for here. int8 is the standard CPU-deployment setting — a real,
    # meaningful speedup with negligible accuracy loss for this model size.
    compute_type = "int8" if settings.WHISPER_DEVICE == "cpu" else "default"
    return WhisperModel(
        settings.WHISPER_MODEL_SIZE, device=settings.WHISPER_DEVICE, compute_type=compute_type,
    )


# Domain-vocabulary hint (faster-whisper's equivalent of OpenAI's `prompt`
# param) — biases decoding toward terms that actually come up in a
# collections visit, and toward common Hindi/English code-switch words, so
# they're less likely to be misheard as something phonetically similar.
_INITIAL_PROMPT = (
    "Field agent voice note for a loan collections visit in India. Mixed Hindi and "
    "English speech. Common words: loan, EMI, payment, PTP, promise to pay, "
    "outstanding, case, customer, visit, agent, manager, office, bank, kal, abhi, "
    "paisa, ghar."
)


def _transcribe_local(audio_bytes: bytes, filename: str) -> str:
    # faster-whisper (via PyAV) decodes webm/mp4/wav directly — no transcoding needed.
    suffix = os.path.splitext(filename)[1] or ".mp4"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name
    try:
        # vad_filter strips silent/non-speech segments before decoding.
        # condition_on_previous_text=False stops one bad/uncertain segment's
        # decode from anchoring the next one, a known contributor to fluent-
        # sounding but unrelated ("hallucinated") output. language is left
        # unset (auto-detect) rather than hardcoded — agents switch between
        # Hindi and English per visit, so a fixed hint would help one
        # language and actively hurt the other. Customer.language_preference
        # exists in the data model and could seed a real per-visit hint
        # later (worth doing if accuracy on real data warrants it), but
        # isn't wired up yet — this call only knows it's transcribing a
        # voice note, not which case/customer it's for.
        segments, _info = _local_model().transcribe(
            tmp_path, task="translate", vad_filter=True,
            condition_on_previous_text=False, initial_prompt=_INITIAL_PROMPT,
        )
        segments = list(segments)   # the generator can only be walked once
        # vad_filter judges speech-vs-silence on raw energy before decoding
        # ever happens — on a quiet or very short real recording it can
        # decide there was no speech at all and hand back zero segments.
        # Retrying once without it is the only way to get anything back in
        # that case, same reasoning as the no_speech_prob fallback below:
        # an empty result reads as "broken," not "nothing to transcribe."
        if not segments:
            segments, _info = _local_model().transcribe(
                tmp_path, task="translate",
                condition_on_previous_text=False, initial_prompt=_INITIAL_PROMPT,
            )
            segments = list(segments)
        # vad_filter operates on raw audio energy, before decoding — it can
        # still hand the decoder a short/noisy real-microphone segment that
        # isn't actually intelligible speech. no_speech_prob is the model's
        # own confidence, *after* decoding, that a given segment wasn't
        # speech at all; segments it flags this way are exactly where
        # hallucinated, fluent-but-wrong text comes from, so they're
        # preferred to be dropped — but NOT unconditionally: on real-
        # microphone audio the model can run every segment above threshold
        # (nothing scored "confident"), and returning "" in that case reads
        # as "the mic button is broken" rather than "here's my best guess,
        # please check it." Falling back to the unfiltered decode when
        # filtering would empty the result entirely keeps this a quality
        # improvement, not a silent-failure trap.
        reliable = [seg for seg in segments if seg.no_speech_prob < 0.6]
        chosen = reliable if reliable else segments
        return " ".join(seg.text.strip() for seg in chosen).strip()
    finally:
        os.unlink(tmp_path)


def _transcribe_openai(audio_bytes: bytes, filename: str) -> str:
    import io
    from openai import OpenAI

    if not settings.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY not configured")
    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    audio_file = io.BytesIO(audio_bytes)
    audio_file.name = filename
    # translations.create (not transcriptions.create) is the endpoint that
    # always outputs English regardless of input language — there is no
    # task="translate" parameter on transcriptions.create; passing one
    # raises TypeError. This was a real, previously-uncaught bug: every
    # real call through this path failed until this fix.
    resp = client.audio.translations.create(
        model="whisper-1",
        file=audio_file,
        response_format="text",
    )
    return resp.strip()


def transcribe(audio_bytes: bytes, filename: str) -> str:
    if settings.TRANSCRIPTION_PROVIDER == "local":
        return _transcribe_local(audio_bytes, filename)
    return _transcribe_openai(audio_bytes, filename)
