# Call Round — recorded pre-visit calls that re-plan the agent's day

*Plan only, written 2026-09-18. No code changed. Everything under "What exists
today" was verified against the tree on that date; everything else is proposed.*

## The use case

Before starting the beat, the agent calls each borrower on today's route to ask
whether a visit is possible. The borrower answers in their own words —
*"come after 12"*, *"any time is fine"*, *"not today, Monday"*, *"I'm at the
shop, not at home"*. The product must:

1. record the call and turn it into a text transcript in which **every line is
   attributed to the right speaker** (agent or borrower);
2. extract the visit-scheduling outcome from what the **borrower** said;
3. let the agent confirm it with one tap;
4. re-optimise the day's route around the confirmed windows — automatically,
   not on a button — so that a borrower who said "after 12" is sequenced after
   12 and a borrower who said "any time" leaves the route unchanged.

Today the nightly route is built from case history and the agent's caseload;
the borrower's availability is not asked before the day begins.

## What exists today

| piece | state | where |
|---|---|---|
| Agent → borrower call from the app | **Built.** Twilio Voice SDK in the browser; the agent's leg is WebRTC, the borrower's leg is PSTN. | `frontend/src/hooks/useVoiceCall.ts`, `GET /agent/voice/token`, `POST /agent/voice/outbound` (returns `<Dial>` TwiML) |
| Call log with scheduling fields | **Built.** `available_from`, `available_until`, `blocked_until_date`, `best_time_to_visit`, `alternate_location_hint`, `visit_feasible_today`, `borrower_disposition`, `verbal_payment_date`, `payment_intent_signalled`, `ai_intel_summary`, `duration_seconds`, `outcome` (ANSWERED / NO_ANSWER / BUSY / DECLINED / SWITCHED_OFF / WRONG_NUMBER). Today the agent **types** these after the call. | `backend/app/models/call_log.py`, `POST /agent/cases/{id}/call-log` |
| Route re-optimisation from those fields | **Built, manual.** `reoptimize_beat` turns `available_from/until` into OR-Tools time windows, forces a case to be the next stop when its window closes within 45 minutes (`_URGENT_WINDOW_SECONDS`), moves `blocked_until_date` cases out of today, fetches the OSRM road matrix, solves, and rewrites `beat.ordered_case_ids`. The agent must press "Re-optimise". | `backend/app/services/case_service.py::reoptimize_beat`, `POST /agent/beat/reoptimize` |
| Nightly planner time windows | **Built, narrower.** `_contact_windows()` applies RBI contact hours plus `customers.preferred_contact_start/end`. It does **not** read call logs. | `backend/app/services/planner_service.py` |
| Speech-to-text | **Built as a seam.** Whisper via the OpenAI API or local faster-whisper, used for visit voice notes. | `backend/app/core/transcription.py` |
| Structured extraction from text | **Built as a seam.** One LLM gateway with JSON-mode output, provider by settings. | `backend/app/core/llm.py` |
| Borrower disposition vocabulary | **Built.** `BorrowerDisposition` (WILL_PAY / MAY_PAY / NO_COMMITMENT / REFUSES / HARDSHIP / DISPUTE) on both `call_logs` and `visits`; read by `recovery_risk` 2.2.0 as `latest_disposition`. | `backend/app/models/call_log.py` |
| Call recording, transcript, auto-trigger, call-round screen | **Not built.** No recording is made, no transcript exists, nothing links a call outcome to a re-plan, and `call_logs` on the live book are seed data only (1,089 rows). | — |

So the new work is: **record → transcribe with speakers → extract → confirm →
re-optimise automatically → a call-round screen before the beat starts**, on
top of the solver, the log, the STT seam and the LLM seam that already exist.

## How the speakers are told apart

**Do not recognise voices. Use the channels.**

A phone call has two directions of audio. A normal (mono) recording mixes them
into one track, like a single microphone in a room, and afterwards software has
to *guess* who said what. **Dual-channel recording** keeps the two directions on
two tracks of one stereo file: left channel = the agent's side, right channel =
the borrower's side. Twilio can do this because it sits in the middle of the
call and receives the two legs separately — the agent over the app's internet
connection, the borrower over the phone network — and it writes each leg to its
own channel instead of mixing them.

Consequences:

- **Speaker identity is a fact, not a prediction.** Anything on channel 1 is
  the agent, anything on channel 2 is the borrower — even when both talk at
  once, in a noisy street, or switching between Hindi and English mid-sentence.
- **Transcription is cleaner.** Each channel is transcribed on its own with
  word timestamps, so crosstalk does not garble words; merging the two by time
  gives the conversation in order, every line already labelled:

  ```
  00:04  AGENT     Namaste Sharma ji, main HDFC ki taraf se bol raha hoon…
  00:09  BORROWER  Haan, aaj aa sakte ho, par baarah baje ke baad.
  00:12  AGENT     Theek hai, dopahar mein aata hoon.
  ```

- **Extraction becomes trustworthy.** "Available after 12" is taken from
  BORROWER lines only; the agent asking "should I come at 12?" can never be
  mistaken for the borrower's answer.

In Twilio it is one attribute on the `<Dial>` the app already generates —
`record="record-from-answer-dual"` — plus a `recordingStatusCallback` URL to
receive the file. Same per-minute price as a mono recording.

**Diarization** (pyannote, or the "speaker labels" option of AssemblyAI /
Deepgram) is the alternative: software listening to a mixed track and
clustering voices by acoustic signature. It is right roughly 90–95% of the
time on clean audio and worse with code-switching and overlap. It is what a
*single microphone* needs — for example recording a doorstep conversation on
the agent's phone — and is the fallback only if a call recording ever arrives
mono. For a two-party phone call it is never needed; the network has already
separated the speakers.

## Speech-to-text for Hindi / Hinglish / regional languages

Calls will be in Hindi, Hinglish and regional languages. Candidates:

| provider | notes |
|---|---|
| Whisper `large-v3` (already wired) | Reasonable Hindi; weak on code-switching and Indian names; self-hostable. |
| Sarvam AI — Saarika | Built for Indian languages, priced in ₹, handles code-switching; the strongest fit on paper. |
| Google Cloud Speech (Chirp) | Broad Indic coverage; enterprise contract. |
| AssemblyAI / Deepgram Nova-3 | Good English, Hindi improving; both offer per-channel transcription natively. |

**Decide by measurement, not by brochure:** run 20 real calls through two
providers and compare word error rate on the *time expressions* — "baarah baje
ke baad", "shaam ko", "kal subah" must come out right; the rest of the
sentence matters far less. The seam in `core/transcription.py` already
switches provider by setting, so the bake-off costs one config value per run.

## From transcript to route change

1. **Extraction, not free text.** The merged transcript goes to the LLM in
   JSON mode with a fixed schema that maps one-to-one onto the existing
   `call_logs` columns:

   ```json
   {
     "availability": { "kind": "any | window | not_today | callback | unknown",
                       "from": "12:00", "until": null, "date": null },
     "alternate_location": null,
     "disposition": "WILL_PAY | MAY_PAY | NO_COMMITMENT | REFUSES | HARDSHIP | DISPUTE | null",
     "payment_intent": true,
     "verbal_payment_date": "2026-09-25",
     "confidence": 0.92,
     "evidence": [ { "t": "00:09", "speaker": "BORROWER", "text": "…baarah baje ke baad." } ]
   }
   ```

   Mapping: *"after 12"* → `available_from = 12:00`, `available_until =` end
   of contact hours; *"any time"* → `kind = any`, no window; *"not today,
   Monday"* → `blocked_until_date`; *"I'm at the shop"* →
   `alternate_location_hint`. The `evidence` quotes are stored so the agent
   and a compliance reviewer can see *why* a window was set. Only BORROWER
   lines may be cited as evidence for availability.

2. **The agent confirms; the model never commits.** After the call the Log
   Call form opens **pre-filled** with the extraction and the quotes. The agent
   taps Confirm or edits a field. A wrong window silently reorders a whole day,
   so a human tap between the model and the route is a rule, not a preference.
   Record whether the agent changed the prefill; after a few hundred calls the
   acceptance rate says whether auto-accept above a confidence threshold is
   safe.

3. **Re-optimise fires on confirm.** The existing `reoptimize_beat` runs — no
   new solver — and the beat screen says *"Route updated: Sharma moved to
   after 12:00, now stop 6"*. Windows that cannot be honoured are handled as
   the solver already does: forced next when closing soon, otherwise flagged
   for the agent.

4. **The morning call round.** A new screen before "Start beat": today's stops
   as a checklist, tap-to-call each, outcomes fill in as calls complete
   (answered / window / not today / no answer / callback), and **one**
   re-optimise when the agent taps Start. This is the flow in the use case. It
   also produces a fresh `borrower_disposition` reading before every visit —
   the observability research on this repo (`app/ml/artifacts/recovery_risk/
   2.1.0/observability/`) found that a pre-visit disposition reading is the
   single most valuable input the book lacks, so the call round feeds the
   recovery model as a side effect of scheduling.

5. **The nightly planner learns the pattern.** Today `_contact_windows()` reads
   only `customers.preferred_contact_start/end`. After repeated calls, persist
   "usually available after 12" onto the customer from call outcomes, so
   tomorrow's plan already sequences them late and the call round handles only
   exceptions.

## Rules to settle before building

- **Consent to record.** Indian practice and the RBI Fair Practices Code
  require the borrower to be told. Play a short disclosure at connect
  (*"this call is recorded for quality and training"*); if the borrower
  objects, the call continues **unrecorded** and the agent logs by hand. The
  product needs the transcript, not the audio: keep audio ~30 days, keep
  transcripts under the audit retention.
- **Storage and PII.** Recordings go to MinIO like visit photos and never stay
  on Twilio; transcripts are borrower data on `call_logs`, under the same
  tenant scoping as everything else in the manager router.
- **Nothing here may move a case to a different agent.** Call outcomes change
  the *order* of one agent's day (routing); allocation is untouched. Pin it
  with a test.
- **Cost.** Twilio dual-channel recording ≈ $0.0025/min, Whisper API
  ≈ $0.006/min, one LLM extraction ≈ ₹0.5 → a 3-minute call ≈ ₹2–3;
  200 calls/day per agency ≈ ₹500/day. Sarvam is cheaper for Hindi.

## Build plan

| phase | what | effort |
|---|---|---|
| 0 | Decisions: consent script, STT bake-off on 20 real calls, retention | 2 days |
| 1 | **Record the call.** Dual-channel `<Dial>`, recording-status webhook, pull the file into MinIO, `call_logs.recording_key` + `recording_consent` (migration) | 1–2 days |
| 2 | **Transcribe with speakers.** Split channels, STT per channel with timestamps, merge → `transcript` JSON (`speaker, t, text`), `transcript_language`, `stt_provider` (migration); runs as a Celery task on recording arrival | 2–3 days |
| 3 | **Extract → prefill.** JSON-schema LLM call through `core/llm.py`, confidence + evidence, `extraction` JSON on the log, Log Call form pre-filled, agent confirms; `prefill_changed` recorded | 2 days |
| 4 | **Auto re-optimise + call round.** Confirm → `reoptimize_beat` → "Route updated" toast; the morning call-round screen with a single solve on Start beat | 2–3 days |
| 5 | **Planner learns windows.** Persist recurring windows to the customer; manager sees call outcomes on the Field Plan and Live Map | 2 days |
| later | Real-time transcript during the call (Twilio Media Streams → streaming STT). Not needed for this use case — the outcome is only needed *after* the call | — |

Roughly two to three weeks for phases 1–4.

## Schema additions (proposed)

On `call_logs` — all nullable, no backfill:

| column | type | meaning |
|---|---|---|
| `recording_key` | String | MinIO object key of the dual-channel audio |
| `recording_consent` | Boolean | the borrower did not object to the disclosure |
| `recording_sid` | String | Twilio recording id, for reconciliation |
| `transcript` | JSON | `[{speaker: "AGENT"\|"BORROWER", t: seconds, text}]` |
| `transcript_language` | String(8) | detected language code |
| `stt_provider` | String(32) | which provider produced it |
| `extraction` | JSON | the schema above, as returned |
| `extraction_confidence` | Float | for the auto-accept decision later |
| `prefill_changed` | Boolean | the agent edited the prefill before confirming |

The scheduling fields themselves (`available_from` …) already exist and stay
the single source the solver reads.

## Sequence

```
agent taps Call on the call-round screen
  → app: Twilio Device.connect()
  → API: /voice/outbound returns <Dial record="record-from-answer-dual"
         recordingStatusCallback="/voice/recording">
  → disclosure plays; borrower stays on (consent) or objects (no recording)
  → call ends
  → Twilio POSTs recording status → API stores recording_key, enqueues transcribe
  → worker: fetch audio, split channels, STT per channel, merge, store transcript
  → worker: LLM extraction → extraction JSON + confidence
  → app polls the call log (or receives it on the next open): Log Call form
    opens PRE-FILLED with availability, disposition, evidence quotes
  → agent confirms (or edits) → POST /cases/{id}/call-log
  → API: reoptimize_beat → beat.ordered_case_ids rewritten
  → app: "Route updated: … moved to after 12:00"
  → after the last call, agent taps Start beat → one final solve
```

## Tests to write alongside

- Unanswered / voicemail: outcome recorded, no transcript, no window, route unchanged.
- "After 12" in Hindi, English and Hinglish → `available_from = 12:00`; "any time" → no window; "not today, Monday" → `blocked_until_date`.
- Evidence must cite BORROWER lines only; an AGENT line naming a time is not evidence.
- A window that makes the day infeasible → forced next or flagged, never a silent drop.
- Two calls on the same case the same morning → the latest confirmed outcome wins.
- Consent refused → no recording, no transcript, manual log still works.
- Windows are clipped to RBI contact hours (`core/geo.is_within_contact_hours`).
- Tripwire: no code path from a call outcome writes `Case.agent_id`.

## Not in scope

Voice-print speaker identification (unnecessary with dual-channel), real-time
coaching during the call, and any change to the nightly allocator's
assignment of cases to agents.
