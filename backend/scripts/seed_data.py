"""
TIQCollect — NPA Recovery Seed Data
=====================================
Data  architecture
-----------------
  ABC Bank  ──► daily CSV batch  ──► ingest_daily.py  ──► Customer + Loan records
                                                       ──► UNASSIGNED Cases (pool)
                                                       ──► 8 PM allocator assigns to agents
  Agency internal (what this seed builds):
    cases           – allocated to agents from the bank batch
    visits          – field reports submitted by agents after each visit
    payments        – cash/UPI collected during visits
    ptps            – promise-to-pay commitments from customers
    agent_leaves    – attendance / leave log (3–5 agents per business day)
    agent_performance – monthly snapshots per agent
Generated volumes
-----------------
  Managers   : 2 (M1 → 25 agents, M2 → 5 agents)
  Agents     : 30 (agent001–030)
  Customers  : 300  (~35% SMA-1 30-60 DPD / ~35% SMA-2 60-90 DPD / ~30% NPA 90+ DPD)
  Loans      : 350  (same DPD distribution — SMA-1, SMA-2, NPA)
  Cases      : ~420 historical  +  15 demo (fixed, deterministic for agent002)  +  10 bank-today
  Visits     : ~900 historical  +  today demo activity  +  historical visits for all 15 demo customers
  Leaves     : ~630 records (4 agents × 156 business days over 6 months)
  Performance: 180 monthly snapshots (30 agents × 6 months)
Run: python -m scripts.seed_data
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
# Windows UTF-8 fix — must be before any print statements
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
import logging
import uuid
import random
import math
from collections import defaultdict
from datetime import datetime, date, timedelta, timezone
# Silence all SQLAlchemy logs regardless of engine.echo setting
logging.getLogger("sqlalchemy").setLevel(logging.CRITICAL)
from sqlalchemy import text, func
from app.core.database import SessionLocal, engine
engine.echo = False  # force off regardless of settings.DEBUG
from app.core.database import Base
from app.core.config import settings
from app.core.security import hash_password
import app.models  # noqa: register all models
from app.models.user import User, UserRole
from app.models.agent import (
    Agent, AgentTier, AgentStatus, AgentSpecialization, AgentPerformance,
)
from app.models.customer import Customer
from app.models.loan import Loan, LoanType, DPDBucket, LoanStatus, RecoveryPotential
from app.models.case import Case, CaseStatus, CasePriority, EscalationReason
from app.models.repayment_snapshot import TRIGGER_SEED
from app.services.repayment_service import RepaymentService
from app.models.visit import Visit, VisitOutcome, PersonMet, DefaultReason, NotMetReason
from app.models.payment import Payment, PaymentMode, PaymentStatus
from app.models.ptp import PTP, PTPStatus
from app.models.beat import Beat, BeatStatus
from app.models.call_log import CallLog, CallOutcome
from app.models.audit_log import AuditLog, AuditAction
from faker import Faker
fake = Faker("en_IN")
Faker.seed(42)   # deterministic fake data across re-seeds
random.seed(42)  # deterministic random calls across re-seeds
# ─── Config ───────────────────────────────────────────────────────────────────
N_MANAGERS      = 2
N_AGENTS        = 18          # 15 under manager1, 3 under manager2
N_CUSTOMERS     = 400         # delinquent pool: ~35% SMA-1 (30-60), ~35% SMA-2 (60-90), ~30% NPA (90+)
N_LOANS         = 500         # ~1.25 loans per customer
HISTORY_MONTHS  = 6
CASES_PER_MONTH = 120         # ~4 cases/day for historical simulation (real daily = 150-200)
AGENCY_ID       = "AGENCY-TIQ-001"
AGENCY_NAME     = "TIQ Financial Services Pvt. Ltd."
AGENCY_ADDRESS  = "Near Millennium City Centre, Gurgaon - Delhi Expy, Sector 29, Gurugram, Haryana 122001"
AGENCY_LAT      = 28.4570
AGENCY_LON      = 77.0630
# NCR cities (same as before — agent territory)
CITIES = [
    ("Delhi",    "Delhi",   28.6139, 77.2090),
    ("Noida",    "Uttar Pradesh", 28.5355, 77.3910),
    ("Gurugram", "Haryana", 28.4595, 77.0266),
]
LOAN_TYPES  = list(LoanType)
LANGUAGES   = ["HINDI", "ENGLISH", "PUNJABI", "URDU", "HARYANVI"]
PAYMENT_MODES = list(PaymentMode)
CUSTOMER_SEGMENTS = ["SALARIED", "SELF_EMPLOYED", "BUSINESS_OWNER", "RETIRED", "HOMEMAKER"]
# DPD 31–60 = SMA-1/BUCKET_2, 61–90 = SMA-2/BUCKET_3, 91+ = NPA/BUCKET_NPA. No current accounts.
# Weights: ~35% bucket-2, ~35% bucket-3, ~30% NPA
DPD_CHOICES = [35, 42, 50, 58, 65, 72, 80, 88, 95, 105, 120, 150, 180, 210, 270]
DPD_WEIGHTS = [10, 10,  8,  7,  9,  9,  8,  7,  6,   6,   5,   5,   4,   4,   2]
# Agent voice-note transcripts (spoken after visit, Whisper-transcribed).
# Written in first-person spoken style as the agent would dictate them.
AGENT_TRANSCRIPTS: dict[str, list[str]] = {
    "PAID_FULL": [
        "So I just came out of the visit with the borrower. He paid the full amount in cash, I've issued the receipt on the spot. He seemed very relieved, requested a closure letter from the bank. Case is closed.",
        "Visit done. Customer transferred the full outstanding via UPI just now. I have the bank reference number noted. She said she had been waiting to clear this for a while. All good.",
        "Full payment collected. The borrower had arranged funds from his brother. Paid everything including the penal charges. Asked for original documents to be returned — I've noted that request.",
        "Entire outstanding settled today. Customer used proceeds from a matured FD. Very smooth visit, no issues. He wants the NOC letter urgently.",
        "Visit complete. Customer sold his idle two-wheeler to arrange funds. Full amount received. He was very cooperative throughout.",
        "So the customer had already received her salary arrears this morning. She paid the full amount immediately. No negotiation needed. Closure letter requested.",
        "Visit done. Borrower's family member paid on his behalf — a relative transferred the amount directly. Full collection confirmed. Case resolved.",
        "Full payment received today. Insurance claim had settled last week and he used that money. Everything cleared in one shot.",
    ],
    "PART_PAID": [
        "Just finished the visit. Customer paid a partial amount — his salary got delayed this month, says it should credit by the 10th. I've collected what he had and will revisit after the 10th.",
        "Visit done. Partial payment collected. She's running three EMIs simultaneously and cash is tight right now. She was cooperative and said she'll arrange the rest in about 10 days.",
        "I met the borrower. He's an auto driver, income has been irregular this week. He paid whatever he had earned over the past two days. I'll revisit in 5 days.",
        "Partial collected. Customer said her business is slow this season. She did pay what she had. She mentioned she's usually home after 3 PM on weekdays, so better to visit afternoons.",
        "Visit complete. Borrower is on notice period — severance payout is due next month. He paid what he could from savings. Full payment expected after the payout.",
        "Collected part payment today. Customer mentioned medical expenses for his father last month wiped out his savings. He's recovering financially. Revisit in 2 weeks.",
        "Partial payment done. Customer is a small shopkeeper. He said business is better after the 20th of the month when his clients pay. Will plan revisit around that.",
        "Visit done. Customer paid partial via UPI. She's a homemaker — her husband sends money monthly and it was short this month. She mentioned she's home every morning till noon.",
        "Partial collection done. Two months of employer salary delay for this borrower. He paid whatever was available in his account right now.",
        "Visit complete. Borrower runs a tea stall. Daily cash earner. Paid today's earnings. I'll need to come back a few times to collect the full amount.",
    ],
    "PART_PAID_PTP": [
        "Visit done. Collected partial payment and also got a firm PTP for the balance. Customer's salary credits on the 9th so PTP is set for the 10th. Spouse was also present and confirmed.",
        "Partial collected, PTP taken for the rest. Customer is a contractor — client payments come on the 20th. He paid what he had and committed the balance for the 22nd.",
        "Visit complete. Got part payment in cash. He said balance will come when his tenant pays rent — that's on the 2nd of next month. PTP locked for the 3rd.",
        "Part amount collected today. Customer confirmed she'll be home before 10 AM every day. PTP set — she mentioned salary comes on the last working day of the month.",
        "Collected partial. PTP for balance confirmed by the borrower and his wife both. Customer mentioned he is always available in the mornings before his work starts at 10.",
        "Visit done. Part payment via UPI, PTP for remainder given for end of month. Customer keeps cash at home — said best to visit early morning when he's not gone out yet.",
    ],
    "PTP": [
        "Visit done. Borrower met me and acknowledged the outstanding. Salary credit is expected in 5 days. Full PTP taken. He was cooperative.",
        "Visit complete. Spouse was at home, borrower is travelling on work this week. She gave a PTP on his behalf and signed the acknowledgement. Confirmed he'll be back by Sunday.",
        "Met the borrower today. He's a schoolteacher — salary comes on the 28th every month. PTP set for the 29th accordingly. Very cooperative.",
        "Visit done. Customer on medical leave for the past 2 weeks. Insurance claim pending. PTP taken for month end once the claim settles.",
        "PTP taken. Customer is a shopkeeper. He told me cash is available after 6 PM when his shop closes. So for future visits, I should come in the evening.",
        "Visit complete. Customer acknowledged the dues. Requested 10 days to arrange. PTP noted. He mentioned he works from home on Wednesdays so that's a good day to visit.",
        "Met borrower today. He's a contractor — client pays him on the 20th. PTP set for the 22nd. Business seems steady.",
        "Visit done. Customer was home. Cooperative. He mentioned he's always home before 9:30 in the morning. Best time for future visits confirmed.",
        "PTP collected. Borrower's tenant pays rent on the 2nd — he'll use that. PTP locked for the 3rd. Customer seemed settled and willing.",
        "Visit complete. Customer receives pension on the 5th every month. PTP set for the 6th. Very cooperative senior citizen.",
        "Met the borrower. He said he's available on Sunday mornings before 11 AM. Best window confirmed. PTP taken for this Sunday.",
        "Visit done. Customer mentioned his wife handles all finances — she was present, confirmed the PTP. Borrower working day shift, wife home all day.",
        "PTP taken today. Borrower noted that neighbour Mrs. Sharma can verify he's home if the door is locked. Good intelligence for future visits.",
        "Visit complete. Customer is cooperative. He confirmed he's home every Tuesday and Thursday afternoon after 2 PM. PTP set for next Tuesday.",
    ],
    "BROKEN_PTP": [
        "Visit done but the PTP was not honoured. Customer said the funds got delayed from his end. He's given a new date — 7 more days. I've warned him this is the last extension.",
        "PTP broken again — this is the second time. Borrower claims the business deal he was counting on fell through. I've escalated to supervisor. New date given but flagging for review.",
        "PTP not met. Customer's employer delayed salary for the third consecutive month. He seemed genuinely stressed. New PTP given with empathy but escalation is being considered.",
        "Third broken PTP. Strongly recommending escalation to pre-legal stage. Supervisor has been notified. Customer was apologetic but has no clear plan.",
        "Broken PTP today. Customer says the cheque he deposited bounced — showing me a screenshot of the return. Need to investigate with the bank. New date pending clarification.",
        "PTP broken. Customer was out of station unexpectedly for a family emergency. Met him on call — he gave a new commitment. Will follow up.",
        "Visit done. PTP broken — customer said funds were diverted for an urgent medical expense for his child. Empathetic approach taken. New PTP set.",
        "Second consecutive broken PTP. Behaviour pattern is concerning. Recommending manager review before next visit.",
    ],
    "RTP": [
        "Customer refused to pay. Claims the outstanding amount is wrong and wants a revised bank statement before doing anything. Hostile but no physical confrontation.",
        "Refused completely. He says he never received the loan disbursement. I showed him the documents but he's still denying. Escalation recommended.",
        "Very hostile visit. Customer threatened to file a harassment complaint against the bank. I left immediately. Supervisor needs to review before next contact.",
        "Customer says he already paid the bank branch directly back in April. No receipt traceable. Cross-check with bank needed urgently.",
        "Customer refused. His lawyer was present and advised him not to make any payment until the bank acknowledges a calculation error. Pre-litigation situation.",
        "RTP visit. Customer agitated — disputes the penal interest entirely. Showed his own statement with a different figure. Bank's reconciliation needed.",
        "Refused. Customer says the loan was taken fraudulently in his name. Has filed an FIR and shared a copy. Escalating to legal team immediately.",
        "Customer met but refused any discussion. Third consecutive RTP. Legal team intervention is now required. Manager has been informed.",
    ],
    "DISPUTE": [
        "Visit done. Customer is disputing the interest calculation. Wants a revised statement from the bank before he'll pay anything. Seems genuine — the numbers he showed don't match ours.",
        "Customer disputes the penal charges added last quarter. Payment is on hold until the bank sends a corrected breakdown. Forwarded to grievance cell.",
        "Borrower alleges the loan was taken fraudulently — someone used his documents. He has an FIR copy. Escalating immediately. No payment collected.",
        "Customer says he made 3 payments in the last 6 months that are not reflecting in the statement. Legitimate dispute. Bank verification initiated.",
        "Visit done. Borrower's lawyer was present. They dispute the entire outstanding calculation. Pre-litigation stage — no payment possible until resolved.",
        "Customer disputes the processing fee deducted at disbursement. Says it was not disclosed. Forwarded to the bank grievance team.",
        "Customer showed a receipt claiming a prior settlement was done at the branch. Amount doesn't match our records. Needs urgent bank verification.",
    ],
    "NOT_AVAILABLE": [
        "Visited the address — door was locked. Spoke to the neighbour Mrs. Verma. She said the borrower leaves for his factory shift by 6:30 AM and comes back after 7 PM. Best to visit in the evening.",
        "No one home at the time of my visit. Left a notice slip under the door. Building watchman told me the family returns every evening around 7:30. Will revisit tomorrow evening.",
        "Visited the office address. Receptionist said the customer is on field duty today and will be back by 6 PM. Revisiting later today or tomorrow morning.",
        "Shop was shuttered. The adjacent shopkeeper said it's always closed on Mondays. I'll plan the visit for Tuesday morning when the shop is open.",
        "Door locked. Spoke to the milkman outside — he confirmed the borrower leaves for the construction site by 6 AM every day. Best window is early morning before 6 or after returning.",
        "No response despite multiple knocks. Society security register shows check-out at 8:15 AM. He leaves early. Need to plan a visit before 8 AM or evenings.",
        "Flat locked at 2 PM. The maid told me madam goes to the market between 1 and 3 PM and returns by 3:30. I'll revisit at 4 PM.",
        "Shop closed. Neighbouring vendor confirmed the borrower opens his shop around 10 AM daily except on Tuesdays when he's at the wholesale market.",
        "House locked. A neighbour said the borrower works night shift and is home from 8 AM to 12 noon. I need to plan morning visits for this customer.",
        "No answer at the door. Building admin told me the flat owner is in his native village and returning in 3-4 days. Will plan revisit accordingly.",
        "Visited at 11 AM — door locked. A child was playing outside who said parents had gone to temple and would be back by 11:30. I couldn't wait — will revisit tomorrow morning.",
        "Not available. Auto-driver neighbour mentioned the borrower is also a driver — very irregular hours. Early morning before 8 AM seems to be the only reliable window.",
    ],
    "ADDRESS_ISSUE": [
        "The address in our records doesn't match. Building residents said they don't know anyone by this name. Taking it back for verification.",
        "House number is wrong — the entire block was renumbered after redevelopment last year. Will need the bank to provide the correct address.",
        "Customer not known at this address. Spoke to neighbours — they gave me a possible new area where the family might have shifted. Following up.",
        "Plot has been demolished and new construction is underway. No forwarding contact was available. Need alternate contact from bank records.",
        "Old address — family vacated about 4 months ago. Got a possible new address from a relative who was visiting the building.",
        "Wrong sector in the bank records. Confirmed the correct sector with the local post office. Updating records.",
    ],
    "REVISIT": [
        "Customer is unwell — fever since yesterday. Requested me to come back in 3-4 days. He seemed cooperative, not avoiding. Revisit planned.",
        "Borrower asked me to come only after 5 PM — he works a day shift and can't step out before that. Revisiting this evening.",
        "There were guests at home when I arrived — customer requested privacy and asked me to come tomorrow morning instead. Revisit set for 10 AM tomorrow.",
        "Documents not ready — customer needs 2 more days to gather the bank statements he wants to show. Revisit scheduled.",
        "Joint borrower is not available today — customer wants both of them present for the discussion. Revisit in 2 days when co-borrower returns.",
        "Customer was quite emotional during the visit — seems to be going through a difficult personal situation. Given time. Revisit next week.",
        "Power of attorney document is not ready yet. Customer asked for 2 more days. Revisit once document is available.",
    ],
    "DECEASED": [
        "Borrower is deceased — family confirmed he passed away about 3 months ago. Son was present. Starting the legal heir process as per protocol. Nominee details collected.",
        "Death confirmed by family. Wife present — she showed the death certificate. Forwarding to legal heir settlement team. No payment possible at this stage.",
        "Neighbour confirmed the borrower's death. Family has vacated the flat. Got a contact number from a relative who visited. Forwarding to legal team.",
        "Spouse showed death certificate. No nominee registered with the bank. Legal team needs to initiate the estate claim process.",
    ],
}

# Customer borrower voice transcripts (recorded during visit).
# Written in Hindi-English mixed conversational style as customers would speak.
BORROWER_TRANSCRIPTS: dict[str, list[str]] = {
    "PAID_FULL": [
        "Haan bhai, main ne poora amount de diya hai. Ab mujhe band karne ka letter chahiye bank se jaldi.",
        "Maine transfer kar diya UPI se. Reference number bhi le lo. Mujhe receipt chahiye abhi.",
        "Poora ho gaya. Mere bhai ne help ki. Ab koi balance nahi hai. Please NOC bhej dena jaldi.",
        "Maine FD tod ke bhar di. Ab mera account band karo please. Closure letter urgent hai.",
    ],
    "PART_PAID": [
        "Bhai salary late ho gayi is mahine. Jo tha de diya. 10 tarikh ke baad aao, baki de dunga.",
        "Abhi itna hi tha mere paas. Teen EMI chal rahi hain ek saath. Thoda time do please.",
        "Main auto chalata hoon. Iss hafte kamai kam rahi. Jo kama tha de diya. Panch din mein aana.",
        "Business slow chal raha hai. Poora nahi de sakta abhi. Maheene ke end mein pakka dunga.",
    ],
    "PART_PAID_PTP": [
        "Itna abhi de sakta hoon. Baaki 9 tarikh ko salary aane ke baad turant de dunga.",
        "Kuch de raha hoon aaj. Baki jab client payment aayegi 20 tarikh ko — tab pakka.",
        "Partial abhi lo. Balance end of month mein salary se dunga. Wife bhi yahan hai, woh bhi confirm kar rahi hai.",
    ],
    "PTP": [
        "Bhai salary 5 din mein aayegi. Poora de dunga. Paise ready hain bas credit hona baaki hai.",
        "Main bahar gaya hoon kaam se. Ghar wali ne baat ki. 15 tarikh tak main wapas aaunga aur turant bhar dunga.",
        "Insurance claim pending hai. Jaise hi aayega — uss din hi de dunga. Please ek mahina aur do.",
        "Naukri badli hai, pehli salary aane wali hai. 10 din mein pakka dunga. Koi problem nahi hogi.",
        "Pension 5 tarikh ko aati hai mere. Aap 6 ko aao — pakka de dunga. Main yahan hota hoon subah.",
        "Dukan band karta hoon 7 baje. Tab cash hota hai mere paas. Shaam ko aana acha rahega.",
    ],
    "BROKEN_PTP": [
        "Maafi chahta hoon — deal fall ho gayi last minute. 7 aur din do please. Is baar pakka.",
        "Salary phir delay ho gayi. Company issue hai. Main kya karoon. Thoda aur time chahiye.",
        "Cheque bounce ho gaya — bank ne return kar diya. Screenshot dikha raha hoon. Investigation karo please.",
        "Ghar mein emergency aa gayi, medical tha bacche ka. Poora fund use ho gaya. Samjho please.",
    ],
    "RTP": [
        "Mujhe kuch nahi dena. Amount hi galat hai. Bank ka statement lao — tab baat karo.",
        "Maine yeh loan nahi liya. Kisi ne mere documents use karke liya. FIR file kar di hai.",
        "Main pehle hi bank mein bhar chuka hoon April mein. Receipt nikaalke dikhaunga. Aap log dobara mat aana.",
        "Yeh penal charges galat hain. Main advocate se baat kar raha hoon. Koi payment nahi hogi abhi.",
    ],
    "DISPUTE": [
        "Mere statement mein alag amount dikh raha hai. Yeh extra charges kahan se aaye? Revised statement chahiye.",
        "Maine 3 payments ki hain jo reflect nahi ho rahi. Bank verify kare pehle, tab main baat karoonga.",
        "Yeh loan fraudulently liya gaya hai mere naam pe. FIR hai mere paas. Main nahi bhar sakta.",
        "Disbursement pe jo processing fee kati thi woh clearly nahi bataya gaya tha. Grievance daalunga.",
    ],
    "REVISIT": [
        "Bhai bukhar hai mujhe. 3-4 din baad aao — tab baat karte hain. Main bhaag nahi raha.",
        "Abhi koi khaas baat nahi kar sakta. Kal subah 10 baje aao — poori baat karte hain.",
        "Documents ready nahi hain abhi. 2 din aur chahiye mujhe. Tab aana.",
        "Joint borrower bahar gaye hain. Jab woh aayein tab aana — hum dono saath mein baat karenge.",
    ],
}

# AI-generated visit notes pool (dummy, will be LLM-generated in production).
# Structured 80-120 word summaries combining agent + borrower transcripts + structured data.
AI_VISIT_NOTES: dict[str, list[str]] = {
    "PAID_FULL": [
        "Full recovery achieved on this visit. Agent confirmed borrower made complete payment via UPI — bank reference noted and receipt issued on site. Borrower had arranged funds through a family member. Customer expressed relief and requested NOC and closure letter urgently. No further action required on collection. Case is resolved. Recommend bank to process closure documentation within 5 working days as per customer's request.",
        "Complete settlement recorded. Borrower used proceeds from a matured fixed deposit to clear all outstanding including penal charges. Visit was smooth with no resistance. Agent issued receipt on the spot. Customer specifically requested return of original property documents — bank to coordinate. Case closed, no further field action needed.",
        "Full outstanding cleared this visit. Borrower's insurance claim had settled last week and proceeds were used directly to pay the loan. Agent confirmed UPI transfer with reference number. Customer satisfied, requested formal closure letter. Collection target 100% achieved. Case status updated to PAID.",
    ],
    "PART_PAID": [
        "Partial recovery made this visit. Borrower is salaried with salary delay this month — paid available funds immediately and committed balance on salary credit date (10th). Agent has noted best revisit window as after 10th of month. Customer cooperative throughout. Income source stable, collection expected to complete in next 1-2 visits. Recommend priority follow-up on 10th.",
        "Part payment collected. Customer is managing three concurrent EMIs causing cash flow strain. Paid immediately from available balance. Showed genuine willingness to cooperate. Agent noted customer is home every weekday morning till noon — scheduling next visit accordingly. Recovery trajectory is positive given the cooperative attitude.",
        "Partial amount collected. Borrower is a self-employed shopkeeper — business cash flow peaks after the 20th when clients pay. Agent has established best contact window is evening after shop closing. Recommend scheduling next visit on 21st or 22nd of the month for higher recovery probability.",
    ],
    "PTP": [
        "PTP secured for full outstanding amount. Customer was cooperative and acknowledged the dues clearly. Salary credits on 9th of every month — PTP set for 10th accordingly. Agent confirmed customer is home before 9:30 AM daily, which is the optimal visit window going forward. Behavioural assessment: low risk of broken PTP based on engagement quality. Recommend follow-up call on 9th to confirm.",
        "Promise to Pay recorded. Customer is a pensioner — pension deposits on 5th of every month. PTP set for 6th. Customer was calm and cooperative, clearly understands the obligation. Best time for future visits is morning as customer leaves for prayer by 11 AM. No risk indicators observed. Recommend morning visit on 6th to collect.",
        "PTP obtained from spouse as borrower was travelling on work. Spouse acknowledged the debt and committed on borrower's behalf. Agent noted borrower returns Sunday — recommended visit on Monday morning. Spouse confirmed availability and payment readiness. PTP validity subject to borrower's return, recommend call verification before visit.",
    ],
    "BROKEN_PTP": [
        "Committed PTP was not honoured this visit. Borrower cited unexpected business payment delay — third party client has not paid as expected. This is the second broken PTP. Escalation risk is moderate. Agent has given 7 more days with a firm warning. Recommend supervisor review before next extension is granted. Pattern of commitment without follow-through is concerning.",
        "PTP broken for the second consecutive time. Employer salary has been delayed again — borrower has limited control over this. Agent observed genuine financial distress rather than deliberate avoidance. New PTP given. Recommend manager to evaluate whether pre-legal notice would help motivate payment without excessive pressure given the borrower's cooperative attitude.",
        "Third broken PTP. Pattern now clearly indicates inability rather than unwillingness. Recommend escalation to pre-legal stage. Supervisor notified. Borrower was apologetic but has no clear financial plan. Legal notice may prompt structured settlement discussion.",
    ],
    "RTP": [
        "Customer refused all payment — hostile engagement. Claims outstanding amount is incorrect and demands revised bank statement before any discussion. Agent left after presenting formal demand notice. Customer is aware of legal consequences but still refuses. Recommend legal team review. Outstanding dispute on interest calculation needs bank clarification before next field visit.",
        "Strong refusal to pay. Customer alleges fraudulent loan disbursal in his name — has filed FIR and showed copy to agent. No payment possible until fraud investigation is complete. Case forwarded to legal team. Bank's fraud desk must be looped in immediately. Further field visits not productive until fraud allegation is resolved.",
        "RTP with increasing hostility. Third consecutive refusal. Customer's lawyer was present advising non-payment. Matter appears to be heading to consumer court. Recommend legal team take over directly. Agent should not make further field visits without legal team coordination to avoid harassment complaint risk.",
    ],
    "DISPUTE": [
        "Customer is disputing the outstanding amount — claims 3 payments made to bank branch are not reflecting in current statement. Agent verified the discrepancy exists based on customer's payment receipts shown. Legitimate dispute. Payment collection is blocked until bank reconciles the statement. Recommend urgent escalation to bank's accounts team. Customer is cooperative and has willingness to pay once amounts are corrected.",
        "Disputed visit — customer's lawyer present advising against payment. Dispute is on penal interest charges that customer claims were not disclosed at loan origination. Legal team involvement is necessary. Customer has also contacted the banking ombudsman. No payment until dispute resolves. Case should be flagged as dispute-pending in the system.",
        "Amount disputed — customer showed own calculation with a significantly different outstanding figure. Discrepancy likely from processing fees charged at disbursement that are not separately itemized in the bank statement. Forwarded to grievance cell. Recommend bank provide itemized break-up to resolve this and unlock payment.",
    ],
    "NOT_AVAILABLE": [
        "Customer not found at address during visit. Building watchman confirmed borrower works a factory night shift and is home between 8 AM and 12 noon. Agent to plan next visit in that morning window. Left official notice at door. Best contact time is morning. Recommend scheduling next visit between 9-11 AM on a weekday.",
        "Premises locked at time of visit. Neighbour confirmed customer goes to the market from 1-3 PM and is home before 3:30 PM. Alternatively home by 7:30 PM in evenings. Agent left notice. Recommend next visit at 4 PM or after 7:30 PM to maximise contact probability.",
        "Customer not available. Shop is closed on Mondays — agent visited on a Monday. Adjacent shopkeeper confirmed business opens at 10 AM on all other days. Recommend scheduling next visit Tuesday to Saturday between 10 AM-12 PM. Notice left. Key scheduling insight for future reference.",
    ],
    "REVISIT": [
        "Visit incomplete — customer requested revisit due to personal circumstances. Customer appeared cooperative and genuine. Not avoiding contact. Agent has scheduled revisit for 3 days later. Customer confirmed specific availability window. No red flags for evasion. Standard follow-through expected.",
        "Revisit needed — documents requested by customer not ready today. Customer is engaged and communicative. Specific revisit date agreed upon. Agent notes customer is easiest to reach in morning hours. Recommend morning visit on agreed date.",
        "Incomplete visit — joint borrower not present and customer wants both parties at the meeting. Revisit scheduled for when co-borrower returns (2 days). This is a procedural delay, not evasion. No risk indicators.",
    ],
    "ADDRESS_ISSUE": [
        "Address in system is incorrect — building does not match, residents do not recognise the borrower's name. Agent attempted to gather forwarding information but neighbours had no details. Bank needs to verify the correct address from KYC documents or contact number. Field visit cannot proceed until correct address is confirmed.",
        "Address mismatch confirmed. Area was redeveloped and block numbers renumbered. Agent has identified the correct block through enquiry. Bank records need updating. Recommend calling the customer to confirm current address before next field visit.",
    ],
}

# Call log note pools — agent's spoken note after phone call with customer.
CALL_LOG_NOTES: dict[str, list[str]] = {
    "ANSWERED_TIMING": [
        "Called the customer. He picked up. Said he will be home until 10:30 AM tomorrow — I can visit any time before 11. Good to note for scheduling.",
        "Spoke to the customer just now. She leaves for the market at 12:30 PM daily. Best window is weekday mornings before noon.",
        "Customer answered. He works a night shift — so he's available from 9 AM to 1 PM on weekdays. I'll plan the visit for 10 AM.",
        "Call answered. Customer is home only on weekends. Not reachable during weekdays in the day at all.",
        "Customer picked up. Said come after 10 AM as he prays in the early morning. Available from 10 onwards on all days.",
        "Good call. Customer confirmed he's home till 9 AM every weekday before heading to his construction site. I need to go early.",
        "Spoke to the borrower's wife — she said her husband is at his shop on MG Road today. I can visit the shop between 11 AM and 1 PM.",
        "Customer answered. Said he works from home on Wednesdays and Fridays — those are the best days for a visit.",
        "Called — customer's daughter picked up and said mother is home by 4 PM every day from her clinic job. Evening visits work.",
        "Customer answered. Said he's home for lunch between 1 PM and 2:30 PM daily. That's a good window to visit.",
        "Spoke to borrower. He said Sundays he's home all morning. Weekdays he's at the factory by 7 AM.",
        "Customer picked up and confirmed home on the visit day. Requested I call before leaving so he can keep cash ready.",
    ],
    "ANSWERED_UNAVAILABLE": [
        "Customer picked up. Said he's out of city for 10 days for a family function. Requested no visit until he returns on the 15th. Blocking visits until then.",
        "Spoke to the borrower. He's at a wedding in his native village — back by Sunday. Asked me to visit on Monday morning.",
        "Customer's wife answered. Said the borrower is hospitalised for a surgery. Should be back home by Thursday. Will revisit after that.",
        "Customer answered from out of town. He's on a work contract in Rajasthan for 2 more weeks. Not reachable for visit till month end.",
        "Borrower picked up. Child's board exams are ongoing — he specifically requested no visit for the next 2 weeks till exams are over.",
        "Customer answered. Said he's going through court proceedings this week — his lawyer advised no contact. Requesting 2 weeks gap.",
        "Called — customer said he's in the process of moving house this week. New address will be shared in 3-4 days once settled.",
        "Customer answered but was very distressed — his parent is critical in hospital. Not a good time. Requested I call back in 4 days.",
        "Customer on pilgrimage — wife confirmed, said he will be back in 12 days. Blocking visits till confirmed return.",
        "Spoke to borrower. Harvest season — he's at his farm in another state. Will not be reachable for a month. Seasonal constraint.",
    ],
    "ANSWERED_PTP_CONFIRM": [
        "Pre-visit call done. Customer confirmed the PTP amount is ready in his account. Visit confirmed for tomorrow morning.",
        "Called as a reminder. Borrower said she will transfer via UPI today itself — before noon. No need for field visit today.",
        "Reminder call — customer confirmed salary credited yesterday and full amount is ready. Coming to collect now.",
        "Pre-visit call confirmed. Customer said payment is ready. Also said come before noon as he has an afternoon appointment.",
        "Called to confirm PTP — customer gave me the bank reference number for a transfer already done today. Will verify.",
        "Reminder answered. Customer confirmed payment physically kept ready in cash. Visit confirmed.",
        "Pre-visit reminder — customer said everything is ready. Asked me to be discreet when visiting as neighbours are watching.",
    ],
    "ANSWERED_HOSTILE": [
        "Customer picked up and immediately said to stop calling or he will file a harassment complaint. He cut the call. No visit recommended right now.",
        "Borrower answered. Said the outstanding amount is wrong and he will not pay until the bank corrects the statement. Call ended.",
        "Hostile call. Customer said the matter is with the banking ombudsman and threatened complaint if we call again.",
        "Customer answered aggressively — said he has a lawyer and to not call again. Call ended within 30 seconds.",
        "Customer picked up briefly, said don't disturb during office hours and disconnected. Will try in the evening.",
    ],
    "ANSWERED_PAYMENT_INTEL": [
        "Good call. Customer told me his salary comes on the 9th every month. He will pay on the 10th. I'll plan the visit accordingly.",
        "Spoke to the borrower. He runs a small business — income is strongest after the 20th when his clients pay. PTP for 22nd confirmed.",
        "Customer answered. His pension deposits on the 5th. Very willing to pay on the 6th. Cooperative customer.",
        "Call done. Borrower gets rental income on the 2nd of every month. Best time to collect is on the 3rd or 4th.",
        "Customer mentioned his wife is the primary earner — her salary comes on the last working day of the month. PTP accordingly.",
        "Spoke to borrower. His property is being sold — proceeds will clear the full outstanding. 30-60 day window expected.",
        "Customer said insurance claim is pending settlement. When it comes, it will cover the full loan amount. Monitoring.",
    ],
    "NO_ANSWER": [
        "Tried calling three times — no response. Will try visiting directly based on the timing intel from the last visit.",
        "Phone rang but no one picked up. Will try the alternate number listed in records.",
        "No answer since morning. Will try calling in the evening — customer may be at work.",
        "Primary number not answering. Alternate number also not reachable. Planned a direct visit.",
        "Three unanswered attempts today. Visiting directly — based on previous note that customer is home before 10 AM.",
    ],
    "SWITCHED_OFF": [
        "Phone is switched off. Will try again after 2 hours.",
        "Both primary and alternate numbers are switched off since morning. Planning a direct visit.",
        "Switched off — second consecutive day. Will visit directly tomorrow morning.",
        "Switched off. Going by the timing intel from last visit — planning morning visit before 9 AM.",
    ],
    "BUSY": [
        "Busy tone on first attempt. Will try again in 15 minutes.",
        "Line was busy. Customer probably on another call. Trying again.",
        "Continuously busy for past 20 minutes. Will visit directly.",
    ],
    "DECLINED": [
        "Call was declined immediately — customer likely recognised the number. Will try from a different number.",
        "Declined 3 times in a row. Customer is actively avoiding contact. Planning direct field visit.",
        "Call rejected within 2 rings each time. Possible number block. Visiting directly.",
    ],
}

# Leave remarks for beat history seeding
LEAVE_REMARKS: dict[str, list[str]] = {
    "SICK_LEAVE": [
        "Fever and body ache — doctor prescribed 2 days rest.",
        "Food poisoning — admitted to clinic for day.",
        "Viral infection — unfit for field work.",
        "Stomach infection, on medication.",
        "Severe headache — not fit for travel.",
        "Dental extraction — pain and swelling.",
        "Knee injury — unable to ride two-wheeler.",
        "Eye treatment — rest prescribed post procedure.",
    ],
    "CASUAL_LEAVE": [
        "Personal work — bank and government documents.",
        "Family function — sibling's engagement.",
        "Child school admission formality.",
        "Voter ID and Aadhaar update work.",
        "Moving to new house — logistical day.",
        "Personal emergency.",
        "Attending relative's funeral.",
        "Vehicle RC and insurance renewal.",
    ],
    "EARNED_LEAVE": [
        "Annual family trip — Diwali vacation.",
        "Planned vacation — approved 2 weeks prior.",
        "Festival celebration leave.",
        "Hometown visit — earned leave block.",
        "Extended Eid holidays — approved.",
    ],
    "ABSENT": [
        "Did not report — no prior information.",
        "Absent without leave. Manager notified.",
        "No communication from agent.",
        "Unplanned absence — second occurrence this month.",
    ],
}
# Realistic bank remarks for cases forwarded from ABC Bank
BANK_REMARKS = [
    "Customer had good repayment history until Q3 2022. Defaulted post job loss.",
    "Bank field agent visited twice — no contact. Escalated to recovery agency.",
    "Previous agency attempt closed with no resolution. Fresh allocation.",
    "Customer called 6 times by bank IVR — no response. Contact number may have changed.",
    "Borrower is cooperative per branch records but cash-strapped. Willing to negotiate.",
    "Legal notice sent last month. Customer acknowledged receipt, no payment.",
    "Seasonal worker — income irregular. Best to visit early morning.",
    "Business borrower — shop visit recommended. Morning hours more effective.",
    "Co-applicant details on file. May respond if approached together.",
    "Borrower relocated. New address shared by guarantor — verify on field visit.",
    "Partial payment of overdue attempted by customer at branch — reversed due to shortfall.",
    "Account flagged for special mention — court case pending. Handle with care.",
]
# ─── Helpers ──────────────────────────────────────────────────────────────────
def _uid() -> str:
    return str(uuid.uuid4())
def _jitter_coords(lat: float, lon: float, radius_km: float = 15) -> tuple[float, float]:
    dx = random.uniform(-radius_km, radius_km) / 111
    dy = random.uniform(-radius_km, radius_km) / (111 * math.cos(math.radians(lat)))
    return round(lat + dx, 6), round(lon + dy, 6)
def _dpd_to_bucket(dpd: int) -> DPDBucket:
    if dpd <= 30:
        return DPDBucket.BUCKET_1
    elif dpd <= 60:
        return DPDBucket.BUCKET_2
    elif dpd <= 90:
        return DPDBucket.BUCKET_3
    return DPDBucket.NPA
# _risk_from_dpd was DELETED on 2026-08-21, not deprecated.
#
# It was one of two near-identical copies of the same formula (the other lived
# in ingest_daily.py) and they had already drifted: this one floored the score
# at 30, so RiskCategory.LOW was unreachable on a seeded database and
# RiskBadge's LOW branch was dead code. It also added random.uniform(-8, 8) of
# jitter, so the same borrower scored differently on consecutive runs and no
# score could be reproduced from its inputs.
#
# Worst of all it was fed a `dpd_hint` drawn independently of the customer's
# real loans (see [3/11] below), so the number was decorrelated from the very
# thing it claimed to measure.
#
# Scoring now happens once, at [11c], through services/repayment_service.py —
# after visits, PTPs and payments exist, because those are what the scorecard's
# behavioural factors read.
def _priority_from_score(score: float) -> CasePriority:
    if score >= 85: return CasePriority.CRITICAL
    if score >= 60: return CasePriority.HIGH
    if score >= 35: return CasePriority.MEDIUM
    return CasePriority.LOW
def _recovery_potential(dpd: int, loan_type: LoanType, risk_score: float) -> RecoveryPotential:
    """Derive recovery_potential with realistic signal for ML training.

    DPD is the primary driver; loan_type and risk_score add secondary signal.
    Weights are [HIGH, MEDIUM, LOW] and always sum to 100 (approx).
    """
    # Base weights by DPD bucket
    if dpd <= 60:       # SMA-1: borrower still reachable, salary-linked defaults
        w = [50, 30, 20]
    elif dpd <= 90:     # SMA-2: harder, some absconding
        w = [20, 45, 35]
    else:               # NPA 90+: mostly resistant or untraceable
        w = [8, 22, 70]

    # Secured loans (HOME / AUTO / GOLD) have collateral → easier to convert
    if loan_type in (LoanType.HOME, LoanType.AUTO, LoanType.GOLD):
        w[0] += 8
        w[2] -= 8

    # High risk score means behaviour is deteriorating → lower potential
    if risk_score >= 75:
        w[0] -= 12
        w[2] += 12
    elif risk_score <= 45:
        w[0] += 8
        w[2] -= 8

    w = [max(1, x) for x in w]   # no negative weights
    return random.choices(list(RecoveryPotential), weights=w)[0]


def _collection_stage(dpd: int, legal: str) -> str:
    if dpd <= 60:  return "FIELD"
    if dpd <= 90:  return "PRE_LEGAL"
    if legal != "NONE": return "LEGAL"
    return "NPA_RECOVERY"
def _drop_all_enums(eng):
    with eng.begin() as conn:
        rows = conn.execute(text(
            "SELECT typname FROM pg_type WHERE typtype='e' "
            "AND typnamespace=(SELECT oid FROM pg_namespace WHERE nspname='public')"
        )).fetchall()
        for (typname,) in rows:
            conn.execute(text(f'DROP TYPE IF EXISTS "{typname}" CASCADE'))
    if rows:
        print(f"  Dropped {len(rows)} old enum type(s)")
# ─── Outcome weights by DPD bucket ────────────────────────────────────────────
def _visit_outcomes_for_dpd(dpd: int):
    """Returns (met_prob, met_outcomes_weights, not_met_outcomes_weights)."""
    if dpd <= 60:
        # 30-60 DPD: better contact rate, more recoveries
        return (
            0.60,
            [VisitOutcome.PAID_FULL, VisitOutcome.PART_PAID, VisitOutcome.PTP,
             VisitOutcome.PART_PAID_PTP, VisitOutcome.RTP, VisitOutcome.DISPUTE],
            [18, 20, 30, 12, 12, 8],
            [VisitOutcome.NOT_AVAILABLE, VisitOutcome.ADDRESS_ISSUE, VisitOutcome.REVISIT],
            [55, 20, 25],
        )
    elif dpd <= 90:
        # 60-90 DPD: moderate contact, more disputes
        return (
            0.45,
            [VisitOutcome.PAID_FULL, VisitOutcome.PART_PAID, VisitOutcome.PTP,
             VisitOutcome.PART_PAID_PTP, VisitOutcome.RTP, VisitOutcome.DISPUTE],
            [10, 15, 28, 10, 22, 15],
            [VisitOutcome.NOT_AVAILABLE, VisitOutcome.ADDRESS_ISSUE, VisitOutcome.REVISIT],
            [45, 30, 25],
        )
    else:
        # 90+ DPD: low contact, many absconded / address issues
        return (
            0.30,
            [VisitOutcome.PAID_FULL, VisitOutcome.PART_PAID, VisitOutcome.PTP,
             VisitOutcome.PART_PAID_PTP, VisitOutcome.RTP, VisitOutcome.DISPUTE],
            [5, 10, 18, 7, 30, 30],
            [VisitOutcome.NOT_AVAILABLE, VisitOutcome.ADDRESS_ISSUE,
             VisitOutcome.REVISIT, VisitOutcome.DECEASED],
            [40, 30, 22, 8],
        )
# A field agent collects cash / UPI at the door. Anything above this in one
# visit would be a bank transfer, not a doorstep recovery — without the cap,
# a PAID_FULL on a ₹30L NPA case books ₹30L against a single visit and the
# day's collection total stops being believable.
FIELD_PAYMENT_CAP = 120_000.0


# What the agency asks an agent to recover on a case THIS cycle — the arrears
# it is chasing now, not the whole outstanding balance. Billing the full
# overdue made a 12-stop day carry a ₹26L+ target, when a field agent's day is
# worth ₹2-4L; it also made every "collection rate" on screen read near zero.
CYCLE_TARGET_CAP = 70_000.0


def _cycle_target(overdue: float) -> float:
    if not overdue or overdue <= 0:
        return 5000.0
    return round(min(overdue * random.uniform(0.18, 0.35), CYCLE_TARGET_CAP), 2)


def _field_payment(outcome, remaining: float, r: random.Random) -> float:
    """Amount a doorstep visit can plausibly collect against `remaining`."""
    if remaining <= 0:
        return 0.0
    if outcome == VisitOutcome.PAID_FULL:
        amount = remaining
    else:
        amount = round(remaining * r.uniform(0.75, 1.0), 2)
    amount = min(amount, remaining, FIELD_PAYMENT_CAP)
    return round(max(amount, min(500.0, remaining)), 2)


def _stable_rng(*parts) -> random.Random:
    """RNG keyed off business identifiers (employee_code, case_number, dates) —
    never off row ids, which are fresh UUIDs on every seed run. This is what
    makes a re-run reproduce the same numbers on the same anchor date."""
    return random.Random("|".join(str(p) for p in parts))


# Reasons attached to the 5 due-today PTPs, in beat order. Kept as a fixed list
# (not random) so a re-seed produces the same PTP rows.
DUE_TODAY_PTP_REASONS = [
    "Salary credited today — will pay the committed amount before evening.",
    "Shop collections come in by afternoon. Asked agent to visit after 5 PM.",
    "Client payment cleared this morning. Funds are in the business account.",
    "Family arranged the amount. Borrower confirmed cash is ready at home.",
    "Pension credited on schedule. Will hand over the amount on today's visit.",
]


def curate_demo_agent_ptps(db, agent, today, due_today_case_ids=None):
    """Give one agent a realistic current-month PTP book for the demo:
    several kept (HONORED), one missed (BROKEN), and a handful still due TODAY.
    Idempotent — clears this agent's current-month PTPs first, so it can run in
    the seed AND against a live DB to keep the two in sync. Returns a summary.

    Realistic shape (deterministic): 7 honored + 1 broken + 5 due-today.
    -> PTP conversion ≈ 54% (7/13), and 5 PTPs due today on the home screen.

    The 5 due-today PTPs MUST sit on cases that are in the agent's current beat,
    otherwise the home screen counts 5 but "PTPs Due Today" on the cases page
    (which filters the beat) opens empty. Beat cases are picked in route order,
    skipping the ones already visited today and the DEMO_CONTACT_NAME showcase
    case (kept clean so the live record-visit demo can set its own PTP).
    The honored/broken history goes on the agent's older, off-beat cases so
    today's beat cards aren't polluted with stale promises.
    """
    month_start = date(today.year, today.month, 1)
    cases = db.query(Case).filter(Case.agent_id == agent.id).all()
    if not cases:
        return {"honored": 0, "set": 0, "due_today": 0}
    by_id = {c.id: c for c in cases}

    # Wipe existing current-month PTPs for this agent so re-runs are stable.
    db.query(PTP).filter(
        PTP.agent_id == agent.id,
        PTP.committed_date >= month_start,
    ).delete(synchronize_session=False)
    db.flush()

    # ── Which cases carry the 5 due-today PTPs ────────────────────────────────
    beat = (
        db.query(Beat)
        .filter(Beat.agent_id == agent.id)
        .order_by(Beat.beat_date.desc())
        .first()
    )
    beat_ids = [cid for cid in (beat.ordered_case_ids or []) if cid in by_id] if beat else []
    if due_today_case_ids:
        due_ids = [cid for cid in due_today_case_ids if cid in by_id][:5]
    else:
        beat_day = beat.beat_date if beat else today
        day_start = datetime.combine(beat_day, datetime.min.time()).replace(tzinfo=timezone.utc)
        day_end = datetime.combine(beat_day, datetime.max.time()).replace(tzinfo=timezone.utc)
        visited_today = {
            row[0] for row in db.query(Visit.case_id).filter(
                Visit.agent_id == agent.id,
                Visit.check_in_time >= day_start,
                Visit.check_in_time <= day_end,
            ).all()
        }
        showcase_ids = {
            row[0] for row in db.query(Case.id)
            .join(Customer, Customer.id == Case.customer_id)
            .filter(Case.agent_id == agent.id, Customer.full_name == settings.DEMO_CONTACT_NAME)
            .all()
        }
        eligible = [
            cid for cid in beat_ids
            if cid not in visited_today and cid not in showcase_ids
            and by_id[cid].status not in (CaseStatus.PAID, CaseStatus.CLOSED, CaseStatus.WRITTEN_OFF)
        ]
        # Cases already carrying a PTP_SET badge come first — a due-today promise
        # fits their story, and picking them avoids rewriting other case statuses.
        due_ids = (
            [cid for cid in eligible if by_id[cid].status == CaseStatus.PTP_SET]
            + [cid for cid in eligible if by_id[cid].status != CaseStatus.PTP_SET]
        )[:5]
    due_set = set(due_ids)
    # History lands on off-beat cases first; fall back to any case if the agent
    # has too few, so a small dataset still produces the full 13-PTP book.
    history_pool = [c for c in cases if c.id not in due_set and c.id not in set(beat_ids)] \
        or [c for c in cases if c.id not in due_set] or cases

    # (days_ago, status) — 0 = due today.
    history_plan = (
        [(d, PTPStatus.HONORED) for d in (3, 6, 9, 12, 15, 18, 21)]  # 7 kept
        + [(7, PTPStatus.BROKEN)]                                    # 1 missed
    )
    # Working days already elapsed this month, today excluded — that's where a
    # promise can have fallen due and been settled. Early in the month the
    # natural date (today − N) lands before the 1st; clamping it to month_start
    # stacked 6 of the 8 history PTPs on the same day, and left the profile's
    # PTP rate reading 1/6. Spreading them over the elapsed days is also the
    # truthful story: a PTP taken in late July with an early-August due date
    # settles in August.
    elapsed_days = []
    _d = month_start
    while _d < today:
        if _d.weekday() < 6:       # Mon–Sat, same working week as the beats
            elapsed_days.append(_d)
        _d += timedelta(days=1)

    honored = broken = due_today = 0
    for i, (days_ago, status) in enumerate(history_plan):
        case = history_pool[i % len(history_pool)]
        natural = today - timedelta(days=days_ago)
        if natural >= month_start:
            committed = natural           # full month elapsed — use the real date
        elif elapsed_days:
            committed = elapsed_days[-(1 + (i % len(elapsed_days)))]
        else:
            committed = month_start       # seeded on the 1st: nothing else to use
        amount = round(float(case.target_amount or 5000.0), 2)
        paid = amount if status == PTPStatus.HONORED else 0.0
        db.add(PTP(
            id=_uid(), case_id=case.id, agent_id=agent.id,
            committed_amount=amount, committed_date=committed,
            actual_paid_amount=paid, status=status,
            agent_notes="Demo PTP",
        ))
        if status == PTPStatus.HONORED:
            honored += 1
        else:
            broken += 1

    for i, cid in enumerate(due_ids):
        case = by_id[cid]
        amount = round(float(case.target_amount or 5000.0), 2)
        db.add(PTP(
            id=_uid(), case_id=cid, agent_id=agent.id,
            committed_amount=amount, committed_date=today,
            actual_paid_amount=0.0, status=PTPStatus.ACTIVE,
            customer_reason=DUE_TODAY_PTP_REASONS[i % len(DUE_TODAY_PTP_REASONS)],
            agent_notes="PTP falls due today — follow up before 7 PM.",
            follow_up_date=today,
        ))
        # Badge on the case card must agree with the PTP that's now due on it.
        if case.status not in (CaseStatus.PAID, CaseStatus.CLOSED, CaseStatus.WRITTEN_OFF):
            case.status = CaseStatus.PTP_SET
        due_today += 1
    db.flush()
    return {
        "honored": honored, "broken": broken, "due_today": due_today,
        "set": len(history_plan) + len(due_ids),
    }


def seed_recent_daily_activity(db, agents, today: date, days_back: int = 30) -> dict:
    """Put real visit / payment / PTP rows behind every recent working day.

    The beat history carries per-day totals, but only *today* had actual rows
    behind it — so filtering the manager's case list to 3 or 4 August returned
    nothing, and the days on the duty calendar were unsupported numbers. Every
    working day in the window now gets its own allocation of cases, its own
    beat, and the visits and receipts that justify the day's figures.

    Cases are dealt out so a day's allocation is exclusive: allocation_date is
    a single field, so a case can only belong to one day's list.

    Deterministic per (agent, day) and idempotent — a day that already has real
    visits is left untouched. Caller commits.
    """
    from collections import defaultdict
    made = defaultdict(int)
    DAY_OUTCOMES = [VisitOutcome.PAID_FULL, VisitOutcome.PART_PAID, VisitOutcome.PART_PAID_PTP,
                    VisitOutcome.PTP, VisitOutcome.NOT_AVAILABLE, VisitOutcome.RTP,
                    VisitOutcome.REVISIT, VisitOutcome.DISPUTE]
    DAY_WEIGHTS = [25, 28, 12, 14, 9, 4, 5, 3]   # ~45% of visits recover money:
    # at ~34% the day's recovery could never reach a believable share of
    # the day's target (it was landing at 4-22%).
    MONEY = (VisitOutcome.PAID_FULL, VisitOutcome.PART_PAID, VisitOutcome.PART_PAID_PTP)

    # Working days so far this month, oldest first, today excluded (already
    # populated by the scripted demo day and the today's-activity block).
    #
    # Scoped to the month, not the full `days_back` window: a case may only be
    # worked once in the window (see below), so spreading a full day's load
    # across 26 days would need ~280 cases per agent and they hold 30-60. The
    # month to date is also exactly what the profile counts and what the
    # manager's recent date filters reach for.
    start = max(date(today.year, today.month, 1), today - timedelta(days=days_back))
    days = []
    d = start
    while d < today:
        if d.weekday() < 6:
            days.append(d)
        d += timedelta(days=1)
    if not days:
        return {}

    for ag in agents:
        cust = {}
        my_cases = db.query(Case).filter(Case.agent_id == ag.id).all()
        if not my_cases:
            continue
        for c in my_cases:
            if c.customer_id not in cust:
                cust[c.customer_id] = db.query(Customer).filter(
                    Customer.id == c.customer_id).first()
        beats = {b.beat_date: b for b in db.query(Beat).filter(
            Beat.agent_id == ag.id, Beat.beat_date.in_(days)).all()}
        visited_on = defaultdict(set)
        for v in db.query(Visit).filter(Visit.agent_id == ag.id).all():
            visited_on[v.check_in_time.date()].add(v.case_id)

        # Today's list is already fixed; everything else is dealt across the
        # window, newest day first so recent days get the fullest beats.
        #
        # A case may only be worked on ONE day in the window. allocation_date is
        # a single field, so a case worked twice can only be listed under one of
        # those dates — and the other day ends up with visits but no cases in
        # the manager's list. Anything already visited in the window is out.
        reserved = {c.id for c in my_cases if c.allocation_date == today.strftime("%Y-%m-%d")}
        window_start = today - timedelta(days=days_back)
        for d, cids in visited_on.items():
            if window_start <= d <= today:
                reserved |= cids
        pool = sorted((c for c in my_cases if c.id not in reserved),
                      key=lambda c: c.case_number)
        _stable_rng("pool", ag.employee_code).shuffle(pool)
        cursor = 0

        for day in reversed(days):
            beat = beats.get(day)
            if beat is None or beat.is_leave_day:
                continue
            r = _stable_rng("day", ag.employee_code, day.isoformat())
            # A day's real load, not the synthetic count the attendance history
            # carried (4-16, often far too low): sizing off that left the demo
            # agent with 18 visits for four elapsed days.
            want = min(r.randint(10, 12), max(1, (ag.max_cases_per_day or 12)))
            # Top the day up rather than skipping it. Historical visits land on
            # some of these dates by chance, and skipping any day that already
            # had one left barely half the agent-days populated.
            want -= len(visited_on.get(day, ()))
            if want <= 0:
                continue
            take = pool[cursor:cursor + want]
            cursor += len(take)
            if not take:
                # Agent has no unworked cases left to deal. The day can't claim
                # completed stops it has no visits for, so stand it down.
                beat.ordered_case_ids = []
                beat.total_cases = 0
                beat.cases_completed = 0
                beat.amount_collected = 0.0
                beat.total_target_amount = 0.0
                made["days_stood_down"] += 1
                continue
            day_cash = 0.0
            ordered = []
            for i, c in enumerate(take):
                cu = cust.get(c.customer_id)
                if not cu:
                    continue
                ordered.append(c.id)
                c.allocation_date = day.strftime("%Y-%m-%d")
                outcome = r.choices(DAY_OUTCOMES, weights=DAY_WEIGHTS)[0]
                remaining = round((c.target_amount or 0) - (c.collected_amount or 0), 2)
                if outcome in MONEY and remaining <= 0:
                    outcome = VisitOutcome.PTP      # nothing left to collect
                met = outcome != VisitOutcome.NOT_AVAILABLE
                cin = datetime.combine(day, datetime.min.time()).replace(
                    hour=min(8 + i, 17), minute=r.randint(5, 55), tzinfo=timezone.utc)
                v = Visit(
                    id=_uid(), case_id=c.id, agent_id=ag.id,
                    check_in_latitude=cu.latitude, check_in_longitude=cu.longitude,
                    check_in_time=cin,
                    check_out_time=cin + timedelta(minutes=r.randint(18, 45)),
                    distance_from_customer_metres=round(r.uniform(15, 90), 1),
                    geo_verified=True, within_contact_hours=True,
                    customer_met=met, outcome=outcome,
                    person_met=PersonMet.BORROWER if met else None,
                    agent_recording_transcript=r.choice(
                        AGENT_TRANSCRIPTS.get(outcome.value, AGENT_TRANSCRIPTS["REVISIT"])),
                    ai_visit_note=r.choice(
                        AI_VISIT_NOTES.get(outcome.value, AI_VISIT_NOTES["REVISIT"])),
                    visit_number=(c.visit_count or 0) + 1,
                )
                db.add(v); db.flush()
                c.visit_count = (c.visit_count or 0) + 1
                made["visits"] += 1
                if outcome in MONEY:
                    amt = _field_payment(outcome, remaining, r)
                    if outcome == VisitOutcome.PAID_FULL and amt < remaining - 0.01:
                        # Capped below the balance, so it isn't a full recovery.
                        outcome = VisitOutcome.PART_PAID
                        v.outcome = outcome
                    db.add(Payment(
                        id=_uid(), case_id=c.id, visit_id=v.id, agent_id=ag.id,
                        amount=amt, mode=r.choice(PAYMENT_MODES),
                        status=PaymentStatus.VERIFIED,
                        receipt_number=f"RCP{r.randint(10000000, 99999999)}",
                        payment_date=cin + timedelta(minutes=10), receipt_sms_sent=True))
                    c.collected_amount = round((c.collected_amount or 0) + amt, 2)
                    day_cash += amt
                    made["payments"] += 1
                if outcome in (VisitOutcome.PTP, VisitOutcome.PART_PAID_PTP):
                    due = day + timedelta(days=r.randint(3, 20))
                    if due == today:
                        # "Due today" is the scripted demo set (exactly 5 on the
                        # demo agent's home screen) — back-filled days must not
                        # add to it.
                        due = today + timedelta(days=1)
                    st = (PTPStatus.ACTIVE if due > today
                          else PTPStatus.HONORED if r.random() < 0.6 else PTPStatus.BROKEN)
                    db.add(PTP(
                        id=_uid(), case_id=c.id, visit_id=v.id, agent_id=ag.id,
                        committed_amount=round(float(c.target_amount or 5000.0), 2),
                        committed_date=due,
                        actual_paid_amount=(round(float(c.target_amount or 0) * 0.85, 2)
                                            if st == PTPStatus.HONORED else 0.0),
                        status=st))
                    made["ptps"] += 1
            beat.ordered_case_ids = ordered
            beat.total_cases = len(ordered)
            beat.cases_completed = len(ordered)
            beat.amount_collected = round(day_cash, 2)
            beat.total_target_amount = round(
                sum(float(c.target_amount or 0) for c in take), 2)
            made["days"] += 1
        db.flush()
    return dict(made)


def reconcile_integrity(db, today: date) -> dict:
    """Repair contradictions between visits, payments, PTPs, case status and beats.

    Every screen in the app reads a different one of these tables, so a row that
    disagrees with its neighbours shows up as a visible lie: a case badged
    "part paid" with no receipt, a PTP_SET card with no live promise on it, a
    beat claiming 3 stops done while 8 cases carry today's visited tick.

    Deterministic (all randomness keyed off row ids) and idempotent, so the seed
    and an already-populated DB converge on the same answer. Caller commits.
    """
    from collections import defaultdict
    MONEY = (VisitOutcome.PAID_FULL, VisitOutcome.PART_PAID, VisitOutcome.PART_PAID_PTP)
    fixed = defaultdict(int)

    cases = {c.id: c for c in db.query(Case).all()}
    visits = db.query(Visit).all()
    pays = db.query(Payment).all()
    ptps = db.query(PTP).all()
    paid_total = defaultdict(float)
    for p in pays:
        paid_total[p.case_id] += p.amount or 0.0
    visits_by_id = {v.id: v for v in visits}

    # 1. A money outcome with no payment row — pay it, or stop claiming it.
    have_payment = {p.visit_id for p in pays if p.visit_id}
    for v in visits:
        if v.outcome not in MONEY or v.id in have_payment:
            continue
        c = cases.get(v.case_id)
        if not c:
            continue
        r = _stable_rng("pay", c.case_number, v.check_in_time.date().isoformat())
        remaining = round((c.target_amount or 0) - paid_total[c.id], 2)
        if remaining <= 0:
            v.outcome = (VisitOutcome.PTP if v.outcome == VisitOutcome.PART_PAID_PTP
                         else VisitOutcome.REVISIT)
            fixed["visit_outcome_downgraded"] += 1
            continue
        amount = _field_payment(v.outcome, remaining, r)
        if v.outcome == VisitOutcome.PAID_FULL and amount < remaining - 0.01:
            v.outcome = VisitOutcome.PART_PAID
        db.add(Payment(
            id=_uid(), case_id=c.id, visit_id=v.id, agent_id=v.agent_id,
            amount=amount, mode=r.choice(PAYMENT_MODES), status=PaymentStatus.VERIFIED,
            receipt_number=f"RCP{r.randint(10000000, 99999999)}",
            payment_date=v.check_in_time + timedelta(minutes=r.randint(5, 30)),
            receipt_sms_sent=True,
        ))
        paid_total[c.id] += amount
        fixed["payments_created"] += 1

    # 2. A PTP outcome with no promise recorded — record it.
    have_ptp = {p.visit_id for p in ptps if p.visit_id}
    for v in visits:
        if v.outcome not in (VisitOutcome.PTP, VisitOutcome.PART_PAID_PTP) or v.id in have_ptp:
            continue
        c = cases.get(v.case_id)
        if not c:
            continue
        r = _stable_rng("ptp", c.case_number, v.check_in_time.date().isoformat())
        vday = v.check_in_time.date()
        # Never lands on today: a back-filled promise must not join the
        # scripted "due today" set the demo screens are built around.
        committed = vday + timedelta(days=r.randint(3, 15))
        if committed >= today:
            committed = today + timedelta(days=r.randint(2, 12))
            status, paid_amt = PTPStatus.ACTIVE, 0.0
        elif r.random() < 0.62:
            status, paid_amt = PTPStatus.HONORED, round((c.target_amount or 5000) * 0.85, 2)
        else:
            status, paid_amt = PTPStatus.BROKEN, 0.0
        db.add(PTP(
            id=_uid(), case_id=c.id, visit_id=v.id, agent_id=v.agent_id,
            committed_amount=round(float(c.target_amount or 5000.0), 2),
            committed_date=committed, actual_paid_amount=paid_amt, status=status,
        ))
        fixed["ptps_created"] += 1
    db.flush()

    # 3. A promise dated before the visit that took it.
    for p in ptps:
        v = visits_by_id.get(p.visit_id) if p.visit_id else None
        if v and p.committed_date < v.check_in_time.date():
            c = cases.get(p.case_id)
            r = _stable_rng("ptpdate", c.case_number if c else p.case_id,
                            v.check_in_time.date().isoformat())
            p.committed_date = v.check_in_time.date() + timedelta(days=r.randint(3, 15))
            fixed["ptp_dates_moved"] += 1

    # 3b. Nobody collects on a Sunday — the app states so on its own compliance
    #     panel. Anything that landed there moves back to the Saturday.
    for v in visits:
        if v.check_in_time.date().weekday() == 6:
            v.check_in_time = v.check_in_time - timedelta(days=1)
            if v.check_out_time:
                v.check_out_time = v.check_out_time - timedelta(days=1)
            fixed["sunday_visits_moved"] += 1
    for p in pays:
        if p.payment_date.date().weekday() == 6:
            p.payment_date = p.payment_date - timedelta(days=1)
            fixed["sunday_payments_moved"] += 1
    db.flush()

    # 4. A promise still ACTIVE after its due date has passed. Left alone these
    #    accumulate forever and inflate the "due" counts on every screen.
    for p in ptps:
        if p.status == PTPStatus.ACTIVE and p.committed_date < today:
            p.status = PTPStatus.BROKEN
            fixed["stale_ptps_broken"] += 1
    db.flush()

    # 5. Case status has to match what the case actually holds.
    live_ptp_cases = {
        p.case_id for p in db.query(PTP).filter(PTP.status == PTPStatus.ACTIVE).all()
    }
    visited_cases = {v.case_id for v in visits}
    for c in cases.values():
        collected = round(paid_total[c.id], 2)
        if c.status in (CaseStatus.CLOSED, CaseStatus.WRITTEN_OFF, CaseStatus.ESCALATED):
            continue
        if c.status == CaseStatus.PTP_SET and c.id not in live_ptp_cases:
            c.status = (CaseStatus.PAID if collected >= (c.target_amount or 0) - 0.01 and collected > 0
                        else CaseStatus.PARTIALLY_PAID if collected > 0
                        else CaseStatus.IN_PROGRESS)
            fixed["ptp_set_without_promise"] += 1
        elif c.status == CaseStatus.ASSIGNED and c.id in visited_cases:
            c.status = (CaseStatus.PARTIALLY_PAID if 0 < collected < (c.target_amount or 0)
                        else CaseStatus.PAID if collected > 0 else CaseStatus.IN_PROGRESS)
            fixed["assigned_but_visited"] += 1
        if abs((c.collected_amount or 0) - collected) > 0.01:
            c.collected_amount = collected
            fixed["collected_amount_resynced"] += 1
    db.flush()

    # 6. Today's beat must agree with today's visits. A case worked today belongs
    #    on today's route — otherwise the manager sees it ticked "visited" while
    #    it sits outside the beat, and the beat's own counter disagrees.
    day_visits = defaultdict(set)
    day_money = defaultdict(float)
    for v in db.query(Visit).all():
        day_visits[(v.agent_id, v.check_in_time.date())].add(v.case_id)
    for p in db.query(Payment).all():
        day_money[(p.agent_id, p.payment_date.date())] += p.amount or 0.0
    # Inside the recent window every working day is backed by real rows, so a
    # beat there with no visits behind it cannot claim completed stops.
    recent = today - timedelta(days=30)
    for b in db.query(Beat).filter(Beat.beat_date >= recent, Beat.beat_date <= today).all():
        if not day_visits.get((b.agent_id, b.beat_date)) and (b.cases_completed or 0) > 0:
            b.cases_completed = 0
            b.amount_collected = 0.0
            fixed["empty_days_stood_down"] += 1

    # Any day that has real visit rows must have a beat that agrees with them.
    real_days = {d for (_, d) in day_visits}
    for b in db.query(Beat).filter(Beat.beat_date.in_(real_days)).all():
        key = (b.agent_id, b.beat_date)
        worked = day_visits.get(key, set())
        if not worked:
            continue
        ids = list(b.ordered_case_ids or [])
        for cid in worked:
            if cid not in ids:
                ids.append(cid)
                fixed["visited_cases_added_to_beat"] += 1
        b.ordered_case_ids = ids
        b.total_cases = len(ids)
        if b.is_leave_day:                 # worked, so it was not a leave day
            b.is_leave_day = False
            b.leave_type = None
            b.leave_remarks = None
            fixed["leave_days_with_visits"] += 1
        if (b.cases_completed or 0) != len(worked):
            b.cases_completed = len(worked)
            fixed["beat_completed_resynced"] += 1
        money = round(day_money.get(key, 0.0), 2)
        if abs((b.amount_collected or 0) - money) > 0.01:
            b.amount_collected = money
            fixed["beat_collected_resynced"] += 1
        b.total_target_amount = round(
            sum(float(cases[cid].target_amount or 0) for cid in ids if cid in cases), 2)
    db.flush()

    # 7. allocation_date has to place each case on exactly one recent day.
    #    The manager's case list filters on it, so if every case that was worked
    #    today is stamped with today — including ones first worked on the 3rd —
    #    the earlier days end up with visits but no cases, and filtering to the
    #    3rd returns nothing. Today's route is reserved first (it drives the
    #    "today's cases" views); every other case goes to the earliest day in
    #    the window on which it was actually worked.
    window_start = today - timedelta(days=30)
    todays_beats = db.query(Beat).filter(Beat.beat_date == today).all()
    claimed: set[str] = set()
    for b in todays_beats:
        for cid in (b.ordered_case_ids or []):
            if cid in cases:
                claimed.add(cid)
                if cases[cid].allocation_date != today.strftime("%Y-%m-%d"):
                    cases[cid].allocation_date = today.strftime("%Y-%m-%d")
                    fixed["allocation_dates_set"] += 1
    older = sorted({d for (_, d) in day_visits if window_start <= d < today})
    for day in older:                      # oldest first — first worked wins
        for b in db.query(Beat).filter(Beat.beat_date == day).all():
            for cid in (b.ordered_case_ids or []):
                if cid in claimed or cid not in cases:
                    continue
                claimed.add(cid)
                if cases[cid].allocation_date != day.strftime("%Y-%m-%d"):
                    cases[cid].allocation_date = day.strftime("%Y-%m-%d")
                    fixed["allocation_dates_set"] += 1
    db.flush()
    return dict(fixed)


def seed():
    print("Resetting DB schema...")
    # Drop stale tables not tracked by current metadata (removed models) before drop_all
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS documents CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS agent_leaves CASCADE"))
    Base.metadata.drop_all(bind=engine)
    _drop_all_enums(engine)
    Base.metadata.create_all(bind=engine)
    print("  Schema ready.\n")
    db = SessionLocal()
    now  = datetime.now(timezone.utc)
    today = date.today()
    # ── Managers ──────────────────────────────────────────────────────────────
    print(f"[1/11] Seeding {N_MANAGERS} managers + admin...")
    # Fixed manager profiles — names, DOB, phone never change between re-seeds
    MANAGER_PROFILES = [
        {
            "email": "manager1@tiqcollect.in",
            "phone": "9810000001",
            "full_name": "Vikram Malhotra",
            "date_of_birth": "1981-03-14",   # age 45
        },
        {
            "email": "manager2@tiqcollect.in",
            "phone": "9810000002",
            "full_name": "Sunita Kapoor",
            "date_of_birth": "1986-07-22",   # age 39
        },
    ]
    managers: list[User] = []
    for profile in MANAGER_PROFILES:
        u = User(
            id=_uid(),
            email=profile["email"],
            phone=profile["phone"],
            full_name=profile["full_name"],
            date_of_birth=profile["date_of_birth"],
            hashed_password=hash_password("Manager@123"),
            role=UserRole.AGENCY_MANAGER,
            is_active=True, is_verified=True,
        )
        db.add(u)
        managers.append(u)
    admin = User(
        id=_uid(), email="admin@tiqcollect.in", phone="9000000000",
        full_name="System Admin",
        hashed_password=hash_password("Admin@123"),
        role=UserRole.AGENCY_ADMIN,
        is_active=True, is_verified=True,
    )
    db.add(admin)
    db.commit()
    # ── Agents ────────────────────────────────────────────────────────────────
    print(f"[2/11] Seeding {N_AGENTS} agents...")
    # Fixed agent roster — names and DOBs never change between re-seeds.
    # Agents 001–015 → Manager1 (Vikram Malhotra)
    # Agents 016–018 → Manager2 (Sunita Kapoor)
    AGENT_ROSTER = [
        # (full_name,               dob,          phone)
        ("Arjun Singh Chauhan",    "1994-04-12", "9770000001"),  # 001
        ("Piyush Sharma",          "1997-09-05", "9770000002"),  # 002 — demo agent (Gurugram)
        ("Rajesh Kumar Yadav",     "1990-11-23", "9770000003"),  # 003
        ("Nitesh Gupta Agarwal",   "1995-03-18", "9770000004"),  # 004
        ("Mohammed Zafar Khan",    "1992-07-30", "9770000005"),  # 005
        ("Anil Kumar Mishra",      "1998-01-07", "9770000006"),  # 006
        ("Suresh Chand Tiwari",    "1988-06-14", "9770000007"),  # 007
        ("Karan Rawat Singh",      "1996-12-02", "9770000008"),  # 008
        ("Deepak Narayan Joshi",   "1993-08-25", "9770000009"),  # 009
        ("Sunil Kumar Sharma",     "1991-05-19", "9770000010"),  # 010
        ("Vivek Prasad Dubey",     "1999-02-28", "9770000011"),  # 011
        ("Pankaj Kumar Sinha",     "1994-10-11", "9770000012"),  # 012
        ("Rahul Dev Pandey",       "1989-03-07", "9770000013"),  # 013
        ("Mohan Lal Nair",         "1997-07-16", "9770000014"),  # 014
        ("Akash Ratan Verma",      "1993-01-29", "9770000015"),  # 015
        ("Sanjay Mohan Gupta",     "1990-09-03", "9770000016"),  # 016 — Manager2 team starts
        ("Devraj Anand Kapoor",    "1995-04-21", "9770000017"),  # 017
        ("Rohit Anand Saxena",     "1992-12-15", "9770000018"),  # 018
    ]
    # Permanent home address + exact home coords per agent (same index as AGENT_ROSTER)
    # City mapping: CITIES[0]=Delhi, CITIES[1]=Noida, CITIES[2]=Gurugram
    # AGENT_CITY_IDX drives city/state; AGENT_HOME drives actual lat/lon and territory label
    AGENT_HOME = [
        # idx 00: EMP0001 Arjun Singh — Delhi
        ("Lajpat Nagar III, New Delhi",           28.5641, 77.2432),
        # idx 01: EMP0002 Priya Sharma — Gurugram Sector 44 (DEMO agent, keep exact coords)
        ("Sector 44, Gurugram",                   28.455151, 77.071623),
        # idx 02: EMP0003 Rajesh Kumar — Gurugram
        ("DLF Phase 2, Gurugram",                 28.4793, 77.0861),
        # idx 03: EMP0004 Neha Gupta — Delhi
        ("Rohini Sector 11, New Delhi",           28.7008, 77.1162),
        # idx 04: EMP0005 Mohammed Khan — Noida
        ("Sector 18, Noida",                      28.5677, 77.3285),
        # idx 05: EMP0006 Anita Mishra — Gurugram
        ("Sohna Road, Gurugram",                  28.4310, 77.0380),
        # idx 06: EMP0007 Suresh Tiwari — Delhi
        ("Dwarka Sector 6, New Delhi",            28.5921, 77.0460),
        # idx 07: EMP0008 Kavita Rawat — Noida
        ("Sector 62, Noida",                      28.6265, 77.3667),
        # idx 08: EMP0009 Deepak Joshi — Gurugram
        ("Sector 15, Gurugram",                   28.4836, 77.0487),
        # idx 09: EMP0010 Sunita Sharma — Delhi
        ("Saket, New Delhi",                      28.5225, 77.2112),
        # idx 10: EMP0011 Vivek Dubey — Noida
        ("Sector 44, Noida",                      28.5573, 77.3478),
        # idx 11: EMP0012 Pooja Sinha — Gurugram
        ("Sector 56, Gurugram",                   28.4230, 77.0945),
        # idx 12: EMP0013 Rahul Pandey — Delhi
        ("Janakpuri, New Delhi",                  28.6219, 77.0855),
        # idx 13: EMP0014 Meena Nair — Noida
        ("Sector 12, Greater Noida",              28.4744, 77.5040),
        # idx 14: EMP0015 Akash Verma — Gurugram
        ("Palam Vihar, Gurugram",                 28.5143, 77.0345),
        # idx 15: EMP0016 Sanjay Gupta — Noida
        ("Sector 37, Noida",                      28.5730, 77.3262),
        # idx 16: EMP0017 Divya Kapoor — Gurugram
        ("Golf Course Rd, Gurugram",              28.4620, 77.1059),
        # idx 17: EMP0018 Rohit Saxena — Delhi
        ("Mayur Vihar Phase 1, New Delhi",        28.6080, 77.2958),
    ]
    agents: list[Agent] = []
    # Fixed city assignment per agent index so territory doesn't shift between seeds
    AGENT_CITY_IDX = [0,1,2,0,1,2,0,1,2,0,1,2,0,1,2, 1,2,0]
    for i in range(N_AGENTS):
        full_name, dob, phone = AGENT_ROSTER[i]
        city, state, _, _ = CITIES[AGENT_CITY_IDX[i]]
        home_area, base_lat, base_lon = AGENT_HOME[i]
        tier = random.choices(
            [AgentTier.TIER_1, AgentTier.TIER_2, AgentTier.TIER_3],
            weights=[20, 40, 40]
        )[0]
        ranking = {"TIER_1": random.uniform(72, 100),
                   "TIER_2": random.uniform(40, 72),
                   "TIER_3": random.uniform(5, 40)}[tier.value]
        assigned_manager = managers[0] if i < 15 else managers[1]
        u = User(
            id=_uid(),
            email=f"agent{i+1:03d}@tiqcollect.in",
            phone=phone,
            full_name=full_name,
            date_of_birth=dob,
            hashed_password=hash_password("Agent@123"),
            role=UserRole.FIELD_AGENT,
            is_active=True, is_verified=True,
        )
        db.add(u)
        a = Agent(
            id=_uid(), user_id=u.id,
            employee_code=f"EMP{i+1:04d}",
            id_card_number=f"TIQID{i+1:05d}",
            agency_id=AGENCY_ID,
            manager_user_id=assigned_manager.id,
            base_latitude=base_lat, base_longitude=base_lon,
            territory=home_area,
            languages_spoken=random.sample(LANGUAGES, random.randint(1, 3)),
            specialization=random.choice(list(AgentSpecialization)),
            max_cases_per_day=random.randint(10, 18),
            vehicle_type=random.choice(["TWO_WHEELER", "FOUR_WHEELER", "PUBLIC_TRANSPORT"]),
            status=AgentStatus.ON_DUTY,  # will be overridden below for today's off-duty agents
            tier=tier,
            ranking_score=round(ranking, 2),
            lifetime_collection_rate=round(random.uniform(0.15, 0.65), 3),
        )
        db.add(a)
        agents.append(a)
    db.commit()
    # Build manager -> agent lookup for leave approval
    manager_for_agent: dict[str, User] = {}
    for i, ag in enumerate(agents):
        manager_for_agent[ag.id] = managers[0] if i < 15 else managers[1]
    # 2-3 random agents are OFF_DUTY today (not agent002 — they must be on duty for demo)
    n_off_today = random.randint(2, 3)
    off_candidates = [a for a in agents if a.employee_code != "EMP0002"]
    off_duty_today = random.sample(off_candidates, min(n_off_today, len(off_candidates)))
    off_duty_ids = {a.id for a in off_duty_today}
    for a in off_duty_today:
        a.status = AgentStatus.OFF_DUTY
    db.commit()
    print(f"  {len(off_duty_ids)} agents set OFF_DUTY today: {[a.employee_code for a in off_duty_today]}")
    # ── Customers (delinquent pool: SMA-1, SMA-2, NPA) ──────────────────────
    print(f"[3/11] Seeding {N_CUSTOMERS} customers (~35% 30-60 DPD / ~35% 60-90 DPD / ~30% 90+ DPD)...")
    customers: list[Customer] = []
    # Build geo zone centers: 6 zones per agent, spread 5-12 km from agent home.
    # Customers scatter within ~1.5 km of each zone center — so the full coverage
    # per agent spans roughly 10-15 km radius, with 3-5 customers per neighbourhood.
    _ZONE_KM = [(0.0, 0.0), (5.0, 4.0), (-4.0, 7.0), (9.0, -3.0), (-7.0, 2.0), (3.0, -9.0)]
    _zone_centers: list[tuple[float, float, str, str]] = []
    for _ai, (_area, _hlat, _hlon) in enumerate(AGENT_HOME):
        _c, _s, _, _ = CITIES[AGENT_CITY_IDX[_ai]]
        for _dk, _dl in _ZONE_KM:
            _zlat = _hlat + _dk / 111.0
            _zlon = _hlon + _dl / (111.0 * math.cos(math.radians(_hlat)))
            _zone_centers.append((_zlat, _zlon, _c, _s))
    for i in range(N_CUSTOMERS):
        _zlat, _zlon, city, state = random.choice(_zone_centers)
        lat, lon = _jitter_coords(_zlat, _zlon, 1.5)  # ~1.5 km scatter within each zone
        cibil = random.randint(300, 680)  # NPA pool: lower CIBIL
        # `dpd_hint` is gone. It was drawn here, used to set risk_score, and
        # thrown away — the customer's actual loan DPDs are drawn independently
        # at [4/11], so the score described a delinquency belonging to no loan
        # of theirs. Customers are now created unscored and take the column
        # defaults; [11c] gives them a value from their own loans.
        c = Customer(
            id=_uid(),
            customer_ref=f"CUST{i+1:06d}",
            full_name=fake.name(),
            date_of_birth=fake.date_of_birth(minimum_age=25, maximum_age=62).strftime("%Y-%m-%d"),
            gender=random.choice(["MALE", "MALE", "FEMALE"]),
            pan_masked=f"XXXXX{random.randint(1000, 9999)}X",
            aadhaar_masked=f"XXXXXXXX{random.randint(1000, 9999)}",
            phone_primary=f"9{random.randint(100000000, 999999999):09d}",
            phone_alternate=f"8{random.randint(100000000, 999999999):09d}" if random.random() > 0.45 else None,
            email=fake.email() if random.random() > 0.55 else None,
            address_line1=fake.street_address(),
            city=city, state=state,
            pincode=str(random.randint(100000, 799999)),
            latitude=lat, longitude=lon,
            # risk_category / risk_score deliberately omitted — the defaults
            # (MEDIUM, 50.0) now mean "not yet scored" until [11c].
            cibil_score=cibil,
            language_preference=random.choice(LANGUAGES),
            customer_segment=random.choice(CUSTOMER_SEGMENTS),
            is_hostile=random.random() < 0.06,
            requires_female_agent=random.random() < 0.08,
            do_not_contact=False,
            fraud_flag=random.random() < 0.02,
        )
        db.add(c)
        customers.append(c)
    db.commit()
    # ── Loans (SMA-1 / SMA-2 / NPA — all delinquent, ABC Bank) ──────────────
    print(f"[4/11] Seeding {N_LOANS} loans (SMA-1 30-60 DPD / SMA-2 60-90 DPD / NPA 90+ DPD, ABC Bank)...")
    loans: list[Loan] = []
    customer_pool = customers[:]
    random.shuffle(customer_pool)
    for i in range(N_LOANS):
        customer = customer_pool[i % len(customer_pool)]
        loan_type = random.choice(LOAN_TYPES)
        sanctioned = round(random.choice([
            random.uniform(50000,  300000),
            random.uniform(300000, 1500000),
            random.uniform(1500000, 5000000),
        ]), -3)
        disbursed = sanctioned * random.uniform(0.90, 1.00)
        interest_rate = random.uniform(10.0, 24.0)
        tenure_months = random.choice([12, 24, 36, 48, 60, 84, 120])
        r = interest_rate / 1200
        emi = round((disbursed * r * (1 + r) ** tenure_months) / ((1 + r) ** tenure_months - 1), 2)
        # Force DPD 31+ (NPA pool)
        dpd = random.choices(DPD_CHOICES, DPD_WEIGHTS)[0]
        bucket = _dpd_to_bucket(dpd)
        disbursement_dt = today - timedelta(days=random.randint(180, 1800))
        outstanding_pct = random.uniform(0.35, 0.95)
        outstanding_principal = round(disbursed * outstanding_pct, 2)
        outstanding_interest = round(outstanding_principal * (interest_rate / 100) * random.uniform(0.08, 0.35), 2)
        overdue = round(emi * (dpd // 30 + 1) * random.uniform(0.85, 1.15), 2)
        total_outstanding = outstanding_principal + outstanding_interest + overdue + round(random.uniform(0, 3000), 2)
        status = LoanStatus.ACTIVE
        if dpd >= 90:
            status = random.choices([LoanStatus.ACTIVE, LoanStatus.NPA], weights=[25, 75])[0]
        legal_st = "NONE"
        if dpd >= 90:
            legal_st = random.choices(
                ["NONE", "NOTICE_SENT", "SARFAESI", "SUIT_FILED"],
                weights=[60, 25, 10, 5]
            )[0]
        settle_st = "NONE"
        if dpd >= 120:
            settle_st = random.choices(
                ["NONE", "OFFERED", "NEGOTIATING"],
                weights=[70, 20, 10]
            )[0]
        bank_risk = round(min(100, dpd / 90 * 50 + (750 - customer.cibil_score) / 450 * 50 + random.uniform(-5, 5)), 1)
        # The customer.risk_score * 0.3 term is gone: at this point nothing has
        # been scored, so it could only have contributed the unscored default.
        priority_score = min(100, dpd / 90 * 40 + outstanding_principal / 500000 * 30)
        l = Loan(
            id=_uid(),
            loan_account_number=f"LN{random.randint(100000000, 999999999)}",
            customer_id=customer.id,
            loan_type=loan_type,
            bank_name="ABC Bank",
            branch_code=f"BR{random.randint(1000, 9999)}",
            sanctioned_amount=sanctioned,
            disbursed_amount=round(disbursed, 2),
            outstanding_principal=outstanding_principal,
            outstanding_interest=outstanding_interest,
            total_outstanding=round(total_outstanding, 2),
            overdue_amount=overdue,
            emi_amount=emi,
            disbursement_date=disbursement_dt.strftime("%Y-%m-%d"),
            maturity_date=(disbursement_dt + timedelta(days=tenure_months * 30)).strftime("%Y-%m-%d"),
            tenure_months=tenure_months,
            last_payment_date=(today - timedelta(days=dpd + random.randint(0, 15))).strftime("%Y-%m-%d"),
            last_payment_amount=round(emi * random.uniform(0.4, 1.0), 2),
            next_due_date=(today + timedelta(days=random.randint(1, 30))).strftime("%Y-%m-%d"),
            dpd=dpd,
            dpd_bucket=bucket,
            status=status,
            interest_rate=round(interest_rate, 2),
            penal_charges=round(random.uniform(500, 8000), 2),
            npa_flag=dpd >= 90,
            legal_status=legal_st,
            settlement_status=settle_st,
            bank_risk_score=bank_risk,
            collection_priority_score=round(priority_score, 2),
            recovery_potential=_recovery_potential(dpd, loan_type, bank_risk),
        )
        db.add(l)
        loans.append(l)
    db.commit()
    # ── Historical Cases + Visits + Payments + PTPs (fully in sync) ─────────────
    # Case status is DERIVED from visit outcomes — never set independently.
    # Approach: create case ASSIGNED → simulate visits → derive final status.
    print(f"[5/11] Seeding {CASES_PER_MONTH * HISTORY_MONTHS} historical cases with visits in sync...")
    cases: list[Case] = []
    case_num = 1
    cust_lookup = {c.id: c for c in customers}
    loan_lookup = {l.id: l for l in loans}
    all_visits: list[Visit] = []
    total_payments = 0
    total_ptps = 0
    # Outcome → case status mapping
    OUTCOME_TO_STATUS = {
        VisitOutcome.PAID_FULL:      CaseStatus.PAID,
        VisitOutcome.PART_PAID:      CaseStatus.PARTIALLY_PAID,
        VisitOutcome.PART_PAID_PTP:  CaseStatus.PARTIALLY_PAID,
        VisitOutcome.PTP:            CaseStatus.PTP_SET,
        VisitOutcome.BROKEN_PTP:     CaseStatus.IN_PROGRESS,
        VisitOutcome.RTP:            CaseStatus.ESCALATED,
        VisitOutcome.DISPUTE:        CaseStatus.ESCALATED,
        VisitOutcome.NOT_AVAILABLE:  CaseStatus.IN_PROGRESS,
        VisitOutcome.ADDRESS_ISSUE:  CaseStatus.ESCALATED,
        VisitOutcome.DECEASED:       CaseStatus.CLOSED,
        VisitOutcome.REVISIT:        CaseStatus.IN_PROGRESS,
    }
    # For older cases, bias the LAST visit outcome toward resolution
    # month_age 5 = 6 months ago (most resolved); 0 = this month (active)
    def _final_outcome_weights(month_age: int, met_outcomes, met_weights, not_met_outcomes, not_met_weights):
        """For the last visit of a case, older cases should skew toward terminal outcomes."""
        if month_age >= 4:
            # Old cases: 60% chance of a terminal outcome on the last visit
            resolved_outcomes = [VisitOutcome.PAID_FULL, VisitOutcome.PART_PAID,
                                  VisitOutcome.RTP, VisitOutcome.ADDRESS_ISSUE, VisitOutcome.DECEASED]
            resolved_weights  = [25, 15, 20, 20, 5]
            active_outcomes   = [VisitOutcome.PTP, VisitOutcome.NOT_AVAILABLE, VisitOutcome.REVISIT]
            active_weights    = [30, 25, 10]
            if random.random() < 0.60:
                return True, resolved_outcomes, resolved_weights
            else:
                return False, active_outcomes, active_weights
        else:
            # Recent cases: use normal outcome weights
            if random.random() < 0.5:
                return True, met_outcomes, met_weights
            else:
                return False, not_met_outcomes, not_met_weights
    loan_pool = loans[:]
    random.shuffle(loan_pool)
    for month_age in range(HISTORY_MONTHS):
        month_start = today.replace(day=1) - timedelta(days=month_age * 30)
        for _ in range(CASES_PER_MONTH):
            loan = random.choice(loan_pool)
            customer = cust_lookup.get(loan.customer_id)
            if not customer:
                continue
            alloc_offset = random.randint(0, 27)
            alloc_date = month_start + timedelta(days=alloc_offset)
            if alloc_date > today:
                alloc_date = today - timedelta(days=random.randint(1, 5))
            # Don't assign today's historical cases to OFF_DUTY agents
            eligible = agents if alloc_date < today else [a for a in agents if a.id not in off_duty_ids]
            agent = random.choice(eligible if eligible else agents)
            score = loan.collection_priority_score
            c = Case(
                id=_uid(),
                case_number=f"CASE{case_num:07d}",
                customer_id=loan.customer_id,
                loan_id=loan.id,
                agent_id=agent.id,
                status=CaseStatus.ASSIGNED,   # will be updated below
                priority=_priority_from_score(score),
                target_amount=_cycle_target(loan.overdue_amount),
                collected_amount=0.0,          # updated from payments
                allocation_date=alloc_date.strftime("%Y-%m-%d"),
                allocation_score=score,
                is_ml_allocated=random.random() > 0.3,
                visit_count=0,                 # updated below
                max_visits_allowed=random.choice([3, 4, 5]),
                is_escalated=False,
                collection_stage=_collection_stage(loan.dpd, loan.legal_status),
                bank_ptp_date=(alloc_date - timedelta(days=random.randint(5, 30))).strftime("%Y-%m-%d") if random.random() < 0.2 else None,
                bank_agent_remarks=random.choice(BANK_REMARKS) if random.random() < 0.25 else None,
            )
            db.add(c)
            db.flush()  # get c.id
            cases.append(c)
            case_num += 1
            # ── Generate visits for this case ─────────────────────────────────
            met_prob, met_outcomes, met_weights, not_met_outcomes, not_met_weights = _visit_outcomes_for_dpd(loan.dpd)
            # Older cases with more time elapsed get more visits
            max_v = 3 if month_age >= 3 else 2 if month_age >= 1 else 1
            # Cases allocated <7 days ago may still be ASSIGNED (no visit yet)
            days_since_alloc = (today - alloc_date).days
            if days_since_alloc < 2 and random.random() < 0.4:
                # Fresh case — still ASSIGNED, no visit
                continue
            n_visits = random.randint(1, max_v)
            case_total_collected = 0.0
            last_outcome = None
            for v_num in range(n_visits):
                visit_offset = random.randint(v_num * 3 + 1, v_num * 3 + 10)
                visit_date = datetime.combine(
                    alloc_date + timedelta(days=visit_offset),
                    datetime.min.time()
                ).replace(
                    hour=random.randint(8, 18),
                    minute=random.randint(0, 59),
                    tzinfo=timezone.utc,
                )
                if visit_date.date() > today:
                    break
                is_last = (v_num == n_visits - 1)
                if is_last and month_age >= 2:
                    # For last visit of older cases: pick outcome that leads to resolution
                    use_met, final_outcomes, final_weights = _final_outcome_weights(
                        month_age, met_outcomes, met_weights, not_met_outcomes, not_met_weights
                    )
                    outcome = random.choices(final_outcomes, final_weights)[0]
                    met = outcome not in [VisitOutcome.NOT_AVAILABLE, VisitOutcome.ADDRESS_ISSUE,
                                          VisitOutcome.REVISIT, VisitOutcome.DECEASED]
                else:
                    met = random.random() < met_prob
                    if met:
                        outcome = random.choices(met_outcomes, met_weights)[0]
                    else:
                        outcome = random.choices(not_met_outcomes, not_met_weights)[0]
                if met:
                    not_met_reason = None
                    person = random.choices(
                        [PersonMet.BORROWER, PersonMet.SPOUSE, PersonMet.SIBLING, PersonMet.RELATIVE, PersonMet.NEIGHBOR],
                        weights=[55, 20, 10, 10, 5]
                    )[0]
                    def_reason = random.choice(list(DefaultReason)) if outcome in [VisitOutcome.RTP, VisitOutcome.DISPUTE] else None
                else:
                    not_met_reason = random.choice(list(NotMetReason))
                    person = None
                    def_reason = None
                # A visit must not report money it never records. Once the case
                # target is fully collected there is nothing left to pay, and
                # the payment block below would skip it — leaving a PAID_FULL
                # visit with no receipt behind it. Log the real thing instead.
                if (outcome in (VisitOutcome.PAID_FULL, VisitOutcome.PART_PAID,
                                VisitOutcome.PART_PAID_PTP)
                        and round(c.target_amount - case_total_collected, 2) <= 0):
                    outcome = (VisitOutcome.PTP if outcome == VisitOutcome.PART_PAID_PTP
                               else VisitOutcome.REVISIT)
                jlat, jlon = _jitter_coords(customer.latitude, customer.longitude, 0.08)
                dist = abs(jlat - customer.latitude) * 111000 + abs(jlon - customer.longitude) * 111000
                # ~25% of non-last visits on older cases were done by a prior agent (re-allocation)
                if not is_last and month_age >= 2 and random.random() < 0.25 and len(agents) > 1:
                    visit_agent_id = random.choice([a.id for a in agents if a.id != c.agent_id])
                else:
                    visit_agent_id = c.agent_id
                v = Visit(
                    id=_uid(),
                    case_id=c.id,
                    agent_id=visit_agent_id,
                    check_in_latitude=jlat, check_in_longitude=jlon,
                    check_in_time=visit_date,
                    check_out_time=visit_date + timedelta(minutes=random.randint(10, 45)),
                    distance_from_customer_metres=round(dist, 1),
                    geo_verified=dist < 200,
                    within_contact_hours=True,
                    customer_met=met,
                    outcome=outcome,
                    person_met=person,
                    default_reason=def_reason,
                    not_met_reason=not_met_reason,
                    agent_recording_transcript=random.choice(AGENT_TRANSCRIPTS.get(outcome.value, AGENT_TRANSCRIPTS["REVISIT"])),
                    borrower_recording_transcript=random.choice(BORROWER_TRANSCRIPTS.get(outcome.value, BORROWER_TRANSCRIPTS["REVISIT"])) if met else None,
                    ai_visit_note=random.choice(AI_VISIT_NOTES.get(outcome.value, AI_VISIT_NOTES["REVISIT"])),
                    visit_number=v_num + 1,
                    property_type=random.choice(["OWNED", "RENTED", "COMMERCIAL", "UNKNOWN"]) if met else None,
                    occupancy_status=random.choice(["OCCUPIED", "LOCKED", "VACATED", "NOT_FOUND"]) if met else None,
                    vehicle_present=random.choice([True, False, None]) if met else None,
                    business_running=random.choice([True, False, None]) if met else None,
                )
                db.add(v)
                db.flush()
                all_visits.append(v)
                last_outcome = outcome
                # ── Create payment record immediately ──────────────────────────
                if outcome in [VisitOutcome.PAID_FULL, VisitOutcome.PART_PAID, VisitOutcome.PART_PAID_PTP]:
                    remaining = round(c.target_amount - case_total_collected, 2)
                    if remaining > 0:
                        amount = _field_payment(outcome, remaining, random)
                        if outcome == VisitOutcome.PAID_FULL and amount < remaining - 0.01:
                            outcome = VisitOutcome.PART_PAID   # capped, not a full recovery
                            v.outcome = outcome
                            last_outcome = outcome
                        case_total_collected += amount
                        db.add(Payment(
                            id=_uid(),
                            case_id=c.id, visit_id=v.id,
                            agent_id=visit_agent_id,
                            amount=amount,
                            mode=random.choice(PAYMENT_MODES),
                            status=random.choices(
                                [PaymentStatus.VERIFIED, PaymentStatus.PENDING_VERIFICATION],
                                weights=[75, 25]
                            )[0],
                            receipt_number=f"RCP{random.randint(10000000, 99999999)}",
                            payment_date=visit_date,
                            receipt_sms_sent=True,
                        ))
                        total_payments += 1
                # ── Create PTP record immediately ──────────────────────────────
                if outcome in [VisitOutcome.PTP, VisitOutcome.PART_PAID_PTP]:
                    committed_date = alloc_date + timedelta(days=random.randint(3, 15))
                    ptp_status = random.choices(
                        [PTPStatus.ACTIVE, PTPStatus.HONORED, PTPStatus.BROKEN, PTPStatus.RESCHEDULED],
                        weights=[30, 35, 25, 10]
                    )[0]
                    # PTPs from old cases are unlikely to still be ACTIVE
                    if month_age >= 3 and ptp_status == PTPStatus.ACTIVE:
                        ptp_status = random.choice([PTPStatus.HONORED, PTPStatus.BROKEN])
                    db.add(PTP(
                        id=_uid(),
                        case_id=c.id, visit_id=v.id,
                        agent_id=c.agent_id,
                        committed_amount=round(loan.overdue_amount * random.uniform(0.5, 1.0), 2),
                        committed_date=committed_date,
                        status=ptp_status,
                        actual_paid_amount=round(loan.overdue_amount * 0.85, 2) if ptp_status == PTPStatus.HONORED else 0.0,
                        customer_reason=random.choice([
                            "Salary delayed", "Family emergency", "Out of city", "Bank issue",
                            "Medical expenses", "Crop failure", "Business loss", None
                        ]),
                        follow_up_date=committed_date - timedelta(days=1),
                    ))
                    total_ptps += 1
            # ── Derive case status from last visit outcome ─────────────────────
            if last_outcome is not None:
                c.status = OUTCOME_TO_STATUS.get(last_outcome, CaseStatus.IN_PROGRESS)
                c.visit_count = len([vv for vv in all_visits if vv.case_id == c.id])
                c.collected_amount = round(case_total_collected, 2)
                c.is_escalated = c.status == CaseStatus.ESCALATED
                if c.status == CaseStatus.PAID:
                    c.resolved_at = datetime.combine(
                        alloc_date + timedelta(days=random.randint(5, 25)), datetime.min.time()
                    ).replace(tzinfo=timezone.utc)
                elif c.status == CaseStatus.CLOSED:
                    c.resolved_at = datetime.combine(
                        alloc_date + timedelta(days=random.randint(1, 10)), datetime.min.time()
                    ).replace(tzinfo=timezone.utc)
        # Batch commit per month for performance
        db.commit()
        print(f"  Month -{month_age}: committed")
    db.commit()
    print(f"  Created {len(cases)} cases, {len(all_visits)} visits, {total_payments} payments, {total_ptps} PTPs — all in sync.")
    # ── Agent Performance (monthly snapshots — 6 months per agent) ────────────
    print(f"[9/10] Seeding agent performance snapshots (30 agents × 6 months)...")
    # Aggregate actual visit data per (agent_id, month)
    visit_agg: dict[tuple, dict] = defaultdict(lambda: {
        "total_visits": 0, "customer_met": 0, "ptps_set": 0,
    })
    for v in all_visits:
        key = (v.agent_id, v.check_in_time.strftime("%Y-%m"))
        visit_agg[key]["total_visits"] += 1
        if v.customer_met:
            visit_agg[key]["customer_met"] += 1
        if v.outcome in [VisitOutcome.PTP, VisitOutcome.PART_PAID_PTP]:
            visit_agg[key]["ptps_set"] += 1
    perf_months_seen: set[tuple] = set()
    for agent in agents:
        tier_mult = {"TIER_1": 1.0, "TIER_2": 0.65, "TIER_3": 0.35}[agent.tier.value]
        for month_offset in range(HISTORY_MONTHS):
            past_month = (today.replace(day=1) - timedelta(days=month_offset * 30))
            month_str = past_month.strftime("%Y-%m")
            key = (agent.id, month_str)
            if key in perf_months_seen:
                continue
            perf_months_seen.add(key)
            agg = visit_agg.get(key, {})
            # Use actual data if available; pad with tier-based estimates
            base_visits = agg.get("total_visits", 0)
            base_met = agg.get("customer_met", 0)
            base_ptps = agg.get("ptps_set", 0)
            # Add simulated historical volume on top of what we actually seeded
            sim_visits  = int(random.uniform(40, 180) * tier_mult) + base_visits
            sim_met     = int(sim_visits * random.uniform(0.35, 0.65)) + base_met
            sim_ptps_set = int(sim_visits * random.uniform(0.10, 0.30)) + base_ptps
            sim_ptps_hon = int(sim_ptps_set * random.uniform(0.30, 0.75))
            sim_collected = round(sim_visits * random.uniform(8000, 45000) * tier_mult, 2)
            coll_rate = round(random.uniform(0.10, 0.55) * (1 + tier_mult * 0.5), 3)
            ranking = min(100, coll_rate * 50 + (sim_ptps_hon / max(sim_ptps_set, 1)) * 25 + (sim_visits / 200) * 25)
            db.add(AgentPerformance(
                id=_uid(),
                agent_id=agent.id,
                month=month_str,
                total_visits=sim_visits,
                customer_met=sim_met,
                total_collected=sim_collected,
                ptps_set=sim_ptps_set,
                ptps_honored=sim_ptps_hon,
                collection_rate=coll_rate,
                ranking_score=round(ranking, 2),
                tier=AgentTier.TIER_1 if ranking >= 70 else AgentTier.TIER_2 if ranking >= 40 else AgentTier.TIER_3,
            ))
        # Update agent's current-month stats
        this_month = today.strftime("%Y-%m")
        agg_now = visit_agg.get((agent.id, this_month), {})
        agent.current_month_visits      = agg_now.get("total_visits", 0)
        agent.current_month_ptps_set    = agg_now.get("ptps_set", 0)
        agent.current_month_ptps_honored = 0
        agent.current_month_collections = 0.0
    db.commit()
    print(f"  Created {len(agents) * HISTORY_MONTHS} performance snapshots.")
    # ── Demo proximity customers for agent002 ─────────────────────────────────
    print(f"[10b] Seeding demo proximity customers for agent002...")
    agent002 = agents[1]
    agent002.base_latitude          = 28.455151
    agent002.base_longitude         = 77.071623
    agent002.last_known_latitude    = 28.455151
    agent002.last_known_longitude   = 77.071623
    agent002.territory              = "Sector 44, Gurugram"
    agent002.max_cases_per_day      = 15      # agent002 handles 15 cases/day
    # Customers are spread across 6 distinct Gurugram neighbourhoods (2–8 km apart)
    # so the route optimizer has genuine choices to make.
    # Agent002 home: 28.455151, 77.071623 (Sector 44)
    # All 15 are FIXED — no random fillers. Agent002's beat is always deterministic.
    # Agent002 home: 28.455151, 77.071623 (Sector 44, Gurugram)
    DEMO_PROXIMITY = [
        # 0 — Sector 44 (~810m — CRITICAL NPA — visited today 9:30 AM)
        {"name": "Rajesh Kumar Sharma",   "phone": "9876541001", "gender": "MALE",
         "lat": 28.4614, "lon": 77.0682, "addr1": "Plot 78-B, Sector 44",           "pin": "122003",
         "dpd": 95,  "outstanding": 185000.0, "loan_type": LoanType.PERSONAL, "priority": CasePriority.CRITICAL},
        # 1 — Sector 44 (~53m — in geo-fence range for visit testing)
        {"name": "Sunita Devi Agarwal",   "phone": "9876541002", "gender": "FEMALE",
         "lat": 28.455551, "lon": 77.071923, "addr1": "House 12, Sector 44",        "pin": "122003",
         "dpd": 62,  "outstanding": 94500.0,  "loan_type": LoanType.HOME,      "priority": CasePriority.HIGH},
        # 2 — Sector 44 (~38m — GEO-FENCE DEMO: dispute pending, unlocks visit)
        #   Showcase customer (DEMO0003). Name/phone come from .env
        #   (DEMO_CONTACT_NAME / DEMO_CONTACT_PHONE) so the demo number is a
        #   config swap, not a reseed — defaults to Balraj Singh / 8015935790.
        {"name": settings.DEMO_CONTACT_NAME, "phone": settings.DEMO_CONTACT_PHONE, "gender": "MALE",
         "lat": 28.455400, "lon": 77.071900, "addr1": "Plot 8, Sector 44",          "pin": "122003",
         "dpd": 45,  "outstanding": 62000.0,  "loan_type": LoanType.AUTO,      "priority": CasePriority.HIGH},
        # 3 — Sector 44 (~50m — GEO-FENCE DEMO: partially paid, unlocks visit)
        {"name": "Priya Singh Rawat",     "phone": "9311448017", "gender": "FEMALE",
         "lat": 28.454800, "lon": 77.071300, "addr1": "Flat 2A, Sector 44",         "pin": "122003",
         "dpd": 38,  "outstanding": 41000.0,  "loan_type": LoanType.PERSONAL,  "priority": CasePriority.MEDIUM},
        # 4 — MG Road / Sector 28 (~4.9km NE — escalated RTP — visited today 10:30 AM)
        {"name": "Deepak Verma Gupta",    "phone": "9876541005", "gender": "MALE",
         "lat": 28.4793, "lon": 77.0998, "addr1": "45 MG Road, Sector 28",          "pin": "122002",
         "dpd": 55,  "outstanding": 33500.0,  "loan_type": LoanType.BUSINESS,  "priority": CasePriority.MEDIUM},
        # 5 — Sector 44 (~67m — in geo-fence range for visit testing)
        {"name": "Anita Kapoor Malhotra", "phone": "9876541006", "gender": "FEMALE",
         "lat": 28.454651, "lon": 77.072023, "addr1": "C-22, Sector 44",            "pin": "122003",
         "dpd": 33,  "outstanding": 27000.0,  "loan_type": LoanType.PERSONAL,  "priority": CasePriority.LOW},
        # 6 — Sector 44 (~72m — in geo-fence range for visit testing)
        {"name": "Suresh Chand Bansal",   "phone": "9876541007", "gender": "MALE",
         "lat": 28.455751, "lon": 77.071323, "addr1": "15, Sector 44",              "pin": "122003",
         "dpd": 44,  "outstanding": 19500.0,  "loan_type": LoanType.GOLD,      "priority": CasePriority.LOW},
        # 7 — DLF Phase 1 (~3.0km NE — NPA 120 DPD — HOSTILE — escalated legal)
        {"name": "Ramesh Lal Gupta",      "phone": "9876541008", "gender": "MALE",
         "lat": 28.4724, "lon": 77.0985, "addr1": "B-44, DLF Phase 1",              "pin": "122022",
         "dpd": 120, "outstanding": 285000.0, "loan_type": LoanType.HOME,      "priority": CasePriority.CRITICAL,
         "is_hostile": True},
        # 8 — Sector 49 (~4.2km S — SMA-1 — DO NOT CONTACT (legal complaint filed))
        {"name": "Kavitha Rao Pillai",    "phone": "9876541009", "gender": "FEMALE",
         "lat": 28.4192, "lon": 77.0820, "addr1": "Flat 7C, Orchid Petals, Sector 49", "pin": "122018",
         "dpd": 44,  "outstanding": 78000.0,  "loan_type": LoanType.PERSONAL,  "priority": CasePriority.MEDIUM,
         "do_not_contact": True},
        # 9 — Sector 44 (~67m — in geo-fence range for visit testing)
        {"name": "Vikas Kumar Pandey",    "phone": "9876541010", "gender": "MALE",
         "lat": 28.454851, "lon": 77.071023, "addr1": "D-5, Sector 44",             "pin": "122003",
         "dpd": 72,  "outstanding": 145000.0, "loan_type": LoanType.BUSINESS,  "priority": CasePriority.HIGH},
        # 10 — Sector 44 (~74m — in geo-fence range for visit testing)
        {"name": "Meena Devi Tiwari",     "phone": "9876541011", "gender": "FEMALE",
         "lat": 28.455651, "lon": 77.071123, "addr1": "Plot 7, Sector 44",          "pin": "122003",
         "dpd": 55,  "outstanding": 89000.0,  "loan_type": LoanType.GOLD,      "priority": CasePriority.MEDIUM},
        # 11 — Sector 44 (~69m — in geo-fence range for visit testing)
        {"name": "Arun Prasad Singh",     "phone": "9876541012", "gender": "MALE",
         "lat": 28.454551, "lon": 77.071423, "addr1": "Flat 3C, Sector 44",         "pin": "122003",
         "dpd": 35,  "outstanding": 52000.0,  "loan_type": LoanType.PERSONAL,  "priority": CasePriority.LOW},
        # 12 — Sector 47 (~2.0km SW — SMA-2 HIGH — REQUIRES FEMALE AGENT)
        {"name": "Fatima Begum Ansari",   "phone": "9876541013", "gender": "FEMALE",
         "lat": 28.4495, "lon": 77.0580, "addr1": "25-B, Sheetla Mata Road, Sector 47", "pin": "122018",
         "dpd": 83,  "outstanding": 167000.0, "loan_type": LoanType.HOME,      "priority": CasePriority.HIGH,
         "requires_female_agent": True},
        # 13 — Sector 66 (~5.2km S — NPA 210 DPD — CRITICAL — legal notice served)
        {"name": "Rohit Kumar Singh",     "phone": "9876541014", "gender": "MALE",
         "lat": 28.4081, "lon": 77.0926, "addr1": "3rd Floor, Tower B, Sector 66",  "pin": "122101",
         "dpd": 210, "outstanding": 425000.0, "loan_type": LoanType.HOME,      "priority": CasePriority.CRITICAL},
        # 14 — Sector 31 (~2.5km W — SMA-1 — handover from prev agent, IN_PROGRESS)
        {"name": "Seema Agarwal Joshi",   "phone": "9876541015", "gender": "FEMALE",
         "lat": 28.4564, "lon": 77.0441, "addr1": "B-37, Sector 31",                "pin": "122001",
         "dpd": 48,  "outstanding": 63000.0,  "loan_type": LoanType.PERSONAL,  "priority": CasePriority.MEDIUM},
    ]
    demo_cases: list[Case] = []
    for idx, d in enumerate(DEMO_PROXIMITY):
        cust = Customer(
            id=_uid(), customer_ref=f"DEMO{idx+1:04d}",
            full_name=d["name"], date_of_birth="1985-06-15",
            gender=d.get("gender", "MALE"),
            pan_masked="XXXXX1234X", aadhaar_masked="XXXXXXXX5678",
            phone_primary=d["phone"],
            address_line1=d["addr1"],
            city="Gurugram", state="Haryana", pincode=d.get("pin", "122022"),
            latitude=d["lat"], longitude=d["lon"],
            # Neither risk_score NOR risk_category is hand-set here. They are one
            # logical value and must move together: the old 85/65/42 ladder had
            # no LOW branch, so the LOW demo customer was given 42.0 — a number
            # every threshold reads as MEDIUM, contradicting its own category.
            # Setting only one of the pair reproduces that same contradiction
            # from the other side. [11c] derives both, for these rows as for
            # every other. The "risk" key was dropped from the dicts above for
            # the same reason.
            cibil_score=520 if d["dpd"] > 60 else 600 if d["dpd"] > 30 else 650,
            language_preference="HINDI", customer_segment="SALARIED",
            is_hostile=d.get("is_hostile", False),
            do_not_contact=d.get("do_not_contact", False),
            requires_female_agent=d.get("requires_female_agent", False),
        )
        db.add(cust)
        db.flush()
        demo_loan = Loan(
            id=_uid(), loan_account_number=f"DEMOLOAN{idx+1:05d}",
            customer_id=cust.id, loan_type=d["loan_type"],
            bank_name="ABC Bank", branch_code="GGN044",
            sanctioned_amount=round(d["outstanding"] * 1.4, 2),
            disbursed_amount=round(d["outstanding"] * 1.3, 2),
            outstanding_principal=d["outstanding"],
            outstanding_interest=round(d["outstanding"] * 0.06, 2),
            penal_charges=round(d["outstanding"] * 0.01, 2),
            total_outstanding=round(d["outstanding"] * 1.07, 2),
            overdue_amount=round(d["outstanding"] * 0.30, 2),
            emi_amount=round(d["outstanding"] / 36, 2),
            disbursement_date="2022-01-15", maturity_date="2025-01-15",
            last_payment_date="2025-11-10",
            next_due_date=today.strftime("%Y-%m-%d"),
            dpd=d["dpd"],
            dpd_bucket=DPDBucket.NPA if d["dpd"] > 90 else DPDBucket.BUCKET_3 if d["dpd"] > 60 else DPDBucket.BUCKET_2,
            status=LoanStatus.NPA if d["dpd"] > 90 else LoanStatus.ACTIVE,
            interest_rate=14.5, npa_flag=d["dpd"] > 90,
            bank_risk_score=round(d["dpd"] / 120 * 100, 1),
            collection_priority_score=round(d["dpd"] / 120 * 100, 1),
            recovery_potential=_recovery_potential(
                d["dpd"], d["loan_type"], round(d["dpd"] / 120 * 100, 1)
            ),
        )
        db.add(demo_loan)
        db.flush()
        demo_case = Case(
            id=_uid(), case_number=f"DEMO{idx+1:06d}",
            customer_id=cust.id, loan_id=demo_loan.id,
            agent_id=agent002.id,
            status=CaseStatus.ASSIGNED,
            priority=d["priority"],
            target_amount=round(min(d["outstanding"] * 0.30, CYCLE_TARGET_CAP), 2),
            collected_amount=0.0,
            allocation_date=today.strftime("%Y-%m-%d"),
            allocation_score=float({"CRITICAL": 95, "HIGH": 75, "MEDIUM": 45, "LOW": 20}[d["priority"].value]),
            is_ml_allocated=False, visit_count=0, max_visits_allowed=5,
            collection_stage="NPA_RECOVERY" if d["dpd"] > 90 else "FIELD",
        )
        db.add(demo_case)
        cases.append(demo_case)
        demo_cases.append(demo_case)
        case_num += 1
    db.commit()
    # ── Demo historical visits — prior agents + agent002 history ──────────────
    # Covers all 7 demo customers, showing:
    #   • Re-allocation (visits by EMP0001/0003/0005/0007/0009 before agent002)
    #   • All case statuses: IN_PROGRESS, PTP_SET, PARTIALLY_PAID, ESCALATED, ASSIGNED
    #   • Broken PTPs, active PTPs, partial payments, receipts
    # This lets agent002 login demonstrate every feature in the app.
    print("[10b-hist] Seeding demo historical visits for agent002 customers...")
    _pa1 = agents[0]   # EMP0001 Arjun Singh (male)
    _pa3 = agents[2]   # EMP0003 Rajesh Kumar (male)
    _pa4 = agents[3]   # EMP0004 Neha Gupta (female)
    _pa5 = agents[4]   # EMP0005 Mohammed Khan (male)
    _pa6 = agents[5]   # EMP0006 Anita Devi (female)
    _pa7 = agents[6]   # EMP0007 Suresh Chand (male)
    _pa9 = agents[8]   # EMP0009 Deepak Joshi (male)
    def _past(days_ago: int, hour: int = 10, minute: int = 30) -> datetime:
        return datetime.combine(today - timedelta(days=days_ago), datetime.min.time()).replace(
            hour=hour, minute=minute, second=0, tzinfo=timezone.utc)
    # ── demo_cases[0]: Rajesh Kumar Sharma (Sector 44) ─ 3 prior visits → today is #4 ─
    rc = demo_cases[0]
    # Visit 1 — 42 days ago, EMP0001: door locked
    _v = Visit(id=_uid(), case_id=rc.id, agent_id=_pa1.id,
               check_in_latitude=28.4615, check_in_longitude=77.0683,
               check_in_time=_past(42, 10, 15), check_out_time=_past(42, 10, 28),
               distance_from_customer_metres=42.0, geo_verified=True, within_contact_hours=True,
               customer_met=False, outcome=VisitOutcome.NOT_AVAILABLE,
               not_met_reason=NotMetReason.PREMISES_LOCKED,
               agent_recording_transcript="Door locked. Neighbour says borrower leaves for factory shift by 7 AM. Best time: after 6 PM.",
               visit_number=1, property_type="RENTED", occupancy_status="LOCKED")
    db.add(_v); db.flush()
    # Visit 2 — 28 days ago, EMP0003: PTP taken ₹55,500 — now broken
    _v2 = Visit(id=_uid(), case_id=rc.id, agent_id=_pa3.id,
                check_in_latitude=28.4614, check_in_longitude=77.0682,
                check_in_time=_past(28, 18, 10), check_out_time=_past(28, 18, 40),
                distance_from_customer_metres=50.0, geo_verified=True, within_contact_hours=True,
                customer_met=True, outcome=VisitOutcome.PTP,
                person_met=PersonMet.BORROWER, default_reason=DefaultReason.JOB_LOSS,
                agent_recording_transcript="Met borrower evening. Laid off 3 months ago. Showed termination letter. PTP full ₹55,500 by month-end.",
                visit_number=2, property_type="RENTED", occupancy_status="OCCUPIED")
    db.add(_v2); db.flush()
    db.add(PTP(id=_uid(), case_id=rc.id,
               visit_id=_v2.id, agent_id=_pa3.id,
               committed_amount=55500.0, committed_date=today - timedelta(days=14),
               status=PTPStatus.BROKEN, customer_reason="Awaiting new job offer — cash flow dry."))
    # Visit 3 — 14 days ago, agent002: broken PTP + goodwill talk
    _v3 = Visit(id=_uid(), case_id=rc.id, agent_id=agent002.id,
                check_in_latitude=28.4614, check_in_longitude=77.0682,
                check_in_time=_past(14, 18, 30), check_out_time=_past(14, 19, 10),
                distance_from_customer_metres=40.0, geo_verified=True, within_contact_hours=True,
                customer_met=True, outcome=VisitOutcome.BROKEN_PTP,
                person_met=PersonMet.BORROWER, default_reason=DefaultReason.JOB_LOSS,
                agent_recording_transcript="PTP not honoured. Customer apologetic. Got part-time work. Offered ₹50K now, requested 2 more weeks for balance.",
                visit_number=3, property_type="RENTED", occupancy_status="OCCUPIED",
                vehicle_present=False, business_running=False)
    db.add(_v3); db.flush()
    rc.visit_count = 3  # today's visit (#4) is added in the today-activity block below
    # ── demo_cases[1]: Sunita Devi Agarwal (DLF Phase 4, ~4.6 km NE) ─ 3 visits ─
    sc = demo_cases[1]
    # Visit 1 — 35 days ago, EMP0005: not home
    _vs1 = Visit(id=_uid(), case_id=sc.id, agent_id=_pa5.id,
                 check_in_latitude=28.4913, check_in_longitude=77.0874,
                 check_in_time=_past(35, 11, 0), check_out_time=_past(35, 11, 14),
                 distance_from_customer_metres=65.0, geo_verified=True, within_contact_hours=True,
                 customer_met=False, outcome=VisitOutcome.NOT_AVAILABLE,
                 not_met_reason=NotMetReason.CUSTOMER_AWAY,
                 agent_recording_transcript="Nobody home. Building watchman says family went to native — will return in a week.",
                 visit_number=1, property_type="OWNED", occupancy_status="LOCKED")
    db.add(_vs1); db.flush()
    # Visit 2 — 20 days ago, EMP0005: partial payment ₹8,000 received
    _vs2 = Visit(id=_uid(), case_id=sc.id, agent_id=_pa5.id,
                 check_in_latitude=28.4912, check_in_longitude=77.0873,
                 check_in_time=_past(20, 10, 30), check_out_time=_past(20, 11, 5),
                 distance_from_customer_metres=55.0, geo_verified=True, within_contact_hours=True,
                 customer_met=True, outcome=VisitOutcome.PART_PAID,
                 person_met=PersonMet.BORROWER, default_reason=DefaultReason.MEDICAL,
                 agent_recording_transcript="Husband hospitalised last month. Partial ₹8,000 accepted in goodwill. Says full amount after discharge.",
                 visit_number=2, property_type="OWNED", occupancy_status="OCCUPIED",
                 vehicle_present=True, business_running=False)
    db.add(_vs2); db.flush()
    db.add(Payment(id=_uid(), case_id=sc.id,
                   visit_id=_vs2.id, agent_id=_pa5.id,
                   amount=8000.0, mode=PaymentMode.CASH, status=PaymentStatus.VERIFIED,
                   receipt_number="RCPT-HIST-S01", payment_date=_past(20, 10, 45)))
    sc.collected_amount = 8000.0
    sc.target_amount = 94500.0  # full outstanding; partial 8K already paid, PTP for remainder
    sc.visit_count = 2  # visit #3 (3 days ago) added in today-activity block below
    # ── demo_cases[2]: Mohammed Irfan Khan (Sector 44, ~38 m from agent home) ─ 2 visits ──
    mc = demo_cases[2]
    # Visit 1 — 21 days ago, EMP0007: premises locked
    _vm1 = Visit(id=_uid(), case_id=mc.id, agent_id=_pa7.id,
                 check_in_latitude=28.455410, check_in_longitude=77.071910,
                 check_in_time=_past(21, 9, 45), check_out_time=_past(21, 10, 0),
                 distance_from_customer_metres=80.0, geo_verified=True, within_contact_hours=True,
                 customer_met=False, outcome=VisitOutcome.NOT_AVAILABLE,
                 not_met_reason=NotMetReason.PREMISES_LOCKED,
                 agent_recording_transcript="Plot locked. Neighbour says owner goes out early.",
                 visit_number=1, property_type="OWNED", occupancy_status="LOCKED")
    db.add(_vm1); db.flush()
    # Visit 2 — 10 days ago, agent002: customer claims already paid directly to bank
    _vm2 = Visit(id=_uid(), case_id=mc.id, agent_id=agent002.id,
                 check_in_latitude=28.455400, check_in_longitude=77.071900,
                 check_in_time=_past(10, 11, 0), check_out_time=_past(10, 11, 28),
                 distance_from_customer_metres=18.0, geo_verified=True, within_contact_hours=True,
                 customer_met=True, outcome=VisitOutcome.DISPUTE,
                 person_met=PersonMet.BORROWER, default_reason=DefaultReason.ALREADY_PAID,
                 agent_recording_transcript="Customer agitated — claims he paid ₹30K directly to bank in Feb. Demanding settlement letter. Raised ticket with bank.",
                 visit_number=2, property_type="RENTED", occupancy_status="OCCUPIED",
                 vehicle_present=False, business_running=False)
    db.add(_vm2); db.flush()
    mc.status = CaseStatus.IN_PROGRESS
    mc.visit_count = 2
    # ── demo_cases[3]: Priya Singh Rawat (Sector 44, ~50 m from agent home) ─ 1 prior visit ─
    pc = demo_cases[3]
    _vp1 = Visit(id=_uid(), case_id=pc.id, agent_id=_pa1.id,
                 check_in_latitude=28.454810, check_in_longitude=77.071310,
                 check_in_time=_past(15, 12, 30), check_out_time=_past(15, 13, 5),
                 distance_from_customer_metres=42.0, geo_verified=True, within_contact_hours=True,
                 customer_met=True, outcome=VisitOutcome.PART_PAID,
                 person_met=PersonMet.BORROWER, default_reason=DefaultReason.SALARY_CUT,
                 agent_recording_transcript="Salary cut 30% post restructuring. Paid ₹5,000 as goodwill. Requesting 3-month EMI waiver.",
                 visit_number=1, property_type="RENTED", occupancy_status="OCCUPIED",
                 vehicle_present=False, business_running=False)
    db.add(_vp1); db.flush()
    db.add(Payment(id=_uid(), case_id=pc.id,
                   visit_id=_vp1.id, agent_id=_pa1.id,
                   amount=5000.0, mode=PaymentMode.UPI, status=PaymentStatus.VERIFIED,
                   receipt_number="RCPT-HIST-P01", payment_date=_past(15, 12, 50)))
    pc.status = CaseStatus.PARTIALLY_PAID  # will be updated to PTP_SET below
    pc.collected_amount = 5000.0           # will be updated to 13000 below
    pc.target_amount = 17300.0             # 5K + 8K collected + 4.3K PTP pending = true overdue
    pc.visit_count = 1                     # will be updated to 2 below
    # ── demo_cases[4]: Deepak Verma Gupta (MG Road, ~4.9 km NE) ─ 1 prior dispute ─
    dc = demo_cases[4]
    _vd1 = Visit(id=_uid(), case_id=dc.id, agent_id=_pa3.id,
                 check_in_latitude=28.4794, check_in_longitude=77.0999,
                 check_in_time=_past(30, 10, 0), check_out_time=_past(30, 10, 35),
                 distance_from_customer_metres=50.0, geo_verified=True, within_contact_hours=True,
                 customer_met=True, outcome=VisitOutcome.DISPUTE,
                 person_met=PersonMet.BORROWER, default_reason=DefaultReason.AMOUNT_DISPUTED,
                 agent_recording_transcript="Customer insists bank overcharged ₹12,000 in penal interest. Showed own statement. Calculation mismatch — escalating.",
                 visit_number=1, property_type="OWNED", occupancy_status="OCCUPIED",
                 vehicle_present=True, business_running=True)
    db.add(_vd1); db.flush()
    dc.status = CaseStatus.IN_PROGRESS
    dc.visit_count = 1  # today's visit adds to 2
    # ── demo_cases[5]: Anita Kapoor Malhotra (Palam Vihar, ~7.9 km N) ─ 1 revisit ─
    ac = demo_cases[5]
    _va1 = Visit(id=_uid(), case_id=ac.id, agent_id=_pa9.id,
                 check_in_latitude=28.5227, check_in_longitude=77.0515,
                 check_in_time=_past(12, 14, 0), check_out_time=_past(12, 14, 22),
                 distance_from_customer_metres=90.0, geo_verified=True, within_contact_hours=True,
                 customer_met=True, outcome=VisitOutcome.REVISIT,
                 person_met=PersonMet.RELATIVE,
                 agent_recording_transcript="Relative home. Says Anita is out of station for a wedding. Back in 10-12 days. Requested revisit.",
                 visit_number=1, property_type="OWNED", occupancy_status="OCCUPIED")
    db.add(_va1); db.flush()
    ac.status = CaseStatus.IN_PROGRESS   # will be updated to PTP_SET below
    ac.visit_count = 1                    # will be updated to 2 below
    # ── demo_cases[6]: Suresh Chand Bansal (Civil Lines, ~2.5 km NW) ─ 2 visits ──
    suc = demo_cases[6]
    # Visit 1 — 25 days ago, EMP0004: PTP ₹5,850
    _vsu1 = Visit(id=_uid(), case_id=suc.id, agent_id=agents[3].id,
                  check_in_latitude=28.4689, check_in_longitude=77.0549,
                  check_in_time=_past(25, 11, 30), check_out_time=_past(25, 12, 5),
                  distance_from_customer_metres=72.0, geo_verified=True, within_contact_hours=True,
                  customer_met=True, outcome=VisitOutcome.PTP,
                  person_met=PersonMet.BORROWER, default_reason=DefaultReason.OVER_LEVERAGED,
                  agent_recording_transcript="3 active loans. Cash flow very tight. PTP full ₹5,850 on 5th of month.",
                  visit_number=1, property_type="RENTED", occupancy_status="OCCUPIED",
                  vehicle_present=False, business_running=False)
    db.add(_vsu1); db.flush()
    db.add(PTP(id=_uid(), case_id=suc.id,
               visit_id=_vsu1.id, agent_id=agents[3].id,
               committed_amount=5850.0, committed_date=today - timedelta(days=10),
               status=PTPStatus.BROKEN, customer_reason="Gold loan EMI came due same date — funds exhausted."))
    # Visit 2 — 10 days ago, agent002: broken PTP + partial + new PTP
    _vsu2 = Visit(id=_uid(), case_id=suc.id, agent_id=agent002.id,
                  check_in_latitude=28.4688, check_in_longitude=77.0548,
                  check_in_time=_past(10, 16, 30), check_out_time=_past(10, 17, 5),
                  distance_from_customer_metres=74.0, geo_verified=True, within_contact_hours=True,
                  customer_met=True, outcome=VisitOutcome.PART_PAID_PTP,
                  person_met=PersonMet.BORROWER, default_reason=DefaultReason.OVER_LEVERAGED,
                  agent_recording_transcript="PTP broken. Customer apologetic. Paid ₹2,000 cash on the spot. New PTP ₹3,850 for end of month.",
                  visit_number=2, property_type="RENTED", occupancy_status="OCCUPIED",
                  vehicle_present=False, business_running=False)
    db.add(_vsu2); db.flush()
    db.add(Payment(id=_uid(), case_id=suc.id,
                   visit_id=_vsu2.id, agent_id=agent002.id,
                   amount=2000.0, mode=PaymentMode.CASH, status=PaymentStatus.VERIFIED,
                   receipt_number="RCPT-HIST-SU01", payment_date=_past(10, 16, 45)))
    db.add(PTP(id=_uid(), case_id=suc.id,
               visit_id=_vsu2.id, agent_id=agent002.id,
               committed_amount=3850.0, committed_date=today + timedelta(days=6),
               status=PTPStatus.ACTIVE, customer_reason="Will clear balance end of month."))
    suc.status = CaseStatus.PTP_SET
    suc.collected_amount = 2000.0
    suc.visit_count = 2
    # ── demo_cases[7]: Ramesh Lal Gupta (DLF Phase 1, ~3.0km NE) — HOSTILE, NPA ──
    rc7 = demo_cases[7]
    # Visit 1 — 28 days ago, EMP0005: premises locked
    db.add(Visit(id=_uid(), case_id=rc7.id, agent_id=_pa5.id,
                 check_in_latitude=28.4725, check_in_longitude=77.0986,
                 check_in_time=_past(28, 10, 0), check_out_time=_past(28, 10, 15),
                 distance_from_customer_metres=70.0, geo_verified=True, within_contact_hours=True,
                 customer_met=False, outcome=VisitOutcome.NOT_AVAILABLE,
                 not_met_reason=NotMetReason.PREMISES_LOCKED,
                 agent_recording_transcript="House locked. Neighbour says owner travels often for business.",
                 visit_number=1, property_type="OWNED", occupancy_status="LOCKED"))
    # Visit 2 — 14 days ago, EMP0003: hostile confrontation, legal warning issued
    _vr7 = Visit(id=_uid(), case_id=rc7.id, agent_id=_pa3.id,
                 check_in_latitude=28.4724, check_in_longitude=77.0985,
                 check_in_time=_past(14, 11, 30), check_out_time=_past(14, 12, 5),
                 distance_from_customer_metres=45.0, geo_verified=True, within_contact_hours=True,
                 customer_met=True, outcome=VisitOutcome.RTP,
                 person_met=PersonMet.BORROWER, default_reason=DefaultReason.AMOUNT_DISPUTED,
                 agent_recording_transcript="Customer became aggressive — threatened to file harassment complaint. Left legal demand notice at door. Escalation recommended.",
                 visit_number=2, property_type="OWNED", occupancy_status="OCCUPIED",
                 vehicle_present=True, business_running=False)
    db.add(_vr7); db.flush()
    rc7.status = CaseStatus.ESCALATED
    rc7.is_escalated = True
    rc7.escalation_reason = EscalationReason.CUSTOMER_ABSCONDED
    rc7.escalated_at = _past(14, 12, 5)
    rc7.escalation_notes = "Customer hostile, threatened legal action. Pending legal team review."
    rc7.visit_count = 2
    # ── demo_cases[8]: Kavitha Rao Pillai (Sector 49, ~4.2km S) — DO NOT CONTACT ──
    kc = demo_cases[8]
    # Visit 1 — 22 days ago, EMP0001: dispute — customer filed complaint with bank
    _vk1 = Visit(id=_uid(), case_id=kc.id, agent_id=_pa1.id,
                 check_in_latitude=28.4193, check_in_longitude=77.0821,
                 check_in_time=_past(22, 14, 0), check_out_time=_past(22, 14, 28),
                 distance_from_customer_metres=55.0, geo_verified=True, within_contact_hours=True,
                 customer_met=True, outcome=VisitOutcome.DISPUTE,
                 person_met=PersonMet.BORROWER, default_reason=DefaultReason.ALREADY_PAID,
                 agent_recording_transcript="Customer insists loan was settled in Dec. Has bank receipt screenshot — amount mismatch. Filed grievance with ombudsman.",
                 visit_number=1, property_type="RENTED", occupancy_status="OCCUPIED",
                 vehicle_present=False, business_running=False)
    db.add(_vk1); db.flush()
    kc.status = CaseStatus.IN_PROGRESS
    kc.visit_count = 1
    # do_not_contact flag is already set on the Customer object from DEMO_PROXIMITY
    # ── demo_cases[9]: Vikas Kumar Pandey (DLF Phase 2, ~3.3km NE) — PTP due today ──
    vc = demo_cases[9]
    # Visit 1 — 32 days ago, EMP0007: not home
    db.add(Visit(id=_uid(), case_id=vc.id, agent_id=_pa7.id,
                 check_in_latitude=28.4811, check_in_longitude=77.0861,
                 check_in_time=_past(32, 9, 30), check_out_time=_past(32, 9, 45),
                 distance_from_customer_metres=82.0, geo_verified=True, within_contact_hours=True,
                 customer_met=False, outcome=VisitOutcome.NOT_AVAILABLE,
                 not_met_reason=NotMetReason.CUSTOMER_AWAY,
                 agent_recording_transcript="Office closed — told he's in client meeting. Will return by 6 PM.",
                 visit_number=1, property_type="OWNED", occupancy_status="LOCKED"))
    # Visit 2 — 20 days ago, EMP0009: partial ₹20K + broken PTP
    _vv2 = Visit(id=_uid(), case_id=vc.id, agent_id=_pa9.id,
                 check_in_latitude=28.4810, check_in_longitude=77.0860,
                 check_in_time=_past(20, 17, 0), check_out_time=_past(20, 17, 45),
                 distance_from_customer_metres=28.0, geo_verified=True, within_contact_hours=True,
                 customer_met=True, outcome=VisitOutcome.PART_PAID_PTP,
                 person_met=PersonMet.BORROWER, default_reason=DefaultReason.BUSINESS_FAILURE,
                 agent_recording_transcript="Business down 40%. Paid ₹20,000. PTP for remaining ₹1,25,000 in 10 days.",
                 visit_number=2, property_type="OWNED", occupancy_status="OCCUPIED",
                 vehicle_present=True, business_running=True)
    db.add(_vv2); db.flush()
    db.add(Payment(id=_uid(), case_id=vc.id,
                   visit_id=_vv2.id, agent_id=_pa9.id,
                   amount=20000.0, mode=PaymentMode.UPI, status=PaymentStatus.VERIFIED,
                   receipt_number="RCPT-HIST-V01", payment_date=_past(20, 17, 20)))
    db.add(PTP(id=_uid(), case_id=vc.id,
               visit_id=_vv2.id, agent_id=_pa9.id,
               committed_amount=125000.0, committed_date=today - timedelta(days=10),
               status=PTPStatus.BROKEN, customer_reason="Client delayed payment, funds locked."))
    # Visit 3 — 8 days ago, agent002: broken PTP, new PTP set (due today+2)
    _vv3 = Visit(id=_uid(), case_id=vc.id, agent_id=agent002.id,
                 check_in_latitude=28.4809, check_in_longitude=77.0859,
                 check_in_time=_past(8, 11, 15), check_out_time=_past(8, 11, 55),
                 distance_from_customer_metres=33.0, geo_verified=True, within_contact_hours=True,
                 customer_met=True, outcome=VisitOutcome.PTP,
                 person_met=PersonMet.BORROWER, default_reason=DefaultReason.BUSINESS_FAILURE,
                 agent_recording_transcript="PTP broken. Client payment finally cleared. New PTP ₹1,25,000 due today — business account funded.",
                 visit_number=3, property_type="OWNED", occupancy_status="OCCUPIED",
                 vehicle_present=True, business_running=True)
    db.add(_vv3); db.flush()
    db.add(PTP(id=_uid(), case_id=vc.id,
               visit_id=_vv3.id, agent_id=agent002.id,
               committed_amount=125000.0, committed_date=today,
               status=PTPStatus.ACTIVE, customer_reason="Awaiting client wire transfer clearing today."))
    vc.status = CaseStatus.PTP_SET
    vc.collected_amount = 20000.0
    vc.visit_count = 3
    # ── demo_cases[10]: Meena Devi Tiwari (Sector 23, ~2.8km W) — GOLD LOAN, partial ──
    mc10 = demo_cases[10]
    # Visit 1 — 30 days ago, EMP0003: paid ₹15K towards gold loan
    _vm10a = Visit(id=_uid(), case_id=mc10.id, agent_id=_pa3.id,
                   check_in_latitude=28.4657, check_in_longitude=77.0498,
                   check_in_time=_past(30, 10, 0), check_out_time=_past(30, 10, 35),
                   distance_from_customer_metres=60.0, geo_verified=True, within_contact_hours=True,
                   customer_met=True, outcome=VisitOutcome.PART_PAID,
                   person_met=PersonMet.BORROWER, default_reason=DefaultReason.SALARY_CUT,
                   agent_recording_transcript="Salary delayed. Paid ₹15,000 in cash. Requesting 2 more months.",
                   visit_number=1, property_type="OWNED", occupancy_status="OCCUPIED",
                   vehicle_present=False, business_running=False)
    db.add(_vm10a); db.flush()
    db.add(Payment(id=_uid(), case_id=mc10.id,
                   visit_id=_vm10a.id, agent_id=_pa3.id,
                   amount=15000.0, mode=PaymentMode.CASH, status=PaymentStatus.VERIFIED,
                   receipt_number="RCPT-HIST-M10A", payment_date=_past(30, 10, 15)))
    # Visit 2 — 12 days ago, EMP0007: another ₹10K payment
    _vm10b = Visit(id=_uid(), case_id=mc10.id, agent_id=_pa7.id,
                   check_in_latitude=28.4656, check_in_longitude=77.0497,
                   check_in_time=_past(12, 15, 30), check_out_time=_past(12, 16, 10),
                   distance_from_customer_metres=40.0, geo_verified=True, within_contact_hours=True,
                   customer_met=True, outcome=VisitOutcome.PART_PAID,
                   person_met=PersonMet.BORROWER, default_reason=DefaultReason.SALARY_CUT,
                   agent_recording_transcript="Paid another ₹10,000 cash. Struggling with multiple EMIs. Requesting settlement letter.",
                   visit_number=2, property_type="OWNED", occupancy_status="OCCUPIED",
                   vehicle_present=False, business_running=False)
    db.add(_vm10b); db.flush()
    db.add(Payment(id=_uid(), case_id=mc10.id,
                   visit_id=_vm10b.id, agent_id=_pa7.id,
                   amount=10000.0, mode=PaymentMode.CASH, status=PaymentStatus.VERIFIED,
                   receipt_number="RCPT-HIST-M10B", payment_date=_past(12, 15, 45)))
    mc10.status = CaseStatus.PARTIALLY_PAID
    mc10.collected_amount = 25000.0
    mc10.visit_count = 2
    # ── demo_cases[11]: Arun Prasad Singh (Sector 48, ~4.1km S) — NEW CASE ──
    ac11 = demo_cases[11]
    # Visit 1 — 7 days ago, EMP0001: initial contact, not available
    db.add(Visit(id=_uid(), case_id=ac11.id, agent_id=_pa1.id,
                 check_in_latitude=28.4211, check_in_longitude=77.0741,
                 check_in_time=_past(7, 9, 15), check_out_time=_past(7, 9, 28),
                 distance_from_customer_metres=88.0, geo_verified=True, within_contact_hours=True,
                 customer_met=False, outcome=VisitOutcome.NOT_AVAILABLE,
                 not_met_reason=NotMetReason.CUSTOMER_AWAY,
                 agent_recording_transcript="Flat locked, security says tenant leaves early for work. Left notice. Case handed to EMP0002.",
                 visit_number=1, property_type="RENTED", occupancy_status="LOCKED"))
    ac11.status = CaseStatus.IN_PROGRESS   # will be updated to PTP_SET below
    ac11.visit_count = 1                    # will be updated to 2 below
    # ── demo_cases[12]: Fatima Begum Ansari (Sector 47, ~2.0km SW) — REQUIRES FEMALE AGENT ──
    fc = demo_cases[12]
    # Visit 1 — 25 days ago, EMP0004 (female): husband refused entry
    db.add(Visit(id=_uid(), case_id=fc.id, agent_id=_pa4.id,
                 check_in_latitude=28.4496, check_in_longitude=77.0581,
                 check_in_time=_past(25, 11, 0), check_out_time=_past(25, 11, 18),
                 distance_from_customer_metres=65.0, geo_verified=True, within_contact_hours=True,
                 customer_met=False, outcome=VisitOutcome.NOT_AVAILABLE,
                 not_met_reason=NotMetReason.CUSTOMER_AWAY,
                 agent_recording_transcript="Husband answered door — refused to call Fatima. Culturally sensitive. Requires female agent only.",
                 visit_number=1, property_type="OWNED", occupancy_status="OCCUPIED"))
    # Visit 2 — 10 days ago, EMP0006 (female): met customer, partial commitment
    _vf2 = Visit(id=_uid(), case_id=fc.id, agent_id=_pa6.id,
                 check_in_latitude=28.4495, check_in_longitude=77.0580,
                 check_in_time=_past(10, 14, 30), check_out_time=_past(10, 15, 20),
                 distance_from_customer_metres=38.0, geo_verified=True, within_contact_hours=True,
                 customer_met=True, outcome=VisitOutcome.PTP,
                 person_met=PersonMet.BORROWER, default_reason=DefaultReason.MEDICAL,
                 agent_recording_transcript="Met Fatima — husband ill, no income. Agreed to pay ₹20,000 once husband recovers. PTP in 15 days.",
                 visit_number=2, property_type="OWNED", occupancy_status="OCCUPIED",
                 vehicle_present=False, business_running=False)
    db.add(_vf2); db.flush()
    db.add(PTP(id=_uid(), case_id=fc.id,
               visit_id=_vf2.id, agent_id=_pa6.id,
               committed_amount=20000.0, committed_date=today + timedelta(days=5),
               status=PTPStatus.ACTIVE, customer_reason="Husband recovering — will arrange funds by weekend."))
    fc.status = CaseStatus.IN_PROGRESS
    fc.visit_count = 2
    # ── demo_cases[13]: Rohit Kumar Singh (Sector 66, ~5.2km S) — NPA 210 DPD, LEGAL ──
    rhc = demo_cases[13]
    # Visit 1 — 55 days ago, EMP0001: initial NPA contact
    db.add(Visit(id=_uid(), case_id=rhc.id, agent_id=_pa1.id,
                 check_in_latitude=28.4082, check_in_longitude=77.0927,
                 check_in_time=_past(55, 10, 0), check_out_time=_past(55, 10, 30),
                 distance_from_customer_metres=50.0, geo_verified=True, within_contact_hours=True,
                 customer_met=True, outcome=VisitOutcome.RTP,
                 person_met=PersonMet.BORROWER, default_reason=DefaultReason.BUSINESS_FAILURE,
                 agent_recording_transcript="Customer unresponsive. Job loss 7 months ago. No visible income. Demand letter issued.",
                 visit_number=1, property_type="OWNED", occupancy_status="OCCUPIED",
                 vehicle_present=True, business_running=False))
    # Visit 2 — 40 days ago, EMP0005: legal notice served
    db.add(Visit(id=_uid(), case_id=rhc.id, agent_id=_pa5.id,
                 check_in_latitude=28.4081, check_in_longitude=77.0926,
                 check_in_time=_past(40, 11, 45), check_out_time=_past(40, 12, 10),
                 distance_from_customer_metres=42.0, geo_verified=True, within_contact_hours=True,
                 customer_met=True, outcome=VisitOutcome.RTP,
                 person_met=PersonMet.BORROWER, default_reason=DefaultReason.BUSINESS_FAILURE,
                 agent_recording_transcript="Legal demand notice served in person. Customer asked for 30-day settlement window. Referred to legal team.",
                 visit_number=2, property_type="OWNED", occupancy_status="OCCUPIED",
                 vehicle_present=True, business_running=False))
    # Visit 3 — 22 days ago, EMP0003: customer hired lawyer, dispute
    db.add(Visit(id=_uid(), case_id=rhc.id, agent_id=_pa3.id,
                 check_in_latitude=28.4082, check_in_longitude=77.0927,
                 check_in_time=_past(22, 10, 30), check_out_time=_past(22, 11, 0),
                 distance_from_customer_metres=58.0, geo_verified=True, within_contact_hours=True,
                 customer_met=True, outcome=VisitOutcome.DISPUTE,
                 person_met=PersonMet.BORROWER, default_reason=DefaultReason.AMOUNT_DISPUTED,
                 agent_recording_transcript="Customer's lawyer present. Disputes interest calculation. Demands settlement with waiver. Case in pre-litigation.",
                 visit_number=3, property_type="OWNED", occupancy_status="OCCUPIED",
                 vehicle_present=True, business_running=False))
    # Visit 4 — 8 days ago, agent002: confirming legal path, case escalated
    _vrh4 = Visit(id=_uid(), case_id=rhc.id, agent_id=agent002.id,
                  check_in_latitude=28.4081, check_in_longitude=77.0925,
                  check_in_time=_past(8, 15, 0), check_out_time=_past(8, 15, 45),
                  distance_from_customer_metres=35.0, geo_verified=True, within_contact_hours=True,
                  customer_met=True, outcome=VisitOutcome.RTP,
                  person_met=PersonMet.BORROWER, default_reason=DefaultReason.AMOUNT_DISPUTED,
                  agent_recording_transcript="Lawyer still involved. Customer refuses to pay pending court outcome. Escalated to legal recovery unit.",
                  visit_number=4, property_type="OWNED", occupancy_status="OCCUPIED",
                  vehicle_present=True, business_running=False)
    db.add(_vrh4); db.flush()
    rhc.status = CaseStatus.ESCALATED
    rhc.is_escalated = True
    rhc.escalation_reason = EscalationReason.LEGAL_NOTICE_REQUIRED
    rhc.escalated_at = _past(8, 15, 45)
    rhc.escalation_notes = "Customer engaged lawyer. Pre-litigation — legal team handling."
    rhc.visit_count = 4
    # ── demo_cases[14]: Seema Agarwal Joshi (Sector 31, ~2.5km W) — HANDOVER ──
    sec14 = demo_cases[14]
    # Visit 1 — 20 days ago, EMP0009: initial contact, set payment plan
    db.add(Visit(id=_uid(), case_id=sec14.id, agent_id=_pa9.id,
                 check_in_latitude=28.4565, check_in_longitude=77.0442,
                 check_in_time=_past(20, 10, 45), check_out_time=_past(20, 11, 20),
                 distance_from_customer_metres=72.0, geo_verified=True, within_contact_hours=True,
                 customer_met=True, outcome=VisitOutcome.PTP,
                 person_met=PersonMet.BORROWER, default_reason=DefaultReason.SALARY_CUT,
                 agent_recording_transcript="Cooperative borrower. Income reduced post job change. Agreed on ₹10K/month repayment schedule.",
                 visit_number=1, property_type="RENTED", occupancy_status="OCCUPIED",
                 vehicle_present=False, business_running=False))
    # Visit 2 — 8 days ago, EMP0007: missed appointment, handed over
    _vs14 = Visit(id=_uid(), case_id=sec14.id, agent_id=_pa7.id,
                  check_in_latitude=28.4564, check_in_longitude=77.0441,
                  check_in_time=_past(8, 12, 0), check_out_time=_past(8, 12, 20),
                  distance_from_customer_metres=55.0, geo_verified=True, within_contact_hours=True,
                  customer_met=False, outcome=VisitOutcome.NOT_AVAILABLE,
                  not_met_reason=NotMetReason.CUSTOMER_AWAY,
                  agent_recording_transcript="Flat locked at agreed time. Called — phone switched off. Case handed over to EMP0002 for follow-up.",
                  visit_number=2, property_type="RENTED", occupancy_status="LOCKED")
    db.add(_vs14); db.flush()
    sec14.status = CaseStatus.IN_PROGRESS
    sec14.handover_notes = "Missed appointment — transfer to EMP0002. Customer previously cooperative; track via call first."
    sec14.visit_count = 2
    # ── demo_cases[3]: Priya Singh Rawat — visit 2 by agent002 (in-range ~50m) ──
    # First visit (EMP0001, 15d ago) collected ₹5,000. Now agent002 gets another ₹8K.
    _vp2 = Visit(id=_uid(), case_id=pc.id, agent_id=agent002.id,
                 check_in_latitude=28.454800, check_in_longitude=77.071300,
                 check_in_time=_past(7, 12, 0), check_out_time=_past(7, 12, 35),
                 distance_from_customer_metres=48.0, geo_verified=True, within_contact_hours=True,
                 customer_met=True, outcome=VisitOutcome.PART_PAID_PTP,
                 person_met=PersonMet.BORROWER, default_reason=DefaultReason.SALARY_CUT,
                 agent_recording_transcript="Revisited at home. Customer cooperative. Paid ₹8,000 via UPI. Salary cut still ongoing — promised final ₹4,300 by month-end.",
                 visit_number=2, property_type="RENTED", occupancy_status="OCCUPIED",
                 vehicle_present=False, business_running=False)
    db.add(_vp2); db.flush()
    db.add(Payment(id=_uid(), case_id=pc.id,
                   visit_id=_vp2.id, agent_id=agent002.id,
                   amount=8000.0, mode=PaymentMode.UPI, status=PaymentStatus.VERIFIED,
                   receipt_number="RCPT-HIST-P02", payment_date=_past(7, 12, 15)))
    db.add(PTP(id=_uid(), case_id=pc.id,
               visit_id=_vp2.id, agent_id=agent002.id,
               committed_amount=4300.0, committed_date=today + timedelta(days=4),
               status=PTPStatus.ACTIVE, customer_reason="Salary credit expected end of month. Will pay balance immediately."))
    pc.status = CaseStatus.PTP_SET
    pc.collected_amount = 13000.0
    pc.visit_count = 2
    # ── demo_cases[5]: Anita Kapoor Malhotra (Palam Vihar, ~7.9km) — add visit 2 ──
    # Only had 1 revisit (relative was home). Now agent002 follows up and gets a PTP.
    _va2 = Visit(id=_uid(), case_id=ac.id, agent_id=agent002.id,
                 check_in_latitude=28.5226, check_in_longitude=77.0514,
                 check_in_time=_past(6, 10, 0), check_out_time=_past(6, 10, 45),
                 distance_from_customer_metres=82.0, geo_verified=True, within_contact_hours=True,
                 customer_met=True, outcome=VisitOutcome.PTP,
                 person_met=PersonMet.BORROWER, default_reason=DefaultReason.SALARY_CUT,
                 agent_recording_transcript="Anita finally home. Cooperative — salary delayed 3 weeks due to company restructuring. Agreed to pay full ₹8,100 target as soon as salary credited.",
                 visit_number=2, property_type="OWNED", occupancy_status="OCCUPIED",
                 vehicle_present=False, business_running=False)
    db.add(_va2); db.flush()
    db.add(PTP(id=_uid(), case_id=ac.id,
               visit_id=_va2.id, agent_id=agent002.id,
               committed_amount=round(27000.0 * 0.30, 2),
               committed_date=today + timedelta(days=5),
               status=PTPStatus.ACTIVE, customer_reason="Salary credit expected this week. Will transfer same day."))
    ac.status = CaseStatus.PTP_SET
    ac.visit_count = 2
    # ── demo_cases[11]: Arun Prasad Singh (Sector 48, ~4.1km) — add visit 2 ──
    # Was locked on first visit. EMP0009 makes a second attempt and sets a PTP.
    _va11b = Visit(id=_uid(), case_id=ac11.id, agent_id=_pa9.id,
                   check_in_latitude=28.4210, check_in_longitude=77.0740,
                   check_in_time=_past(4, 18, 0), check_out_time=_past(4, 18, 40),
                   distance_from_customer_metres=72.0, geo_verified=True, within_contact_hours=True,
                   customer_met=True, outcome=VisitOutcome.PTP,
                   person_met=PersonMet.BORROWER, default_reason=DefaultReason.JOB_LOSS,
                   agent_recording_transcript="Met customer on second attempt (evening). Recently laid off — 30 days into job search. New offer letter received. PTP full ₹15,600 by month-end. Case transferred to EMP0002.",
                   visit_number=2, property_type="RENTED", occupancy_status="OCCUPIED",
                   vehicle_present=False, business_running=False)
    db.add(_va11b); db.flush()
    db.add(PTP(id=_uid(), case_id=ac11.id,
               visit_id=_va11b.id, agent_id=_pa9.id,
               committed_amount=round(52000.0 * 0.30, 2),
               committed_date=today + timedelta(days=6),
               status=PTPStatus.ACTIVE, customer_reason="New job starting next week. Will pay in full on first salary."))
    ac11.status = CaseStatus.PTP_SET
    ac11.visit_count = 2
    db.commit()
    # ── Today's demo visit activity for agent002 ───────────────────────────────
    def today_at(h: int, m: int = 0) -> datetime:
        return datetime.combine(today, datetime.min.time()).replace(
            hour=h, minute=m, second=0, tzinfo=timezone.utc
        )
    # Rajesh Kumar Sharma (Sector 44): PART_PAID_PTP at 9:30 AM
    rajesh_case = demo_cases[0]
    v_rajesh = Visit(
        id=_uid(), case_id=rajesh_case.id, agent_id=agent002.id,
        check_in_latitude=28.4614, check_in_longitude=77.0682,
        check_in_time=today_at(9, 30), check_out_time=today_at(10, 5),
        distance_from_customer_metres=40.0, geo_verified=True, within_contact_hours=True,
        customer_met=True, outcome=VisitOutcome.PART_PAID_PTP,
        person_met=PersonMet.BORROWER, default_reason=DefaultReason.JOB_LOSS,
        agent_recording_transcript="Paid Rs 50,000 cash. PTP Rs 1,35,000 on 25-Jun. Layoff - awaiting severance.",
        visit_number=4,
    )
    db.add(v_rajesh)
    db.flush()
    db.add(Payment(
        id=_uid(), case_id=rajesh_case.id,
        visit_id=v_rajesh.id, agent_id=agent002.id,
        amount=50000.0, mode=PaymentMode.CASH, status=PaymentStatus.PENDING_VERIFICATION,
        receipt_number="RCPT-DEMO-001", payment_date=today_at(9, 45),
    ))
    db.add(PTP(
        id=_uid(), case_id=rajesh_case.id,
        visit_id=v_rajesh.id, agent_id=agent002.id,
        committed_amount=135000.0, committed_date=today + timedelta(days=3),
        status=PTPStatus.ACTIVE,
        customer_reason="Salary credit expected 25th. Will transfer immediately.",
    ))
    rajesh_case.status = CaseStatus.PARTIALLY_PAID
    rajesh_case.collected_amount = 50000.0
    rajesh_case.visit_count = 4
    # Sunita Devi Agarwal: PTP due today (visited 3 days ago)
    sunita_case = demo_cases[1]
    past3 = datetime.combine(today - timedelta(days=3), datetime.min.time()).replace(
        hour=10, minute=15, tzinfo=timezone.utc
    )
    v_sunita = Visit(
        id=_uid(), case_id=sunita_case.id, agent_id=agent002.id,
        check_in_latitude=28.4912, check_in_longitude=77.0873,
        check_in_time=past3, check_out_time=past3 + timedelta(minutes=20),
        distance_from_customer_metres=55.0, geo_verified=True, within_contact_hours=True,
        customer_met=True, outcome=VisitOutcome.PTP,
        person_met=PersonMet.SPOUSE, default_reason=DefaultReason.MEDICAL,
        agent_recording_transcript="Spouse met. Borrower hospitalised. PTP full amount today on discharge.",
        visit_number=3,
    )
    db.add(v_sunita)
    db.flush()
    db.add(PTP(
        id=_uid(), case_id=sunita_case.id,
        visit_id=v_sunita.id, agent_id=agent002.id,
        committed_amount=86500.0, committed_date=today,
        status=PTPStatus.HONORED,  # honored — payment collected in today's follow-up visit
        customer_reason="Hospital discharge today. Will transfer immediately after.",
    ))
    sunita_case.status = CaseStatus.PTP_SET
    sunita_case.visit_count = 3
    # Sunita Devi Agarwal: today's PTP follow-up — full recovery at 11:45 AM
    v_sunita_today = Visit(
        id=_uid(), case_id=sunita_case.id, agent_id=agent002.id,
        check_in_latitude=28.4912, check_in_longitude=77.0873,
        check_in_time=today_at(11, 45), check_out_time=today_at(12, 20),
        distance_from_customer_metres=30.0, geo_verified=True, within_contact_hours=True,
        customer_met=True, outcome=VisitOutcome.PAID_FULL,
        person_met=PersonMet.BORROWER,
        agent_recording_transcript="PTP honoured. Borrower discharged this morning. Transferred remaining ₹86,500 via UPI. Requested NOC.",
        visit_number=4,
    )
    db.add(v_sunita_today)
    db.flush()
    db.add(Payment(
        id=_uid(), case_id=sunita_case.id,
        visit_id=v_sunita_today.id, agent_id=agent002.id,
        amount=86500.0, mode=PaymentMode.UPI, status=PaymentStatus.VERIFIED,
        receipt_number="RCPT-DEMO-003", payment_date=today_at(12, 0),
    ))
    sunita_case.status = CaseStatus.PAID
    sunita_case.collected_amount += 86500.0  # 8K partial + 86.5K today = 94.5K = target
    sunita_case.visit_count = 4
    # Deepak Verma Gupta (MG Road): RTP / escalation at 10:30 AM
    deepak_case = demo_cases[4]
    v_deepak = Visit(
        id=_uid(), case_id=deepak_case.id, agent_id=agent002.id,
        check_in_latitude=28.4793, check_in_longitude=77.0998,
        check_in_time=today_at(10, 30), check_out_time=today_at(11, 0),
        distance_from_customer_metres=45.0, geo_verified=True, within_contact_hours=True,
        customer_met=True, outcome=VisitOutcome.RTP,
        person_met=PersonMet.BORROWER, default_reason=DefaultReason.AMOUNT_DISPUTED,
        agent_recording_transcript="Claims bank overcharged interest. Refuses until bank sends corrected statement.",
        visit_number=2,
    )
    db.add(v_deepak)
    deepak_case.status = CaseStatus.ESCALATED
    deepak_case.is_escalated = True
    deepak_case.escalation_reason = EscalationReason.DISPUTED_AMOUNT
    deepak_case.escalated_at = today_at(10, 30)
    deepak_case.escalation_notes = "Customer disputes loan amount — requires bank statement."
    deepak_case.visit_count = 2
    agent002.current_month_visits += 4
    agent002.current_month_collections += 136500.0  # 50K (Rajesh) + 86.5K (Sunita)
    agent002.current_month_ptps_set += 2
    agent002.status = AgentStatus.ON_DUTY
    db.commit()
    # ── Today's 10 bank customers (ABC Bank daily batch — first-time arrivals) ──
    # These 10 have ZERO prior agency records: no visits, no payments, no PTPs.
    # All 10 go round-robin to non-demo ON_DUTY agents (agent002 is fully loaded with 15 demo cases).
    print("[10c] Seeding today's 10 bank-sourced first-time customers...")
    BANK_TODAY = [
        # ── 10 new-batch cases distributed to other ON_DUTY agents ──
        {"name": "Suresh Babu Reddy",      "phone": "9811200003", "dob": "1985-06-20",
         "gender": "MALE",   "dpd": 45,  "outstanding":  55000.0,
         "loan_type": LoanType.HOME,      "lat": 28.635000, "lon": 77.218000},
        {"name": "Meena Kumari Pillai",    "phone": "9811200004", "dob": "1991-02-14",
         "gender": "FEMALE", "dpd": 78,  "outstanding":  92000.0,
         "loan_type": LoanType.PERSONAL,  "lat": 28.542000, "lon": 77.192000},
        {"name": "Vinod Kumar Srivastava", "phone": "9811200005", "dob": "1976-09-08",
         "gender": "MALE",   "dpd": 95,  "outstanding": 210000.0,
         "loan_type": LoanType.AUTO,   "lat": 28.699000, "lon": 77.102000},
        {"name": "Poonam Devi Tomar",      "phone": "9811200006", "dob": "1988-04-30",
         "gender": "FEMALE", "dpd": 53,  "outstanding":  63000.0,
         "loan_type": LoanType.PERSONAL,  "lat": 28.589000, "lon": 77.316000},
        {"name": "Ashok Narayan Garg",     "phone": "9811200007", "dob": "1974-12-03",
         "gender": "MALE",   "dpd": 134, "outstanding": 315000.0,
         "loan_type": LoanType.BUSINESS,  "lat": 28.466000, "lon": 77.064000},
        {"name": "Rekha Singh Chauhan",    "phone": "9811200008", "dob": "1993-07-19",
         "gender": "FEMALE", "dpd": 42,  "outstanding":  48000.0,
         "loan_type": LoanType.HOME,      "lat": 28.620000, "lon": 77.374000},
        {"name": "Manish Kumar Trivedi",   "phone": "9811200009", "dob": "1982-01-27",
         "gender": "MALE",   "dpd": 88,  "outstanding": 127000.0,
         "loan_type": LoanType.PERSONAL,  "lat": 28.648000, "lon": 77.238000},
        {"name": "Sunita Bhatia Malhotra", "phone": "9811200010", "dob": "1987-10-11",
         "gender": "FEMALE", "dpd": 107, "outstanding": 176000.0,
         "loan_type": LoanType.AUTO,   "lat": 28.521000, "lon": 77.067000},
    ]
    # ON_DUTY agents (excluding demo agent) to receive the 8 distributed bank cases
    bank_eligible = [a for a in agents
                     if a.id not in off_duty_ids and a.employee_code != "EMP0002"]
    if not bank_eligible:
        bank_eligible = [a for a in agents if a.employee_code != "EMP0002"] or agents
    for idx, d in enumerate(BANK_TODAY):
        assigned_agent = bank_eligible[idx % len(bank_eligible)]
        cust = Customer(
            id=_uid(), customer_ref=f"BANK{idx+1:04d}",
            full_name=d["name"], date_of_birth=d["dob"], gender=d["gender"],
            pan_masked=f"XXXXX{5000+idx}X", aadhaar_masked=f"XXXXXXXX{6000+idx}",
            phone_primary=d["phone"],
            address_line1="House 12, Sector 56",
            city="Gurugram", state="Haryana", pincode="122011",
            latitude=d["lat"], longitude=d["lon"],
            # risk_score / risk_category both derived at [11c] — see the demo
            # block above for why neither is hand-set.
            cibil_score=540, language_preference="HINDI", customer_segment="SALARIED",
        )
        db.add(cust)
        db.flush()
        bank_loan = Loan(
            id=_uid(), loan_account_number=f"BANKLOAN{idx+1:05d}",
            customer_id=cust.id, loan_type=d["loan_type"],
            bank_name="ABC Bank", branch_code="GGN056",
            sanctioned_amount=round(d["outstanding"] * 1.5, 2),
            disbursed_amount=round(d["outstanding"] * 1.4, 2),
            outstanding_principal=d["outstanding"],
            outstanding_interest=round(d["outstanding"] * 0.07, 2),
            penal_charges=round(d["outstanding"] * 0.015, 2),
            total_outstanding=round(d["outstanding"] * 1.085, 2),
            overdue_amount=round(d["outstanding"] * 0.35, 2),
            emi_amount=round(d["outstanding"] / 36, 2),
            disbursement_date="2022-06-01", maturity_date="2025-06-01",
            last_payment_date=(today - timedelta(days=d["dpd"])).strftime("%Y-%m-%d"),
            next_due_date=today.strftime("%Y-%m-%d"),
            dpd=d["dpd"],
            dpd_bucket=DPDBucket.NPA if d["dpd"] > 90 else DPDBucket.BUCKET_3,
            status=LoanStatus.NPA if d["dpd"] > 90 else LoanStatus.ACTIVE,
            interest_rate=16.0, npa_flag=d["dpd"] > 90,
            bank_risk_score=round(d["dpd"] / 120 * 100, 1),
            collection_priority_score=round(d["dpd"] / 120 * 100, 1),
        )
        db.add(bank_loan)
        db.flush()
        bank_case = Case(
            id=_uid(), case_number=f"BANK{idx+1:06d}",
            customer_id=cust.id, loan_id=bank_loan.id,
            agent_id=assigned_agent.id,
            status=CaseStatus.ASSIGNED,
            priority=CasePriority.HIGH if d["dpd"] > 90 else CasePriority.MEDIUM,
            target_amount=round(min(d["outstanding"] * 0.35, CYCLE_TARGET_CAP), 2),
            collected_amount=0.0,
            allocation_date=today.strftime("%Y-%m-%d"),
            allocation_score=round(d["dpd"] / 120 * 100, 1),
            is_ml_allocated=True, visit_count=0, max_visits_allowed=4,
            collection_stage="NPA_RECOVERY" if d["dpd"] > 90 else "FIELD",
        )
        db.add(bank_case)
        case_num += 1
    db.commit()
    # agent002 has exactly 15 fixed DEMO cases — no random fillers needed.
    db.commit()
    # ── Today's field activity — all on-duty agents ───────────────────────────
    # Covers EVERY on-duty agent (except agent002 which has its own demo visits).
    # Each agent does 4-6 field visits today with a realistic NPA outcome mix.
    # This drives all manager dashboard "today" KPIs:
    #   cases_resolved_today, amount_collected_today, ptps_due_today, collection_rate_today
    print("[10d] Seeding today's field activity (all on-duty agents)...")
    rcpt_num = 300
    today_str = today.strftime("%Y-%m-%d")
    # NPA recovery outcome distribution for a working day
    _DAY_OUTCOMES = [
        VisitOutcome.PAID_FULL,     # 12%
        VisitOutcome.PART_PAID,     # 14%
        VisitOutcome.PART_PAID_PTP, # 8%
        VisitOutcome.PTP,           # 22%
        VisitOutcome.NOT_AVAILABLE, # 20%
        VisitOutcome.RTP,           # 8%
        VisitOutcome.REVISIT,       # 10%
        VisitOutcome.DISPUTE,       # 6%
    ]
    _DAY_WEIGHTS = [25, 28, 12, 14, 9, 4, 5, 3]   # ~45% of visits recover money:
    # at ~34% the day's recovery could never reach a believable share of
    # the day's target (it was landing at 4-22%).
    _REVISITABLE = {
        CaseStatus.ASSIGNED, CaseStatus.IN_PROGRESS,
        CaseStatus.PTP_SET, CaseStatus.PARTIALLY_PAID, CaseStatus.ESCALATED,
    }
    # Every agent works today, including the OFF_DUTY ones — that status means
    # "not currently checked in" (shift over, or not started), not "did nothing
    # today". Excluding them left 3 of 18 agents with no visits and no payments
    # at all on the manager's day view, which reads as missing data.
    active_today = [a for a in agents if a.employee_code != "EMP0002"]
    for act_agent in active_today:
        pool = [c for c in cases
                if c.agent_id == act_agent.id and c.status in _REVISITABLE]
        random.shuffle(pool)
        pool = pool[:20]
        if not pool:
            continue
        # 8-13 visits per agent to yield 100-150 case visits across the team
        n_vis = random.randint(8, 13)
        visit_cases = random.sample(pool, min(n_vis, len(pool)))
        outcomes_today = random.choices(_DAY_OUTCOMES, weights=_DAY_WEIGHTS, k=len(visit_cases))
        total_cash = 0.0
        for i, (act_case, outcome) in enumerate(zip(visit_cases, outcomes_today)):
            cust_obj = cust_lookup.get(act_case.customer_id)
            if not cust_obj:
                continue
            visit_hour = 8 + int(i * (9 / max(len(visit_cases), 1)))
            cin = today_at(min(visit_hour, 17), random.randint(5, 55))
            met = outcome not in (VisitOutcome.NOT_AVAILABLE, VisitOutcome.ADDRESS_ISSUE)
            v = Visit(
                id=_uid(), case_id=act_case.id, agent_id=act_agent.id,
                check_in_latitude=cust_obj.latitude, check_in_longitude=cust_obj.longitude,
                check_in_time=cin,
                check_out_time=cin + timedelta(minutes=random.randint(18, 45)),
                distance_from_customer_metres=round(random.uniform(15, 90), 1),
                geo_verified=True, within_contact_hours=True,
                customer_met=met, outcome=outcome,
                person_met=PersonMet.BORROWER if met else None,
                agent_recording_transcript=random.choice(AGENT_TRANSCRIPTS.get(outcome.value, AGENT_TRANSCRIPTS["REVISIT"])),
                borrower_recording_transcript=random.choice(BORROWER_TRANSCRIPTS.get(outcome.value, BORROWER_TRANSCRIPTS["REVISIT"])) if met else None,
                ai_visit_note=random.choice(AI_VISIT_NOTES.get(outcome.value, AI_VISIT_NOTES["REVISIT"])),
                visit_number=act_case.visit_count + 1,
            )
            db.add(v)
            db.flush()
            # Payments
            already = act_case.collected_amount or 0.0
            remaining_target = round(act_case.target_amount - already, 2)
            if outcome in (VisitOutcome.PAID_FULL, VisitOutcome.PART_PAID, VisitOutcome.PART_PAID_PTP) and remaining_target > 0:
                # Same doorstep ceiling as everywhere else — uncapped, a
                # PAID_FULL on a ₹30L NPA case booked ₹30L against one visit and
                # pushed the team's day total past ₹1 crore.
                cash = _field_payment(outcome, remaining_target, random)
                if outcome == VisitOutcome.PAID_FULL and cash < remaining_target - 0.01:
                    outcome = VisitOutcome.PART_PAID
                    v.outcome = outcome
                rcpt_num += 1
                db.add(Payment(
                    id=_uid(), case_id=act_case.id,
                    visit_id=v.id, agent_id=act_agent.id,
                    amount=cash,
                    mode=random.choice([PaymentMode.CASH, PaymentMode.UPI, PaymentMode.NEFT]),
                    status=PaymentStatus.PENDING_VERIFICATION,
                    receipt_number=f"RCPT-DAY-{rcpt_num:04d}",
                    payment_date=cin + timedelta(minutes=10),
                ))
                act_case.collected_amount = already + cash
                total_cash += cash
            # PTPs — create one future PTP and one due today (feeds ptps_due_today)
            if outcome in (VisitOutcome.PTP, VisitOutcome.PART_PAID_PTP):
                remaining = max(act_case.target_amount - (act_case.collected_amount or 0.0), 5000.0)
                db.add(PTP(
                    id=_uid(), case_id=act_case.id,
                    visit_id=v.id, agent_id=act_agent.id,
                    committed_amount=remaining,
                    committed_date=today + timedelta(days=random.randint(3, 10)),
                    status=PTPStatus.ACTIVE,
                ))
                db.add(PTP(
                    id=_uid(), case_id=act_case.id,
                    visit_id=v.id, agent_id=act_agent.id,
                    committed_amount=min(remaining * 0.4, 35000.0),
                    committed_date=today,
                    status=PTPStatus.ACTIVE,
                ))
                act_agent.current_month_ptps_set += 1
            # Case status update
            if outcome == VisitOutcome.PAID_FULL:
                act_case.status = CaseStatus.PAID
                act_case.collected_amount = act_case.target_amount  # ensure exact match
                act_case.resolved_at = cin
            elif outcome == VisitOutcome.PART_PAID:
                act_case.status = CaseStatus.PARTIALLY_PAID
            elif outcome in (VisitOutcome.PTP, VisitOutcome.PART_PAID_PTP):
                act_case.status = CaseStatus.PTP_SET
            elif outcome == VisitOutcome.RTP:
                act_case.status = CaseStatus.ESCALATED
                act_case.is_escalated = True
            act_case.visit_count += 1
        act_agent.current_month_visits += len(visit_cases)
        act_agent.current_month_collections += total_cash
    db.commit()
    # Refresh cust_lookup to include demo + bank customers added after initial build
    for c in db.query(Customer).all():
        cust_lookup[c.id] = c
    # ── Post-process: fill ai_visit_note + borrower_recording_transcript on all visits ──
    # Covers demo visits (hardcoded agent_recording_transcript but no ai/borrower fields)
    # and any visit that didn't get them via the random pools above.
    print("[10e] Back-filling ai_visit_note and borrower_recording_transcript on all visits...")
    from app.models.visit import Visit as _Visit
    for _v in db.query(_Visit).all():
        _k = _v.outcome.value if _v.outcome else "REVISIT"
        if _v.ai_visit_note is None:
            _v.ai_visit_note = random.choice(AI_VISIT_NOTES.get(_k, AI_VISIT_NOTES["REVISIT"]))
        if _v.borrower_recording_transcript is None and _v.customer_met:
            _v.borrower_recording_transcript = random.choice(BORROWER_TRANSCRIPTS.get(_k, BORROWER_TRANSCRIPTS["REVISIT"]))
    db.commit()
    print("  Back-fill complete.")
    # ── Beat history — 6 months of daily attendance for all agents ─────────────
    # Every Mon–Sat over 6 months = 1 Beat record per agent per working day.
    # Present days: status=COMPLETED with realistic visit stats.
    # Leave/absent days: status=CANCELLED, is_leave_day=True, leave_type set.
    print("[10f] Seeding 6-month Beat attendance history (all agents, Mon-Sat)...")
    WORKING_DAYS_HISTORY: list[date] = []
    _hist_start = today - timedelta(days=HISTORY_MONTHS * 30)
    _d = _hist_start
    while _d < today:
        if _d.weekday() < 6:   # Mon=0 … Sat=5; skip Sunday
            WORKING_DAYS_HISTORY.append(_d)
        _d += timedelta(days=1)
    # Agents who tend to take more leave (random, but seeded)
    _rng_leave = random.Random(99)  # deterministic sub-RNG so leave pattern is stable
    # Probability of leave per day per agent: ~4-6% → roughly 8-12 days leave per 6 months
    _LEAVE_TYPES = ["SICK_LEAVE", "CASUAL_LEAVE", "EARNED_LEAVE", "ABSENT"]
    _LEAVE_WEIGHTS = [35, 30, 20, 15]
    hist_beat_count = 0
    for _agent in agents:
        for _wd in WORKING_DAYS_HISTORY:
            is_leave = _rng_leave.random() < 0.052   # ~4 leaves per 6-month period per agent
            if is_leave:
                _lt = _rng_leave.choices(_LEAVE_TYPES, _LEAVE_WEIGHTS)[0]
                db.add(Beat(
                    id=_uid(),
                    agent_id=_agent.id,
                    beat_date=_wd,
                    beat_number=f"BEAT-{_wd.strftime('%Y%m%d')}-{_agent.id[:6].upper()}",
                    ordered_case_ids=[],
                    total_cases=0,
                    estimated_distance_km=0.0,
                    estimated_duration_minutes=0,
                    total_target_amount=0.0,
                    status=BeatStatus.CANCELLED,
                    cases_completed=0,
                    amount_collected=0.0,
                    is_ml_generated=False,
                    is_leave_day=True,
                    leave_type=_lt,
                    leave_remarks=_rng_leave.choice(LEAVE_REMARKS.get(_lt, ["Leave taken."])),
                ))
            else:
                # Present day — build a realistic beat with visit stats
                _n_cases = _rng_leave.randint(8, 16)
                _n_completed = _rng_leave.randint(4, _n_cases)
                _collected = round(_rng_leave.uniform(12000, 90000), 2)
                _dist_km = round(_rng_leave.uniform(20, 65), 2)
                _dur_min = int(_dist_km / 25 * 60) + _n_cases * 18
                _beat_st = BeatStatus.COMPLETED if _wd < today else BeatStatus.IN_PROGRESS
                db.add(Beat(
                    id=_uid(),
                    agent_id=_agent.id,
                    beat_date=_wd,
                    beat_number=f"BEAT-{_wd.strftime('%Y%m%d')}-{_agent.id[:6].upper()}",
                    ordered_case_ids=[],   # historical — actual case IDs not critical
                    total_cases=_n_cases,
                    estimated_distance_km=_dist_km,
                    estimated_duration_minutes=_dur_min,
                    total_target_amount=round(_collected * _rng_leave.uniform(2.0, 4.5), 2),
                    status=_beat_st,
                    cases_completed=_n_completed,
                    amount_collected=_collected,
                    is_ml_generated=True,
                    is_leave_day=False,
                    leave_type=None,
                ))
            hist_beat_count += 1
        db.commit()   # commit per agent for memory efficiency
    print(f"  Seeded {hist_beat_count} historical Beat records ({len(agents)} agents × {len(WORKING_DAYS_HISTORY)} working days).")
    # ── CallLog history — pre-visit phone calls for all cases ─────────────────
    # For each historical case, seed 1-3 call log entries spread before the first visit.
    # ~70% of cases get at least one call. Mix of outcomes with timing/scheduling intel.
    print("[10g] Seeding 6-month CallLog history (pre-visit calls for ~70% of cases)...")
    all_cases_for_calls = [c for c in cases if c.status != CaseStatus.ASSIGNED or random.random() < 0.3]
    all_call_logs_count = 0
    _CALL_OUTCOMES = list(CallOutcome)
    _CALL_WEIGHTS  = [40, 15, 8, 12, 10, 15]  # ANSWERED dominant, others spread
    _ANSWERED_SUBTYPES = [
        "ANSWERED_TIMING", "ANSWERED_UNAVAILABLE", "ANSWERED_PTP_CONFIRM",
        "ANSWERED_HOSTILE", "ANSWERED_PAYMENT_INTEL",
    ]
    for _case in all_cases_for_calls:
        if random.random() > 0.70:
            continue   # 30% of cases never got a pre-visit call
        _cust_obj = cust_lookup.get(_case.customer_id)
        if not _cust_obj:
            continue
        _alloc_dt = datetime.strptime(_case.allocation_date, "%Y-%m-%d").date() if isinstance(_case.allocation_date, str) else _case.allocation_date
        n_calls = random.randint(1, 3)
        for _ci in range(n_calls):
            _call_offset_days = random.randint(0, 3)
            _call_date = _alloc_dt + timedelta(days=_call_offset_days)
            if _call_date > today:
                _call_date = today - timedelta(days=1)
            _call_hour = random.randint(9, 18)
            _call_ts = datetime.combine(_call_date, datetime.min.time()).replace(
                hour=_call_hour, minute=random.randint(0, 59), tzinfo=timezone.utc)
            _outcome = random.choices(_CALL_OUTCOMES, _CALL_WEIGHTS)[0]
            _duration = random.randint(30, 240) if _outcome == CallOutcome.ANSWERED else (
                random.randint(5, 25) if _outcome == CallOutcome.BUSY else None
            )
            _answered = _outcome == CallOutcome.ANSWERED
            # Pick note subtype for ANSWERED calls to give scheduling intel variety
            if _answered:
                _subtype = random.choice(_ANSWERED_SUBTYPES)
                _note = random.choice(CALL_LOG_NOTES.get(_subtype, CALL_LOG_NOTES["ANSWERED_TIMING"]))
            else:
                _note = random.choice(CALL_LOG_NOTES.get(_outcome.value, CALL_LOG_NOTES["NO_ANSWER"]))
            # Structured intel fields — only meaningful for ANSWERED calls
            _best_time  = None
            _blocked_until = None
            _payment_intent = None
            _verbal_date = None
            _visit_feasible = None
            _alt_location = None
            if _answered:
                if _subtype == "ANSWERED_TIMING":
                    _best_time = random.choice(["before 10 AM", "after 6 PM", "11 AM to 1 PM", "between 1-3 PM", "morning before 9:30"])
                    _visit_feasible = True
                elif _subtype == "ANSWERED_UNAVAILABLE":
                    _blocked_until = today + timedelta(days=random.randint(3, 15))
                    _visit_feasible = False
                elif _subtype == "ANSWERED_PTP_CONFIRM":
                    _payment_intent = True
                    _visit_feasible = True
                    _verbal_date = today + timedelta(days=random.randint(0, 5))
                elif _subtype == "ANSWERED_HOSTILE":
                    _visit_feasible = False
                elif _subtype == "ANSWERED_PAYMENT_INTEL":
                    _payment_intent = True
                    _verbal_date = today + timedelta(days=random.randint(1, 20))
                    _visit_feasible = True
                    _best_time = random.choice(["after salary credit on 9th", "after 20th of month", "on pension day", None])
            # AI intel summary — a short structured extract from the call note
            _ai_intel = None
            if _answered and _best_time:
                _ai_intel = f"Customer reachable {_best_time}. {_note[:80]}..."
            elif _answered and _payment_intent and _verbal_date:
                _ai_intel = f"Payment intent confirmed. Verbal commitment: {_verbal_date.strftime('%d %b %Y')}. {_note[:60]}..."
            elif _answered and _blocked_until:
                _ai_intel = f"Do not visit until {_blocked_until.strftime('%d %b %Y')} — customer unavailable."
            db.add(CallLog(
                id=_uid(),
                case_id=_case.id,
                agent_id=_case.agent_id,
                customer_id=_case.customer_id,
                called_at=_call_ts,
                duration_seconds=_duration,
                outcome=_outcome,
                phone_used=random.choice(["PRIMARY", "PRIMARY", "ALTERNATE"]),
                customer_response_notes=_note,
                visit_feasible_today=_visit_feasible,
                best_time_to_visit=_best_time,
                blocked_until_date=_blocked_until,
                alternate_location_hint=_alt_location,
                payment_intent_signalled=_payment_intent,
                verbal_payment_date=_verbal_date,
                ai_intel_summary=_ai_intel,
            ))
            all_call_logs_count += 1
        if all_call_logs_count % 200 == 0 and all_call_logs_count > 0:
            db.commit()
    db.commit()
    print(f"  Seeded {all_call_logs_count} CallLog records across {len(all_cases_for_calls)} cases.")
    # ── Beats for today ────────────────────────────────────────────────────────
    # Route optimization pipeline (mirrors production allocator):
    #   1. Fetch agent's home coordinates as the depot.
    #   2. Build real road-time matrix via OSRM Table API.
    #   3. Solve TSP with OR-Tools (fallback: nearest-neighbour).
    #   4. Persist ordered_case_ids in the Beat.
    print(f"[11/11] Seeding today's beats (OSRM + OR-Tools route optimization)...")
    try:
        from app.core.routing import optimize_route as _opt_route
        _routing_available = True
    except Exception:
        _routing_available = False
        print("  [warn] routing module unavailable — using unordered IDs")
    today_str = today.strftime("%Y-%m-%d")
    # ── Top up today's allocation so EVERY agent has a day's work ─────────────
    # The month-0 loop scatters allocation dates across the month, so only ~1
    # case per agent happened to land on today — agent002 looked fully loaded
    # (its 15 demo cases are pinned to today) while everyone else had an empty
    # beat and an empty "today's cases" drill-down in the manager views.
    # Open cases are rolled forward onto today, which is what daily allocation
    # does in production: an unresolved case rolls onto a later beat, carrying
    # its visit history with it.
    _ROLLABLE = (CaseStatus.ASSIGNED, CaseStatus.IN_PROGRESS, CaseStatus.PTP_SET,
                 CaseStatus.PARTIALLY_PAID, CaseStatus.ESCALATED)
    _rolled = 0
    for agent in agents[:N_AGENTS]:
        if agent.employee_code == "EMP0002":
            continue    # demo beat is scripted case-by-case — never touch it
        _mine = [c for c in cases if c.agent_id == agent.id]
        _have = [c for c in _mine if c.allocation_date == today_str]
        _want = min(agent.max_cases_per_day, random.randint(9, 14))
        # Oldest open cases first — ageing debt is what a real allocator pushes
        # to the top of today's route. Ordinary tickets are preferred over the
        # ₹3L+ tail: taking those purely by age handed one agent a ₹60L day,
        # which no allocator would build.
        _open = [c for c in _mine if c.allocation_date != today_str and c.status in _ROLLABLE]
        _by_age = lambda c: c.allocation_date or ""
        _pool = (sorted((c for c in _open if (c.target_amount or 0) <= 300_000), key=_by_age)
                 + sorted((c for c in _open if (c.target_amount or 0) > 300_000), key=_by_age))
        for c in _pool[:max(0, _want - len(_have))]:
            c.allocation_date = today_str
            _rolled += 1
    db.flush()
    print(f"  Rolled {_rolled} open cases onto today's allocation across {N_AGENTS - 1} agents.")
    for agent in agents[:N_AGENTS]:
        # Beat = only today's allocated cases so beat, home, and my-cases all show the same count
        agent_cases = [
            c for c in cases
            if c.agent_id == agent.id and c.allocation_date == today_str
        ][:agent.max_cases_per_day]
        if not agent_cases:
            continue
        is_demo = agent.employee_code == "EMP0002"
        if _routing_available and agent_cases:
            # Each agent's home lat/lon was stored during agent creation
            start_lat = agent.last_known_latitude or 28.4595
            start_lon = agent.last_known_longitude or 77.0266
            try:
                case_coords = [
                    (cust_lookup[c.customer_id].latitude, cust_lookup[c.customer_id].longitude)
                    for c in agent_cases if c.customer_id in cust_lookup
                ]
                # Filter to cases whose customer was resolved
                agent_cases = [c for c in agent_cases if c.customer_id in cust_lookup]
                order = _opt_route(case_coords, start_lat, start_lon)
                agent_cases = [agent_cases[i] for i in order if i < len(agent_cases)]
                print(f"  Optimised beat for {agent.employee_code}: {len(agent_cases)} stops")
            except Exception as e:
                print(f"  [warn] Optimisation failed for {agent.employee_code}: {e}")
        ordered_ids = [c.id for c in agent_cases]
        total_target = sum(c.target_amount for c in agent_cases)
        # Estimate route distance from ordered coordinates (Haversine cumulative)
        def _route_km(cases_list):
            if len(cases_list) < 2:
                return round(random.uniform(5, 15), 2)
            total = 0.0
            for i in range(len(cases_list) - 1):
                cu1 = cust_lookup.get(cases_list[i].customer_id)
                cu2 = cust_lookup.get(cases_list[i+1].customer_id)
                if not cu1 or not cu2:
                    continue
                dlat = math.radians(cu2.latitude - cu1.latitude)
                dlon = math.radians(cu2.longitude - cu1.longitude)
                a = math.sin(dlat/2)**2 + math.cos(math.radians(cu1.latitude))*math.cos(math.radians(cu2.latitude))*math.sin(dlon/2)**2
                total += 6371 * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
            return round(total * 1.35, 2)  # road factor ~1.35×
        est_km = _route_km(agent_cases)
        est_min = int(est_km / 25 * 60) + len(agent_cases) * 20  # drive + 20 min/visit
        db.add(Beat(
            id=_uid(), agent_id=agent.id,
            beat_date=today,
            beat_number=f"BEAT-{today.strftime('%Y%m%d')}-{agent.id[:6].upper()}",
            ordered_case_ids=ordered_ids,
            total_cases=len(ordered_ids),
            estimated_distance_km=est_km,
            estimated_duration_minutes=est_min,
            total_target_amount=round(total_target, 2),
            status=BeatStatus.IN_PROGRESS if is_demo else random.choice([BeatStatus.PLANNED, BeatStatus.IN_PROGRESS]),
            cases_completed=3 if is_demo else random.randint(0, min(5, len(ordered_ids))),
            amount_collected=144500.0 if is_demo else round(random.uniform(0, total_target * 0.4), 2),
            is_ml_generated=True, ml_model_version="vrp_ortools_v1",
        ))
    db.commit()
    # ── Realistic PTP book for the demo agent (agent002) ──────────────────────
    # The 5 due-today PTPs are pinned to fixed demo cases so the same 5 cards
    # show up under "PTPs Due Today" on every re-seed: Priya (3), Anita (5),
    # Suresh (6), Vikas (9), Arun (11). Deliberately excluded — demo_cases 0/1/4
    # (already visited today) and demo_cases[2], the DEMO_CONTACT_NAME showcase
    # case that the live record-visit demo runs on.
    _demo = curate_demo_agent_ptps(
        db, agents[1], today,
        due_today_case_ids=[demo_cases[i].id for i in (3, 5, 6, 9, 11)],
    )
    db.commit()
    print(f"  agent002 PTPs: {_demo['honored']} honored, {_demo['broken']} broken, {_demo['due_today']} due today")
    # ── Real rows behind every recent working day ─────────────────────────────
    print("[11a] Seeding daily field activity for the last 30 days...")
    _daily = seed_recent_daily_activity(db, agents[:N_AGENTS], today, days_back=30)
    db.commit()
    print(f"       {_daily.get('days', 0)} agent-days: {_daily.get('visits', 0)} visits, "
          f"{_daily.get('payments', 0)} payments, {_daily.get('ptps', 0)} PTPs")
    # ── Integrity pass ────────────────────────────────────────────────────────
    # Runs last, so it also catches anything the demo blocks and the PTP
    # curation left inconsistent with each other.
    print("[11b] Cross-checking visits / payments / PTPs / statuses / beats...")
    _fx = reconcile_integrity(db, today)
    db.commit()
    if _fx:
        for k, v in sorted(_fx.items()):
            print(f"       {k:34} {v}")
    else:
        print("       nothing to repair.")
    # ── Repayment scoring ─────────────────────────────────────────────────────
    # LAST, and that ordering is the whole point. The scorecard's behavioural
    # factors read visits, PTPs and payments; scoring before [5/11]-[11b] have
    # run would leave every one of them abstaining and produce a flat book —
    # which is precisely the failure the old formula had, from the other end.
    #
    # This is the ONLY place a seeded database gets a repayment score. Writing
    # Customer.risk_score is gated on REPAYMENT_WRITE_RISK_SCORE (default
    # False), so by default a fresh seed leaves every customer at the 50.0
    # column default and records snapshots only.
    print("[11c] Scoring repayment likelihood (scorecard)...")
    _score = RepaymentService(db).rescore(as_of=today, trigger=TRIGGER_SEED)
    db.commit()
    _dist = _score["distribution"]["after"]
    print(f"       {_score['loans_scored']} loans, {_score['customers_affected']} customers, "
          f"{_score['snapshots_written']} snapshots")
    print(f"       Customer.risk_score written: {_score['customers_written']} "
          f"(write gate {'OPEN' if _score['write_risk_score_enabled'] else 'CLOSED'})")
    if _dist.get("n"):
        print(f"       would-be risk_score  median={_dist['median']}  mean={_dist['mean']}  "
              f"bands={_dist['bands']}")
    # ── Reconcile current-month agent counters ────────────────────────────────
    # Strictly the calendar month to date — the tile is labelled "This Month",
    # so it has to mean the month. Seeded early in a month that means a small
    # number (the 5th = 4 working days), and that is the honest reading: the
    # figure must stay in proportion to the days actually worked.
    print("[12/12] Reconciling current-month agent counters...")
    window_start = date(today.year, today.month, 1)
    for ag in agents:
        # Visits and collections come from the agent's Beat rows — the daily
        # record of work that also draws the duty calendar. The Visit and
        # Payment tables only ever hold the scripted demo activity, so counting
        # those made a whole month read like a single day: 13 visits and ₹1.5L
        # against a ₹1.37L one-day target.
        mo_beats = (
            db.query(Beat)
            .filter(Beat.agent_id == ag.id,
                    Beat.beat_date >= window_start,
                    Beat.beat_date <= today)
            .all()
        )
        mo_vis = sum(int(b.cases_completed or 0) for b in mo_beats)
        mo_col = round(sum(float(b.amount_collected or 0.0) for b in mo_beats), 2)
        mo_ptps = db.query(func.count(PTP.id)).filter(
            PTP.agent_id == ag.id,
            PTP.committed_date >= window_start,
            PTP.committed_date <= today,
        ).scalar() or 0
        # honored count drives the PTP-conversion rate on the profile; without
        # this it stayed 0 (previously hardcoded), showing an unrealistic 0%.
        mo_hon = db.query(func.count(PTP.id)).filter(
            PTP.agent_id == ag.id,
            PTP.committed_date >= window_start,
            PTP.committed_date <= today,
            PTP.status.in_([PTPStatus.HONORED, PTPStatus.PARTIALLY_HONORED]),
        ).scalar() or 0
        ag.current_month_collections = round(float(mo_col), 2)
        ag.current_month_visits = mo_vis
        ag.current_month_ptps_set = mo_ptps
        ag.current_month_ptps_honored = mo_hon
    db.commit()
    db.close()
    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("Seed complete!")
    print("=" * 60)
    print(f"  Agency       : {AGENCY_NAME}")
    print(f"  Address      : {AGENCY_ADDRESS}")
    print(f"  Managers     : {N_MANAGERS}  (manager1@tiqcollect.in / Manager@123)")
    print(f"                   manager1 -> agents 001-015 (15 agents)")
    print(f"                   manager2 -> agents 016-018  (3 agents)")
    print(f"  Admin        : admin@tiqcollect.in / Admin@123")
    print(f"  Agents       : {N_AGENTS}  (agent001–018@tiqcollect.in / Agent@123)")
    print(f"  Demo agent   : agent002@tiqcollect.in  (Gurugram GPS)")
    print(f"")
    print(f"  Customers    : {N_CUSTOMERS} (all 30–60 / 60–90 / 90+ DPD — NPA pool)")
    print(f"  Loans        : {N_LOANS}  (all ABC Bank)")
    print(f"  Cases        : {case_num - 1}  ({len(cases)} historical + 15 fixed demo + 10 bank-today)")
    print(f"  Visits       : {len(all_visits)} historical + today demo activity")
    print(f"  Performance  : {N_AGENTS * HISTORY_MONTHS} monthly snapshots")
    print(f"")
    print(f"  Bank batch   : 10 first-time customers today (all → other agents, agent002 is full)")
    print(f"  Daily test   : python -m scripts.generate_bank_data  (2 rows)")
    print(f"               : python -m scripts.ingest_daily --file data/incoming/bank_portfolio_*.csv")
    print("=" * 60)
if __name__ == "__main__":
    seed()