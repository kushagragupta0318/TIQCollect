# 0011. Offline outbox: capture time, replay and its bounds

**Status:** Accepted by the coordinator (tiqcollect-0c) on 2026-09-29. Written by
tiqcollect-37 (P7, task I02). The number may shift at integration (0009 and 0010 are on other
branches).

**Decisions on the open questions (coordinator):**

1. **48 h, one window for visits and the GPS trail.** The location path changes from 24 h to
   48 h. The change is recorded on the claims board and pinned by a test.
2. **The read cache is in I02:** today's beat and case detail, through runtime caching of
   `/agent/beat` and `/agent/cases/{id}`.
   - Only the agent's own cases are cached.
   - Entries expire at the end of the IST day and are invalidated by any successful mutation.
   - The cache is not encrypted, the same as the outbox.
3. **No recordings in the pilot outbox.**
4. **The pilot's encryption stance** is §8's recommendation, and it goes on the pilot risks list.
5. **Do-Not-Contact is judged at sync time,** against the current list. The client's "needs
   attention" entry explains why the item was refused.
6. **Client tests use an in-memory storage double.** A Playwright smoke test on real IndexedDB
   comes later.
7. **Order of work:** the outbox, the capture-time judgement and the signals first, then the
   read cache. The per-device public-key column is deferred.

## Context

- Agents lose signal in the field. Today `RecordVisitPage` refuses to submit while offline
  (`canSubmit`), drafts only 24 text fields in `localStorage`, and drops photos, signature
  and recordings (Blobs cannot be JSON'd).
- The service worker caches the app shell only (`pwaConfig.ts`, `runtimeCaching: []`). No API
  data is available offline.
- The server judges everything at **receipt** time: contact hours (`is_within_contact_hours(now)`),
  case access (`agent_case_or_404`: assigned now, or on *today's* beat) and the duplicate guard
  (a 15 s window, `_DUPLICATE_SUBMIT_WINDOW_SECONDS`). A visit made at 18:30 and synced at 20:30
  is refused as out of hours, and after the 20:00 allocation it may 404 on access too.

## Decision (proposed)

### 1. What is queued

- **Queued:** visits (with the PTP set on the visit form, the borrower stance and the notes),
  their photos and signature, the GPS trail, and call logs. Recordings upload only while online
  (decision 3). Offline, the recorder is disabled with a message.
- **Never queued: payments.** The payment step needs the borrower's OTP to reach the server.
  Offline, the form disables PAID_FULL, PART_PAID and PART_PAID_PTP with "Needs signal: the
  borrower's OTP must reach the server". The server's existing PENDING_VERIFICATION path is not
  used offline.
- **Every submit goes through the outbox, online or not.** There is one code path. Online, it
  flushes at once and behaves as today.

### 2. Idempotency

- The client stamps each item at capture with a `client_submission_id` (UUIDv4).
- The server stores it with a partial UNIQUE on `(agent_id, client_submission_id)` on visits and
  PTPs, and optionally on call logs. A repeat returns the existing row with the same body. The
  migration is requested from 43.
- Items without the field keep today's behaviour. Older clients and the Command Center's
  manager login are unaffected.

### 3. Which time the server judges

- The client sends `captured_at`, taken from the device clock when the agent pressed Submit.
- **Live item:** `|now − captured_at| ≤ 120 s`, the same tolerance as
  `location_service.FUTURE_TOLERANCE_SECONDS`, moved to one definition. It is judged exactly
  as today: `check_in_time = now`.
- **Late item:** anything older, judged at `captured_at`:
  - **Contact hours:** `is_within_contact_hours(captured_at)`. A refusal still writes
    CONTACT_HOUR_VIOLATION_ATTEMPT, with `attempted_at = captured_at` and `offline: true`.
  - **Geo-fence:** unchanged. The coordinates were always the capture-time ones.
  - **Case access:** the case must be assigned to the agent now, or be on the agent's beat for
    the IST date of `captured_at`. This is one new parameter on the one rule:
    `scope.agent_case_or_404(..., on_day=)`. Without it, every visit synced after the 20:00
    allocation that moved its case would 404. The photo-upload-URL route uses the same rule.
  - **No `sync_assignee` for a late item.** A replay never takes a case over.
  - **`check_in_time = captured_at`.** The receipt time is the existing `created_at`, so the
    sync lag is derivable and needs no new column.

### 4. Bounds on a client-supplied time

A refusal carries a typed `ErrorCode`, and the client moves the item to "needs attention".

| Check | Rule | Refusal |
|---|---|---|
| Future | `captured_at > now + 120 s` is refused, not clamped. Clamping would judge contact hours at sync time again | CAPTURE_TIME_IN_FUTURE |
| Age | `captured_at < now − OFFLINE_MAX_AGE_HOURS`. **Proposed: 48 h**: a whole field day offline plus the next evening | CAPTURE_TOO_OLD |
| Device | The token's `device_id` (server-signed, and bound through the A09b secret) must equal the item's `device_id`, and the agent's bound, not-unbound `AgentDevice`. `captured_at ≥ bound_at` | DEVICE_MISMATCH |
| Order | The item's `device_seq` (strictly increasing per device, allocated in one IndexedDB transaction) must exceed the device's `last_outbox_seq`, and `captured_at ≥ last_outbox_captured_at − 120 s`. A repeat `client_submission_id` is answered before this check | OUT_OF_ORDER |

- **Honest limit.** Someone who controls the phone can forge a consistent story (time, sequence,
  GPS) inside these bounds. The bounds cap the window and catch naive backdating; detection is §6.
- **Same window for the GPS trail.** `location_service.MAX_AGE_HOURS` is 24 today, and the
  client trail queue holds 8 h. A visit older than the trail cannot be corroborated by
  TRAIL_CONTRADICTS_VISIT. Proposal: one `OFFLINE_MAX_AGE_HOURS` for both, which changes the
  location path from 24 h to 48 h.

### 5. Side effects of a late item

- **Case outcome transition:** applied only if no newer visit exists on the case
  (`check_in_time > captured_at`) and the case has not closed since. Otherwise the visit is
  stored as history, the case is untouched, and this is logged.
- **Agent's last known position:** updated only if `captured_at > agent.last_location_update`.
- **`current_month_visits`:** incremented only if `captured_at` falls in the current IST month.
  The monthly snapshot runs at 00:00 on the 1st.
- **Borrower SMS/WhatsApp notice:** sent only if the sync falls inside contact hours and on the
  capture's IST day. Otherwise it is skipped and logged. An evening sync must not text a
  borrower at 21:00.
- **PTP:** the server has no check on the promise date against today, so none was relaxed and
  none was added. The 00:05 lifecycle handles a date that has already passed.
- **Beat reoptimisation:** not called on replay.
- **Audit and events:** VISIT_RECORDED and `visit.recorded` gain `captured_at`, `lag_s` and
  `offline`.

### 6. Manager signals (`fraud_service`, rules)

- **LATE_SYNC (LOW):** lag > 2 h. Informational only.
- **SYNC_WITHHELD (MEDIUM):** the agent's GPS pings reached the server
  (`AgentLocation.received_at`) at least 10 minutes after `captured_at` and at least 5 minutes
  before the visit arrived. The check is per agent: location rows do not record their device.
  - This holds because the client flushes the visit outbox **before** each location batch: a
    genuinely offline visit cannot trail pings that got through.
  - A visit held back by a server error while pings got through is the known false positive,
    hence MEDIUM. The trail is never held back for the outbox: a lone worker's position comes
    first.
- **TRAIL_CONTRADICTS_VISIT:** unchanged. It already tests the claimed position against the trail.

### 7. Client

- **Storage and scoping:**
  - One IndexedDB database per session slot (`slotKey("tiq-outbox")`). Every item carries its
    user and device, and is sent only under that login.
  - Object stores: `items` (JSON, state machine), `blobs` (media), `meta` (`device_seq`).
  - `device_seq` is `max(last + 1, Date.now())`, so a phone whose storage was cleared never
    restarts below what the server holds.
  - This fixes P0-A6. `locationReporter`'s key becomes slot and login (`<slotKey>:<userId>`).
    The old unslotted key is dropped (at most 8 h of trail). Its queue now holds 48 h.
- **Item states:**
  - `queued → media_uploaded (keys recorded per blob) → visit_posted → ptp_posted → done`
    (blobs deleted on done).
  - Or `needs_attention`, which keeps the server's message. Only the agent discards it.
  - Each step is resumable, and none repeats once acknowledged.
- **Errors:**
  - Network, 5xx, 429, and 401 after a failed refresh: retry with backoff (capped at 5 min).
  - A typed 4xx: `needs_attention`. Nothing is dropped silently.
- **Flush triggers:** `online`, `visibilitychange`, app start, every 60 s while items are
  pending, and a manual "Sync now".
  - One flusher per slot across tabs, through `navigator.locks`.
  - Background Sync is out of scope. It is Chrome-only and would put tokens in the service
    worker. The queue flushes while the app is open.
- **Caps:**
  - 60 items or 200 MB, proposed. The header shows "N pending · M MB".
  - At the cap, new captures are refused with a message.
  - `navigator.storage.persist()` is requested at the first queued item.
- **Identity:**
  - Items are bound to (user, device) and never replay under another login.
  - Logout with items pending asks first.
  - A CAPTURE_DEVICE_MISMATCH refusal parks the item and deletes its photos: they can never
    be delivered.
  - A payment visit is sent live and never queued, and carries only its
    `client_submission_id`. A retry after a lost response returns the stored row.
  - `Visit.device_id` stores the login's bound device id, the one the token signs, not the
    user-agent string (coordinator, 2026-09-29).

### 8. Encryption at rest

- **A non-extractable WebCrypto key kept in the same IndexedDB: no.**
  - Chrome persists the key material in the same profile's IndexedDB files.
  - "Non-extractable" only blocks the JS export API, so it protects nothing against access to
    the device or its files. It would be theatre.
- **Real option: hybrid encryption to a per-device server public key.**
  - Each item and blob gets a fresh AES-GCM key, wrapped to the server's key.
  - Nobody on the phone can read the queue, and it survives an offline restart.
  - Cost:
    - Media can no longer PUT straight to MinIO, because the objects would be ciphertext. It
      needs a server-side decrypt step: a new endpoint on the storage seam.
    - The agent cannot open queued items, only see counts and case numbers.
  - Size: M on top of I02.
- **Recommendation for the pilot:**
  - No app-level crypto.
  - Require Android file-based encryption and a screen lock in the pilot runbook (MDM).
  - Minimise what is stored, purge on success, keep the 48 h maximum age, and wipe on unbind.
  - Hybrid encryption is a follow-up if the lender's infosec asks.

### 9. Prerequisite outside I02's wording: offline read cache

- With no API data offline, an agent who loses signal before opening a case cannot reach the
  visit form at all. The outbox then helps only when signal drops mid-form.
- **Proposed:**
  - Cache today's beat and the case details as they are fetched (network-first, with an "as of
    HH:MM" banner).
  - Also cache the contact hours, so the client can warn at capture time.
  - Purge at logout and at the IST day change.
- This puts borrower PII on the device, which is the same at-rest question as §8.

**Decided (coordinator, 2026-09-29): an in-app cache, not the service worker's runtime
caching.**

- Workbox caches by URL, not by the Authorization header, and there is one service worker per
  origin. Offline, the simulator's frames, or a second login on the same phone, would read the
  previous agent's beat and cases.
- The cache is in IndexedDB, in the outbox's slot database, keyed by user.
- It is written on every successful read and used only when the network fails.
- Entries expire at the end of the IST day.
- A case's entry is invalidated by any successful mutation of that case.
- The cache is purged at logout, on a change of login in the slot, and on unbind.

## Backend changes

- **From 43 (migration):**
  - `visits.client_submission_id`, `ptps.client_submission_id` (+ `call_logs`), each UUID with
    a partial UNIQUE on `(agent_id, client_submission_id)`.
  - `agent_devices.last_outbox_seq BIGINT` and `last_outbox_captured_at TIMESTAMPTZ`.
- **In this lane:**
  - Optional `client_submission_id`, `captured_at` and `device_seq` on RecordVisitRequest,
    SetPTPRequest and LogCall.
  - One capture-time module holding the tolerance, the maximum age and the checks.
  - `scope.agent_case_or_404(on_day=)`.
  - The late path in `visit_service` and `set_ptp`.
  - A token-device dependency.
  - The two fraud rules.

## Tests (behaviour)

- **Replay:**
  - A repeated `client_submission_id` returns the same row: one visit and one PTP.
  - A visit captured at 18:30 and synced at 20:30 is accepted.
  - One captured at 19:10 is refused, and writes the audit row.
- **Bounds:**
  - Future plus 121 s, older than 48 h plus 1 s, and exactly at each bound.
  - Another device's token.
  - A lower sequence number.
- **Access and state:**
  - A case reallocated at 20:00 is still accepted for its capture day, and the assignee is
    unchanged.
  - A newer visit exists, so the case state is untouched.
  - Month-boundary counter.
  - No SMS at 21:00.
- **Signals:** SYNC_WITHHELD raised, and not raised when no ping arrived.
- **Client (vitest, fake-indexeddb *or* a hand-rolled in-memory IndexedDB double: no new
  dependency):**
  - The state machine and the error classes.
  - The caps.
  - Slot isolation (P0-A6).
- **Mutation checks:** break the age bound and the device check on purpose, and confirm a test
  fails.

## Open for the coordinator

1. The maximum age: 48 h, and one window for visits and the GPS trail (location goes from 24 h
   to 48 h)?
2. Is the read cache (§9) in I02's scope?
3. Queue recordings too? They are evidence, at about 200 KB a minute.
4. Encryption: accept the pilot recommendation (§8)?
5. Do-Not-Contact on a late item: keep today's refusal as judged at sync time? This is simple,
   and could lose evidence of a real visit.
6. Client tests need IndexedDB, and jsdom has none. Add `fake-indexeddb` (a devDependency only,
   pinned exactly, not in either image's runtime), or write the queue against a small storage
   interface with an in-memory double? My default is the interface and the double: no new
   dependency, with the IndexedDB adapter kept thin.
