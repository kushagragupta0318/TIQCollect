# Unit economics and pricing: a rough first cut

*Business lead, 2026-09-24. Originally written before the lead developer's capacity and cost model existed; **reconciled below (§0)** against `docs/RESTRUCTURE-PLAN.md` §4 on branch `lead-structure` (bb, 2026-09-24), which grounds its volumes in code (`max_cases_per_day`, demo allocation runs, actual table sizes) rather than in my industry assumption. Nothing here is measured on real traffic, because there is none. FX is assumed at ₹88 per US dollar throughout, by both models independently.*

## 0. Reconciled against the lead developer's cost model

bb's model (`RESTRUCTURE-PLAN.md` §4.4) and mine were built independently, from different sources (bb from code call-sites and table sizes; mine from an industry visit-rate assumption). They land within 10% of each other on unit prices and about 40% apart on the total, for one identified reason.

| | Mine (below) | bb's (`RESTRUCTURE-PLAN.md` §4) | Agreement |
|---|---|---|---|
| Twilio SMS price | ₹7.32/msg ($0.0832 × 88) | ₹7.3/msg | **Same source, same number** — good cross-check |
| WhatsApp price | ₹0.136 (with GST) | ₹0.13 | Same, within rounding |
| Indian DLT SMS | ₹0.15 | ₹0.20 | Both assumptions pending a vendor quote; use bb's as the more conservative |
| Visits per agent per day | **12** (industry range, assumption) | **15** (`max_cases_per_day` cap in code, `models/agent.py:54`; demo allocation runs average 14.3) | **This is the gap.** bb's is grounded in what the allocator actually assigns; mine is a generic FOS estimate below the code's cap. **Use bb's 15** |
| SMS + WhatsApp / agent-month, Indian gateway | ₹103 | ₹173 (₹112 SMS + ₹61 WA) | Even scaling mine to 15 visits/day gives only ₹128 — bb's ₹173 likely also counts a message I didn't (a plausible extra OTP resend or confirmation send); treat bb's as the safer upper bound |
| Transcription | ₹82 (single pass, defect fixed) | ₹155 (as-coded, double transcription, 15 visits/day) | bb models today's code as it runs now; mine models it after the double-transcription defect (§6) is fixed. Both are right for what they describe |
| Fixed infrastructure | ₹60k/month flat estimate, no per-scale detail | ₹132–198/agent (scales 5,000 → 160 agents), fully costed (API, Postgres, Redis, self-hosted OSRM, backups) | **Use bb's** — it is the more careful estimate and gives a number at every fleet size, not just one |
| **Total, DLT gateway + Groq, 1,000 agents** | ₹434 | **₹483** | Within 10% |
| **Total, Twilio, 1,000 agents** | ₹3,528 | **₹4,483** | Both agree Twilio is 9–11× the DLT-gateway cost and dominates the bill either way |

**Reconciled pricing floor: use bb's numbers.** They are grounded in the code (the allocator's actual cap, measured table sizes) rather than in an outside industry rate, and they are the more conservative (higher) figure. At pilot scale (~100–160 agents, no economies of scale yet), the reconciled variable cost per agent is:

| Scenario | ₹ / agent / month | At 100 agents |
|---|---|---|
| **DLT gateway + Groq (recommended)** | **≈ ₹549** (₹198 infra + ₹4 storage + ₹19 LLM + ₹155 transcription + ₹112 SMS + ₹61 WA) | ₹54,900/month |
| Twilio (as coded) | ≈ ₹4,549 | ₹4.55 lakh/month |

This **replaces** the "recommended stack ₹314" and "as-coded ₹3,408" figures in §4 below — those undercounted transcription and used a lower visit rate. The rest of this document (§1–§6) is kept for its per-line detail and the "not worth its cost" findings, which still hold; only the headline totals below are superseded by the reconciled figures in this section.

## The answer first (reconciled)

- **As coded today, the product loses money on every agent.** Messaging alone runs **₹4,100–4,500 per agent per month** on Twilio, against a plausible seat price of ₹900. **Critical.**
- **With an Indian DLT SMS gateway, the reconciled variable cost is about ₹480–550 per agent per month** (₹483 at 1,000 agents, ₹549 at pilot scale before economies of scale). Both independent models agree the SMS/WhatsApp line is what moves this number; the LLM choice moves it by at most ₹265 (bb) / ₹300 (mine).
- **Fixed infrastructure is per-agent, not a flat deployment cost** (bb's correction to my earlier "₹60k flat"): ₹132–198 per agent depending on fleet size, self-hosted OSRM included. **Pricing still needs a per-lender platform fee plus a per-active-agent seat**, because at pilot scale (100–160 agents) infra alone is ₹20–32k/month before any messaging or LLM cost.
- **Recommended price, reconciled:** ₹1.5 lakh a month per lender, plus ₹900 per active agent a month (₹750 from 500 agents), plus messaging at cost + 15% beyond an allowance. At the reconciled ₹549 floor and this price, **gross margin is about 77% at 100 agents, 66% at 300, 54% at 1,000** (§5) — better than my original estimate, because bb's infra figures scale down faster than my flat ₹60k assumption did.
- **Five features cost more to run than they return**, and should change before a pilot (§6).

## 1. Usage per active agent per month

| Driver | Value | Basis |
|---|---|---|
| Working days | 26 | 6-day week; the nightly planner skips Sundays (assumption) |
| Visits per day | 12 | Industry range 10–20 for urban FOS (assumption). Demo feed days: p50 11, p90 14 |
| Visits per month | 312 | |
| Payments collected per day | 3 → 78 per month | Assumption. The demo feed shows p50 5, which is flattering |
| Non-payment visits | 234 per month | |
| OTP resends | +15% | Assumption |
| Voice notes | Half of visits, 1 minute each | Assumption |
| Evidence per visit | About 1 MB | 3 photos at 1280×720 JPEG q0.88 (no resizing), a signature, and 2 short audio clips (code inventory) |
| GPS | 1 post every 15 s (about 1,900 per shift), stored only for moves of 25 m or more, kept 90 days | Code |

## 2. What each thing costs (per active agent per month)

**Messaging.** Today's code sends the following, verified in `visit_service.py` and the payment service:
- **Every payment:** 1 OTP SMS, 1 receipt SMS and 1 receipt WhatsApp.
- **Every non-payment visit:** 1 SMS and 1 WhatsApp to the borrower saying "ABC Bank's field agent … visited … Outstanding: Rs.X".

That comes to **402 SMS and 312 WhatsApp** per agent per month.

| Route | Unit price | Monthly |
|---|---|---|
| **Twilio (as coded)** | SMS $0.0832 (₹7.32); WhatsApp ₹0.115 Meta fee + $0.005 Twilio fee | **₹3,114** |
| Indian DLT gateway plus Indian WhatsApp partner | SMS ₹0.15; WhatsApp ₹0.115 + 18% GST | **₹103** |
| Indian gateway, no post-visit message | OTP and receipt SMS only | **₹25** |

Commercial SMS to Indian numbers must be sent under TRAI's DLT registration (sender ID plus approved templates), whichever gateway sends it. The gateway choice is a pure cost decision. WhatsApp is on Twilio's **sandbox** number today, so it reaches nobody real.

**LLM** (1 visit report per visit; a strategy brief on about 2 in 3 visits, cached for 1 hour; 1 smart-order call a day):

| Model | Price per million tokens (input / output) | Monthly |
|---|---|---|
| Groq gpt-oss-120b (current) | $0.15 / $0.60 | **₹21** |
| Claude Haiku 4.5 | $1 / $5 | ₹161 |
| Claude Sonnet 5 (F01's recommendation for agents) | $2 / $10 | ₹323 |

Manager-side AI (briefing at most hourly, insights on click) adds under ₹5 per agent. **At field volume, only cheap models are affordable.** Keep frontier models for bank-side features priced as add-ons.

**Speech to text** (API Whisper, $0.006 a minute): **₹165**, because each voice note is transcribed **twice**, once while the agent waits and again in the background. Fixing that gives ₹82. Local Whisper moves the cost into the server bill instead.

**Storage.** About 0.3 GB of evidence a month. At an S3-class price of $0.025 per GB-month, that is ₹8 a month after a year and ₹24 after three. It is negligible, but **nothing ever deletes media**, and a DPDP retention schedule is needed anyway.

**Support** (assumption): one L1 support person at ₹50k a month per 500 agents is **₹100** per agent. Password resets, device changes and "my visit won't submit" calls are the real per-agent cost of a field app.

**Maps and routing: the hidden bill.** Today the app uses three free public services whose usage policies forbid production volume:
- **OpenStreetMap tiles.**
- **Nominatim address lookup,** called **every 40 m of movement**. That is about 1,250 lookups per agent per day on a 50 km beat.
- **OSRM's public demo routing server,** called twice per agent at night and again after **every visit submit** (automatic re-optimise).

The obvious "fix" is a paid Google API, and it would be ruinous:
- **Address lookup:** about 32k lookups per agent per month at about $5 per 1,000 is about **₹14,000 per agent per month**.
- **Routing:** a 15×15 matrix about 13 times a day would add **tens of thousands of rupees per agent per month**.

The right answer is self-hosting maps, routing and geocoding on one VM in the fixed cost below, and **looking up an address only at check-in and at a visit**.

## 3. Fixed cost per dedicated India deployment

**Superseded by bb's per-agent figures** (§0): my flat ₹60k/month estimate is replaced by bb's fully costed, scale-dependent number — ₹198/agent at ~160 agents, ₹132/agent at 1,000, ₹67/agent at 5,000 (API, workers, managed Postgres, Redis, **self-hosted OSRM** — the free public routing server this repo defaults to cannot carry production load — and backups). The components are the same ones I listed (app server, Postgres, object storage, a maps VM, speech-to-text capacity, monitoring); bb's version sizes each one and scales it with fleet size instead of quoting one flat number. A shared multi-tenant India deployment would cut this further; price a dedicated deployment as a premium.

## 4. Cost per agent (reconciled)

Per agent per month: bb's variable cost (§0) plus a share of bb's per-agent infrastructure figure.

| Active agents | Infra (bb) | **Cost per agent, DLT gateway + Groq** | Cost per agent, Twilio |
|---|---|---|---|
| ~100–160 | ₹198 | **≈ ₹549** | ≈ ₹4,549 |
| 300 | ₹132 (interpolated) | **≈ ₹483** | ≈ ₹4,483 |
| 1,000 | ₹132 | **₹483** | ₹4,483 |
| 5,000 | ₹67 | **₹418** | ₹4,418 |

(My earlier "recommended ₹314 / as-coded ₹3,408" figures undercounted transcription and used a lower visit rate; superseded above.)

## 5. Pricing

**Who pays.** In the standalone plan the **lender** buys: it gets oversight, compliance evidence and agency management. Its agencies use the field app. The agencies' economics make the case *(assumptions)*:
- An agency earns roughly 5–15% commission on what its agents collect.
- An agent collects roughly ₹2–5 lakh a month.
- So the agency earns about ₹15–40k per agent-month.
- A ₹900 seat is 2–6% of that. It pays for itself if the app lifts collections by a few percent or removes one fake visit a week.

| Model | Verdict | Why |
|---|---|---|
| **Platform fee per lender + seat per active agent** | **Recommend** | Revenue follows cost, which is fixed plus per-agent. "Active" means at least one visit in the month, so a lender never pays for leavers (attrition is high) |
| Per placed account per month (for example ₹4–6) | Offer as an alternative | Banks think in accounts and some procurement teams prefer it. It is harder to match to our cost |
| % of recovery | **Avoid** as the main model | Volatile; bank procurement resists it; and a vendor paid per rupee recovered is a bad look for a product sold on RBI fair-practice compliance. At most a small pilot success kicker |
| Per bank, flat | Only for very large lenders | Caps the upside and hides the per-agent SMS cost |

**Proposed list prices** (first cut, to test with 2–3 prospects; Indian FOS seat prices are not published, and my assumed market band is ₹500–1,500):
- **Platform fee: ₹1.5 lakh a month per lender** (dedicated India deployment, exports and MIS, compliance evidence, bank portal when shipped).
- **Seat: ₹900 per active agent a month** (₹750 from 500 agents; do not go below ₹700 unless messaging is trimmed).
- **Messaging:** 300 messages per active agent included; beyond that, at cost + 15%.
- **Add-ons later:** Hindi voice-to-report, bank copilot. Price them separately so frontier-model costs never sit inside the seat.
- **Paid pilot: ₹6 lakh fixed for 90 days, up to 100 agents, one region, one or two agencies.** 50% is credited against year 1 if they convert. This covers about ₹1.65 lakh of infrastructure and messaging (reconciled ₹549/agent × 100 agents × 3 months) plus onboarding and support. Success criteria are agreed in writing up front (REVIEW.md §4).

**Margin at list price, reconciled** (DLT gateway + Groq, bb's per-agent cost from §4):

| Active agents | Revenue / month | Cost / month | Gross margin |
|---|---|---|---|
| 100 | ₹2.40 lakh | ₹0.55 lakh | **77%** |
| 300 | ₹4.20 lakh | ₹1.45 lakh | **66%** |
| 1,000 (seat ₹750) | ₹9.00 lakh | ₹4.83 lakh | **54%** |

Margins are **better** than my original estimate once bb's infrastructure figures (which scale down with fleet size) replace my flat ₹60k assumption. For comparison, **as coded (Twilio)**, 100 agents cost ₹4.55 lakh a month against ₹2.40 lakh of revenue — still a clear loss, and the reason the DLT gateway switch is priced as urgent, not optional.

## 6. Features worth their running cost, and ones that aren't

| Feature | Running cost / agent-month | Verdict |
|---|---|---|
| **Post-visit SMS + WhatsApp to the borrower** | ₹67–173 (Indian, mine–bb) / about ₹1,850–4,100 (Twilio) | **Default off; per-lender setting.** It also discloses the outstanding amount to whoever holds the phone, including after a "deceased" visit |
| **Street-address lookup every 40 m** | Free today only by breaching OSM policy; about ₹14k on Google | **Cut** to check-in and visit events |
| **Double transcription** | ₹155 (bb, as-coded, 15 visits/day) → ₹82 (mine, fixed) | **Fix.** Pure waste — roughly halves the transcription line either way |
| **AI visit report on every submit** | ₹21 (Groq) / ₹323 (Sonnet) | Keep on a cheap model, but **move it off the submit path**. It causes the 15 s timeout and duplicate visits (JOURNEYS §1a), and nobody reads it at the door |
| **Automatic re-optimise after every visit** | Free self-hosted; ruinous on a paid API | Keep only on self-hosted OSRM |
| GPS post every 15 s from login | Data trivial; battery real | Track only while checked in (also a DPDP point) |
| OTP SMS + receipt | ₹25 | **Keep.** This is the product's integrity |
| Hindi voice-to-report (H14, later) | About ₹80 speech-to-text + ₹20 LLM | Worth it **only** if it pre-fills the form in Hindi; price it as an add-on |

## Sources for unit prices

- [Twilio SMS pricing, India](https://www.twilio.com/en-us/sms/pricing/in): $0.0832 per outbound SMS
- [WhatsApp Business API rate card, India (whautomate)](https://whautomate.com/whatsapp-business-api-pricing-india) and [AiSensy](https://aisensy.com/pricing): ₹0.115 per utility/authentication message, plus 18% GST
- [MSG91 SMS pricing](https://msg91.com/in/pricing/sms), [Message Central](https://www.messagecentral.com/en-in/blog/sms-otp-pricing-india), [Gupshup (Codingclave)](https://codingclave.com/blog/gupshup-sms-pricing-india-2026): ₹0.10–0.20 per transactional SMS, DLT mandatory
- [Groq gpt-oss-120b](https://console.groq.com/docs/model/openai/gpt-oss-120b) / [CloudZero](https://www.cloudzero.com/blog/groq-pricing/): $0.15 / $0.60 per million tokens
- Claude model prices: Anthropic's current price list (Sonnet 5 $2/$10, Haiku 4.5 $1/$5 per million tokens)
- Everything else (FX, infrastructure, support, seat band, agency commission, agent collections) is an assumption, labelled where used
