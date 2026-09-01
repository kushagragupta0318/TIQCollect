import os
import sys
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, HRFlowable
)
from reportlab.pdfgen import canvas

class NumberedCanvas(canvas.Canvas):
    """Canvas for adding running headers and 'Page X of Y' footers."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, page_count):
        self.saveState()
        self.setFont("Helvetica-Bold", 8)
        self.setFillColor(colors.HexColor("#64748B"))
        
        # Running header (pages 2+)
        if self._pageNumber > 1:
            self.drawString(54, 750, "TIQCollect -- Machine Learning Allocation Engine")
            self.setFont("Helvetica", 8)
            self.drawRightString(558, 750, "Architecture & Implementation Whitepaper")
            self.setStrokeColor(colors.HexColor("#E2E8F0"))
            self.setLineWidth(0.75)
            self.line(54, 744, 558, 744)

        # Running footer (all pages)
        self.setStrokeColor(colors.HexColor("#E2E8F0"))
        self.setLineWidth(0.75)
        self.line(54, 48, 558, 48)

        self.setFont("Helvetica", 8)
        self.setFillColor(colors.HexColor("#94A3B8"))
        self.drawString(54, 34, "Confidential -- TIQCollect AI Systems")
        self.drawRightString(558, 34, f"Page {self._pageNumber} of {page_count}")
        self.restoreState()

def create_pdf(output_filename="TIQCollect_ML_Allocation_Architecture.pdf"):
    doc = SimpleDocTemplate(
        output_filename,
        pagesize=letter,
        leftMargin=54,
        rightMargin=54,
        topMargin=54,
        bottomMargin=54
    )

    styles = getSampleStyleSheet()
    
    # Custom color palette
    primary_color = colors.HexColor("#0F172A")    # Slate 900
    brand_blue = colors.HexColor("#2563EB")       # Blue 600
    brand_purple = colors.HexColor("#4F46E5")     # Indigo 600
    text_dark = colors.HexColor("#1E293B")        # Slate 800
    text_muted = colors.HexColor("#475569")       # Slate 600
    bg_light = colors.HexColor("#F8FAFC")         # Slate 50
    border_color = colors.HexColor("#CBD5E1")     # Slate 300

    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=20,
        leading=24,
        textColor=primary_color,
        spaceAfter=4
    )

    subtitle_style = ParagraphStyle(
        'DocSubTitle',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=11,
        leading=15,
        textColor=brand_blue,
        spaceAfter=12
    )

    h1_style = ParagraphStyle(
        'H1',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=13,
        leading=17,
        textColor=primary_color,
        spaceBefore=10,
        spaceAfter=6,
        keepWithNext=True
    )

    h2_style = ParagraphStyle(
        'H2',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=10,
        leading=14,
        textColor=brand_purple,
        spaceBefore=8,
        spaceAfter=4,
        keepWithNext=True
    )

    body_style = ParagraphStyle(
        'Body',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8.5,
        leading=12,
        textColor=text_dark,
        spaceAfter=6
    )

    bullet_style = ParagraphStyle(
        'Bullet',
        parent=body_style,
        leftIndent=12,
        firstLineIndent=-8,
        spaceAfter=3
    )

    box_title = ParagraphStyle(
        'BoxTitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=9,
        leading=12,
        textColor=primary_color
    )

    box_body = ParagraphStyle(
        'BoxBody',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8,
        leading=11.5,
        textColor=text_muted
    )

    table_header = ParagraphStyle(
        'TableHeader',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=8,
        leading=10,
        textColor=colors.white
    )

    table_cell = ParagraphStyle(
        'TableCell',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=7.5,
        leading=10,
        textColor=text_dark
    )

    table_cell_bold = ParagraphStyle(
        'TableCellBold',
        parent=table_cell,
        fontName='Helvetica-Bold'
    )

    story = []

    def make_callout(title, text, bg="#F1F5F9", border="#94A3B8"):
        content = [
            [Paragraph(f"<b>{title}</b>", box_title)],
            [Paragraph(text, box_body)]
        ]
        t = Table(content, colWidths=[504])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), colors.HexColor(bg)),
            ('BOX', (0,0), (-1,-1), 1, colors.HexColor(border)),
            ('TOPPADDING', (0,0), (-1,-1), 6),
            ('BOTTOMPADDING', (0,0), (-1,-1), 6),
            ('LEFTPADDING', (0,0), (-1,-1), 10),
            ('RIGHTPADDING', (0,0), (-1,-1), 10),
        ]))
        return t

    # ==========================================
    # PAGE 1: EXECUTIVE SUMMARY & CORE PROBLEM
    # ==========================================
    story.append(Paragraph("TIQCollect -- Machine Learning Architecture", title_style))
    story.append(Paragraph("Empirical Bayes Shrinkage & Global Bipartite Linear Programming for Case Allocation", subtitle_style))
    story.append(HRFlowable(width="100%", thickness=1.5, color=brand_blue, spaceBefore=0, spaceAfter=10))

    story.append(Paragraph("1. Executive Summary & The Problem", h1_style))
    story.append(Paragraph(
        "Debt collection operations face a fundamental trade-off: allocating delinquent loans to field agents in a way that maximizes "
        "total recovered cash while minimizing travel expenses, respecting agent capacity limits, and avoiding customer friction. "
        "Traditional collection systems rely on <b>greedy heuristic allocation</b> (e.g., matching the closest agent to a case one-by-one). "
        "This naive approach leads to severe operational bottlenecks, unfair agent workloads, and millions in lost recoveries.",
        body_style
    ))

    # Problems Table
    prob_data = [
        [Paragraph("Flaw in Legacy Systems", table_header), Paragraph("Real-World Consequence", table_header), Paragraph("TIQCollect Solution", table_header)],
        [
            Paragraph("<b>Greedy 1-by-1 Matching</b>", table_cell),
            Paragraph("First-come assignments grab local easy cases, leaving distant high-value cases stranded.", table_cell),
            Paragraph("<b>Global Bipartite Matcher:</b> Optimizes all cases and agents simultaneously.", table_cell_bold)
        ],
        [
            Paragraph("<b>Direct Case+Agent ML Bias</b>", table_cell),
            Paragraph("Models learn past assignment bias; lucky agents get easy cases, creating a feedback loop.", table_cell),
            Paragraph("<b>Empirical Bayes Shrinkage:</b> Multi-level borrowing of segment priors.", table_cell_bold)
        ],
        [
            Paragraph("<b>Ignoring Rupee Values</b>", table_cell),
            Paragraph("Agents travel 25 km to collect Rs. 2,000 while an adjacent Rs. 1,50,000 account is neglected.", table_cell),
            Paragraph("<b>Expected INR Formulation:</b> Weights allocation by true expected recovery amount.", table_cell_bold)
        ],
        [
            Paragraph("<b>Single Rigid Objective</b>", table_cell),
            Paragraph("Managers cannot adjust operational strategy for fuel savings vs. month-end cash targets.", table_cell),
            Paragraph("<b>Multi-Objective Engine:</b> Max Recovery, Balanced, or Min Distance at a click.", table_cell_bold)
        ]
    ]
    t_prob = Table(prob_data, colWidths=[130, 184, 190])
    t_prob.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), primary_color),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('GRID', (0,0), (-1,-1), 0.5, border_color),
        ('TOPPADDING', (0,0), (-1,-1), 5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
        ('LEFTPADDING', (0,0), (-1,-1), 6),
        ('RIGHTPADDING', (0,0), (-1,-1), 6),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, bg_light]),
    ]))
    story.append(t_prob)
    story.append(Spacer(1, 8))

    story.append(Paragraph("2. System Architecture Overview", h1_style))
    story.append(Paragraph(
        "TIQCollect separates case allocation into a <b>Two-Stage Decoupled AI Pipeline</b> that blends statistical machine learning with "
        "deterministic linear programming constraints:",
        body_style
    ))

    arch_box = (
        "<b>Stage 1: Statistical Learning (Empirical Bayes & Shadow Evaluator)</b><br/>"
        "- Historical repayments are partitioned into <i>(Loan Type x DPD Bucket)</i> risk segments.<br/>"
        "- Agent historical performance is estimated with Beta-Binomial Bayesian shrinkage to eliminate small-sample noise.<br/>"
        "- Calculates the <b>True Recovery Probability P(Recovery | Agent, Case)</b> for all possible pairings.<br/><br/>"
        "<b>Stage 2: Global Bipartite Linear Programming (Scipy linear_sum_assignment)</b><br/>"
        "- Constructs an <i>N x M</i> global utility matrix combining Expected Rupee Value, Proximity, Skill, and Workload.<br/>"
        "- Solves the optimal global assignment globally in &lt; 25 ms under manager-selected objectives.<br/>"
        "- Validates travel feasibility with OR-Tools route radius gates; defers route-infeasible cases cleanly."
    )
    story.append(make_callout("Two-Stage Mathematical Framework", arch_box, bg="#EEF2FF", border="#6366F1"))

    story.append(PageBreak())

    # ==========================================
    # PAGE 2: THE ML MODEL: EMPIRICAL BAYES
    # ==========================================
    story.append(Paragraph("3. The Core ML Model: Empirical Bayes Shrinkage", h1_style))
    story.append(Paragraph(
        "A critical question in collection AI is: <i>Why not train a standard XGBoost or Deep Neural Network predicting recovery on (Agent ID + Case ID)?</i>",
        body_style
    ))

    why_not_text = (
        "<b>Why Direct Case+Agent ML Models Fail in Production:</b><br/>"
        "1. <b>Past Assignment Confounding (Selection Bias):</b> In historical data, an agent only worked the cases assigned to them. "
        "If Agent A was historically assigned easy 15-day DPD cases and Agent B was assigned hostile 90-day DPD cases, a standard ML classifier "
        "learns that 'Agent A has a 90% win rate and Agent B has a 10% win rate'. It falsely concludes Agent A should get every case.<br/>"
        "2. <b>The Small Sample Distortion Trap:</b> If Agent C only visited 2 cases in their career and won both, their raw win rate is 100%. "
        "A naive ML model rates Agent C higher than a veteran agent who has a proven 75% win rate over 500 cases.<br/>"
        "3. <b>Target Leakage & Promise Confusion:</b> Combining promises (PTP) and cash payments as a single target leads models to reward agents "
        "who record fake promises that never clear."
    )
    story.append(make_callout("Why Standard Black-Box ML Fails Here", why_not_text, bg="#FEF2F2", border="#EF4444"))
    story.append(Spacer(1, 8))

    story.append(Paragraph("How the Empirical Bayes Beta-Binomial Model Works", h2_style))
    story.append(Paragraph(
        "To solve these fundamental statistical hazards, TIQCollect implements <b>Beta-Binomial Empirical Bayes Shrinkage</b> "
        "(<code>backend/app/ml/empirical_bayes.py</code>). This technique borrows statistical strength across the entire agency portfolio:",
        body_style
    ))

    story.append(Paragraph("<b>Step 1: Portfolio Segment Priors</b>", bullet_style))
    story.append(Paragraph(
        "Every historic case is assigned to a segment <i>s = (Loan Type, DPD Bucket)</i> (e.g., <i>Personal Loan x 61-90 DPD</i>). "
        "The system computes the global agency prior mean win rate <b>mu_s</b> across all agents.",
        body_style
    ))

    story.append(Paragraph("<b>Step 2: Bayesian Shrinkage Formula</b>", bullet_style))
    story.append(Paragraph(
        "For an individual agent <i>j</i> with <i>n_j,s</i> historical attempts in segment <i>s</i> and observed win rate <i>w_observed</i>, "
        "their estimated true win rate <i>w_estimated</i> is smoothed towards the segment prior:",
        body_style
    ))

    formula_text = (
        "<font size=10 color='#1E293B'><b>w_estimated(j, s) = [ n / (n + M) ] * (w_observed)  +  [ M / (n + M) ] * (mu_segment)</b></font><br/>"
        "Where <b>M = 10</b> is the prior shrinkage strength parameter.<br/>"
        "- <b>Small Sample (n = 2):</b> w_estimated = 0.17 * (Observed) + 0.83 * (Segment Prior)  -- <i>Shrinks heavily to segment baseline.</i><br/>"
        "- <b>Large Sample (n = 40):</b> w_estimated = 0.80 * (Observed) + 0.20 * (Segment Prior)  -- <i>Relies on proven agent ability.</i>"
    )
    story.append(make_callout("Empirical Bayes Formulation", formula_text, bg="#F0FDF4", border="#10B981"))
    story.append(Spacer(1, 6))

    story.append(Paragraph("<b>Step 3: Multiplier Safety Clamping</b>", bullet_style))
    story.append(Paragraph(
        "The agent segment multiplier alpha(j, s) = w_estimated / mu_segment is strictly bounded to the range <b>[0.75, 1.25]</b>. "
        "This guarantees that no agent is penalized or boosted by more than +/- 25% based on past history, preventing runaway star-performer bias.",
        body_style
    ))

    story.append(PageBreak())

    # ==========================================
    # PAGE 3: GLOBAL LP OPTIMIZATION & OBJECTIVES
    # ==========================================
    story.append(Paragraph("4. Global Linear Programming Optimization", h1_style))
    story.append(Paragraph(
        "Once individual case-agent probabilities are estimated, TIQCollect models daily portfolio allocation as a "
        "<b>Bipartite Maximum-Weight Matching Problem</b> solved with the Hungarian / Jonker-Volgenant algorithm "
        "(<code>scipy.optimize.linear_sum_assignment</code>).",
        body_style
    ))

    story.append(Paragraph("Expected Rupee Utility Function", h2_style))
    story.append(Paragraph(
        "For every candidate case <i>i</i> and agent capacity slot <i>j</i>, the engine calculates a multi-dimensional utility score:",
        body_style
    ))

    util_box = (
        "<b>Utility(i, j) = w_INR * Score_INR(i, j) + w_dist * Score_prox(i, j) + w_skill * Score_skill(j) + Continuity + Language</b><br/><br/>"
        "- <b>Expected Rupee Score (Score_INR):</b> Target Balance x P(Recovery | Agent, Segment). Scales with true recovery value.<br/>"
        "- <b>Proximity Score (Score_prox):</b> exp( - Distance_km / 5.5 ). Exponential decay penalizing long commutes.<br/>"
        "- <b>Skills & Tier (Score_skill):</b> Agent Tier weight (Tier 1 = 1.0, Tier 2 = 0.8) + Product Specialization match.<br/>"
        "- <b>Capacity Decay:</b> Slightly prioritizes early morning slots over late fatigue slots."
    )
    story.append(make_callout("Global Utility Matrix Formulation", util_box, bg="#F8FAFC", border="#CBD5E1"))
    story.append(Spacer(1, 8))

    story.append(Paragraph("Manager-Selectable Allocation Objectives", h2_style))
    story.append(Paragraph(
        "Managers can instantly switch the agency allocation strategy in the Command Center. The LP solver recalculates the entire agency "
        "portfolio in under 30 milliseconds:",
        body_style
    ))

    obj_data = [
        [Paragraph("Objective", table_header), Paragraph("Weight Formulation", table_header), Paragraph("Operational Impact on Active Portfolio", table_header)],
        [
            Paragraph("<b>Max Recovery</b>", table_cell_bold),
            Paragraph("70% INR Recovery<br/>10% Proximity<br/>15% Skills & Tier", table_cell),
            Paragraph("<b>Rs. 138.8 Lakhs Expected Recovery</b><br/>Maximizes collections on high-value delinquent accounts. Top choice for month-end closes.", table_cell)
        ],
        [
            Paragraph("<b>Balanced</b>", table_cell_bold),
            Paragraph("45% INR Recovery<br/>40% Proximity<br/>10% Skills & Tier", table_cell),
            Paragraph("<b>Rs. 136.0 Lakhs Expected Recovery</b><br/>Achieves 98% of peak recovery while cutting travel distance by <b>27%</b> (667 km total).", table_cell)
        ],
        [
            Paragraph("<b>Min Distance</b>", table_cell_bold),
            Paragraph("80% Proximity<br/>5% INR Recovery<br/>10% Workload Balance", table_cell),
            Paragraph("<b>467.2 km Total Travel (49% Fuel Savings)</b><br/>Clusters cases tightly around agent homes. Ideal during bad weather or high fuel costs.", table_cell)
        ]
    ]
    t_obj = Table(obj_data, colWidths=[110, 144, 250])
    t_obj.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), primary_color),
        ('GRID', (0,0), (-1,-1), 0.5, border_color),
        ('TOPPADDING', (0,0), (-1,-1), 5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
        ('LEFTPADDING', (0,0), (-1,-1), 6),
        ('RIGHTPADDING', (0,0), (-1,-1), 6),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, bg_light]),
    ]))
    story.append(t_obj)
    story.append(Spacer(1, 8))

    story.append(Paragraph("Stage 2: Travel Feasibility & Boundary Gating", h2_style))
    story.append(Paragraph(
        "After the global solver outputs candidate matches, Stage 2 enforces physical constraints: any case requiring an excessive marginal detour "
        "(> 22 km outside the agent's territorial cluster) is rejected and tagged as <b>DEFERRED_ROUTE_INFEASIBLE</b>. This prevents agents "
        "from being assigned unserviceable isolated cases.",
        body_style
    ))

    story.append(PageBreak())

    # ==========================================
    # PAGE 4: SHADOW EVALUATOR, SMART ORDER & SUMMARY
    # ==========================================
    story.append(Paragraph("5. Shadow ML Evaluator & Doorstep Smart Ranking", h1_style))
    
    story.append(Paragraph("The Shadow ML Classifier (shadow_evaluator.py)", h2_style))
    story.append(Paragraph(
        "In addition to Empirical Bayes allocation, a background <b>Supervised Shadow Model</b> evaluates point-in-time loan outcome vectors "
        "(DPD progression, overdue ratio, visit history, prior payment frequency). "
        "During every planning run, this classifier computes recovery lift deciles and saves diagnostic telemetry to "
        "<code>AllocationRun.summary_metadata['shadow_evaluation']</code> for auditability.",
        body_style
    ))

    story.append(Paragraph("Real-Time Doorstep Case Ranking (Smart Order)", h2_style))
    story.append(Paragraph(
        "When an agent is in the field, clicking <b>'Smart Order'</b> in the mobile app switches from standard geographic route sequencing to "
        "real-time urgency prioritization (<code>CaseService.ranked_cases</code>):",
        body_style
    ))

    smart_data = [
        [Paragraph("Live Field Signal", table_header), Paragraph("Score Bonus", table_header), Paragraph("Badge", table_header), Paragraph("Doorstep Impact", table_header)],
        [
            Paragraph("Promise to Pay (PTP) Due Today", table_cell),
            Paragraph("+60 pts", table_cell_bold),
            Paragraph("<font color='#DC2626'><b>PTP TODAY</b></font>", table_cell),
            Paragraph("Jumps to #1 rank. Agent visits before customer changes mind.", table_cell)
        ],
        [
            Paragraph("Verbal Payment Date Today", table_cell),
            Paragraph("+50 pts", table_cell_bold),
            Paragraph("<font color='#16A34A'><b>PAYMENT TODAY</b></font>", table_cell),
            Paragraph("Confirmed on pre-visit call; customer has funds ready.", table_cell)
        ],
        [
            Paragraph("Time-of-Day Window Match", table_cell),
            Paragraph("+35 pts", table_cell_bold),
            Paragraph("<font color='#2563EB'><b>BEST TIME NOW</b></font>", table_cell),
            Paragraph("Matches customer request (e.g. 'visit after 2 PM').", table_cell)
        ],
        [
            Paragraph("Customer Visited Today", table_cell),
            Paragraph("Auto-Deprioritize", table_cell_bold),
            Paragraph("<font color='#64748B'><b>COMPLETED</b></font>", table_cell),
            Paragraph("Automatically moved to bottom of list with checkmark.", table_cell)
        ]
    ]
    t_smart = Table(smart_data, colWidths=[150, 74, 95, 185])
    t_smart.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), primary_color),
        ('GRID', (0,0), (-1,-1), 0.5, border_color),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ('LEFTPADDING', (0,0), (-1,-1), 5),
        ('RIGHTPADDING', (0,0), (-1,-1), 5),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, bg_light]),
    ]))
    story.append(t_smart)
    story.append(Spacer(1, 8))

    story.append(Paragraph("6. Architectural Summary & Production Guarantees", h1_style))
    summary_text = (
        "<b>Key Technical Advantages of TIQCollect's ML Stack:</b><br/>"
        "1. <b>Zero Selection Bias:</b> Empirical Bayes shrinkage prevents runaway feedback loops and handles low-visit agents safely.<br/>"
        "2. <b>Global Portfolio Optimization:</b> Bipartite linear programming guarantees the agency-wide optimal allocation in &lt; 25 ms.<br/>"
        "3. <b>Managerial Agility:</b> Strategic objective switching (INR Recovery vs. Fuel Distance) without code changes or downtime.<br/>"
        "4. <b>Auditability & Safety:</b> Hard compliance gates (DNC, Hostility, Female-agent constraints) are strictly non-negotiable.<br/>"
        "5. <b>Offline Resiliency:</b> All beats, sequences, and case intelligence cache locally for offline doorstep execution."
    )
    story.append(make_callout("Production Quality & Reliability", summary_text, bg="#F0FDF4", border="#10B981"))
    story.append(Spacer(1, 10))

    story.append(Paragraph("<i>Document generated automatically by TIQCollect Engineering AI Systems -- Confidential</i>", box_body))

    # Build PDF
    doc.build(story, canvasmaker=NumberedCanvas)
    print(f"PDF generated successfully: {output_filename}")

if __name__ == "__main__":
    out = "TIQCollect_ML_Allocation_Architecture.pdf"
    if len(sys.argv) > 1:
        out = sys.argv[1]
    create_pdf(out)
