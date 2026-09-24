# Unit economics and pricing: a rough first cut

*Business lead, 2026-09-24. The lead developer's capacity and cost model has not landed yet. This is my own rough version: every assumption is labelled, and it should be replaced by the lead's figures when they exist. Nothing here is measured on real traffic, because there is none. Usage volumes come from domain knowledge checked against the demo book (REVIEW.md §2). Unit prices are public list prices fetched today (sources at the end). FX is assumed at ₹88 per US dollar.*

## The answer first

- **As coded today, the product loses money on every agent.** Messaging alone costs about **₹3,100 per agent per month** on Twilio, while a plausible seat price is about ₹900. **Critical.**
- **With an Indian SMS gateway, variable cost falls to about ₹310–320 per agent per month**, and to about ₹240 if the post-visit borrower message is off by default.
- **A dedicated India deployment is a fixed cost of about ₹60k a month.** At pilot scale that dominates, so **pricing needs a per-lender platform fee plus a per-active-agent seat**. Seat-only pricing loses money below about 150 agents.
- **Recommended price:** ₹1.5 lakh a month per lender, plus ₹900 per active agent a month (₹750 from 500 agents), plus messaging at cost + 15% beyond an allowance. **Pilot: ₹6 lakh fixed for 90 days, up to 100 agents.** Gross margin is about 60% at 100–300 agents.
- **Five features cost more to run than they return**, and should change before a pilot (last section).

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

## 3. Fixed cost per dedicated India deployment (assumption)

| Item | ₹ / month |
|---|---|
| App server (API, workers, Redis), 4 vCPU / 16 GB | 12,000 |
| Managed Postgres with backups | 15,000 |
| Object storage and backups | 2,000 |
| Maps VM (OSRM, tiles, geocoder; north-India extract), 8 vCPU / 32 GB | 20,000 |
| Local speech-to-text capacity | 8,000 |
| Monitoring, TLS, e-mail, logs | 3,000 |
| **Total, up to about 150 agents** | **about 60,000** (about 75,000 up to 400 agents; about 1.2 lakh at 1,000) |

A shared multi-tenant India deployment would cut the fixed cost per lender by 3–5×. Price a dedicated deployment as a premium.

## 4. Cost per agent

Per agent per month: variable cost (recommended stack **₹314**; as coded **₹3,408**) plus a share of the fixed cost.

| Active agents | Fixed share | **Cost per agent (recommended)** | Cost per agent (as coded) |
|---|---|---|---|
| 50 | ₹1,200 | ₹1,514 | ₹4,608 |
| 100 | ₹600 | **₹914** | ₹4,008 |
| 300 | ₹250 | **₹564** | ₹3,658 |
| 1,000 | ₹120 | **₹434** | ₹3,528 |

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
- **Paid pilot: ₹6 lakh fixed for 90 days, up to 100 agents, one region, one or two agencies.** 50% is credited against year 1 if they convert. This covers about ₹1.8 lakh of infrastructure plus onboarding and support. Success criteria are agreed in writing up front (REVIEW.md §4).

**Margin at list price** (recommended stack):

| Active agents | Revenue / month | Cost / month | Gross margin |
|---|---|---|---|
| 50 | ₹1.95 lakh | ₹0.76 lakh | 61% |
| 100 | ₹2.40 lakh | ₹0.91 lakh | **62%** |
| 300 | ₹4.20 lakh | ₹1.69 lakh | 60% |
| 1,000 (seat ₹750) | ₹9.00 lakh | ₹4.34 lakh | 52% |

For comparison, with **seat-only pricing** (no platform fee), 100 agents gives **−2%** and 300 agents gives 37%. **As coded (Twilio)**, 100 agents cost ₹4.0 lakh a month against ₹2.4 lakh of revenue.

## 6. Features worth their running cost, and ones that aren't

| Feature | Running cost / agent-month | Verdict |
|---|---|---|
| **Post-visit SMS + WhatsApp to the borrower** | ₹67 (Indian) / about ₹1,850 (Twilio) | **Default off; per-lender setting.** It also discloses the outstanding amount to whoever holds the phone, including after a "deceased" visit |
| **Street-address lookup every 40 m** | Free today only by breaching OSM policy; about ₹14k on Google | **Cut** to check-in and visit events |
| **Double transcription** | ₹165 → ₹82 | **Fix.** Pure waste |
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
