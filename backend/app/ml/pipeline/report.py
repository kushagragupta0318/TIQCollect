# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-08 — NEW. Generates the Model Development Document from the artifact
#   directory, so the document cannot describe a model other than the one that
#   was saved.
#
#   IT IS BUILT FROM THE ARTIFACT, NOT FROM THE TRAINING RUN. That is the whole
#   design. A report written by the trainer, from variables in memory, is a
#   report about what the trainer believed; a report built by re-reading
#   metadata.json and the CSVs beside the pickle is a report about what shipped.
#   This repo has already been bitten by the general version of that problem —
#   get_objective_weights() returned one weight table while the allocator ran on
#   a second one hardcoded inline, and nothing surfaced the conflict because
#   nothing read both.
# ───────────────────────────────────────────────────────────────────────────
"""
Model Development Document generator.

    write_model_document(Path("app/ml/artifacts/recovery_risk/1.0.0"))

Produces MODEL_DEVELOPMENT.html in that directory: a single self-contained page
holding the specification, the data, every selection step with what it removed,
the readable scorecard, the full evaluation and the limitations. Images are
referenced relatively, so the directory travels as a unit.
"""
from __future__ import annotations

import html
import json
from pathlib import Path

import pandas as pd

CSS = """
:root{--ink:#1a1a1a;--mut:#666;--line:#e2e2e2;--bg:#fff;--accent:#2563eb;
--ok:#15803d;--warn:#b45309;--fail:#b91c1c;--code:#f6f7f9}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.62 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:1060px;margin:0 auto;padding:48px 28px 96px}
h1{font-size:30px;margin:0 0 6px;letter-spacing:-.02em}
h2{font-size:21px;margin:46px 0 12px;padding-bottom:7px;border-bottom:2px solid var(--ink)}
h3{font-size:16px;margin:26px 0 8px;color:#333}
p{margin:10px 0}
.sub{color:var(--mut);margin:0 0 26px;font-size:14px}
.warn{background:#fff7ed;border:1px solid #fdba74;border-left:4px solid #ea580c;
padding:13px 16px;border-radius:5px;margin:20px 0;font-size:14px}
.note{background:#f8fafc;border:1px solid var(--line);border-left:4px solid var(--accent);
padding:13px 16px;border-radius:5px;margin:16px 0;font-size:14px}
table{border-collapse:collapse;width:100%;margin:14px 0;font-size:13px}
th,td{border:1px solid var(--line);padding:6px 9px;text-align:right}
th{background:#f6f7f9;font-weight:600;text-align:right}
th:first-child,td:first-child{text-align:left}
tbody tr:nth-child(even){background:#fafbfc}
.scroll{overflow-x:auto;margin:14px 0}
img{max-width:100%;border:1px solid var(--line);border-radius:6px;margin:10px 0;display:block}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}
@media(max-width:760px){.grid{grid-template-columns:1fr}}
.kpi{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin:20px 0}
.kpi div{border:1px solid var(--line);border-radius:7px;padding:12px 14px}
.kpi .v{font-size:23px;font-weight:650;letter-spacing:-.02em}
.kpi .l{font-size:11px;color:var(--mut);text-transform:uppercase;letter-spacing:.06em}
.PASS{color:var(--ok);font-weight:650}.WARN{color:var(--warn);font-weight:650}
.FAIL{color:var(--fail);font-weight:650}
code{background:var(--code);padding:1px 5px;border-radius:3px;font-size:13px}
.badge{display:inline-block;padding:3px 10px;border-radius:20px;font-size:12px;
font-weight:650;letter-spacing:.03em}
.badge.PASS{background:#dcfce7;color:#15803d}.badge.FAIL{background:#fee2e2;color:#b91c1c}
ul{margin:10px 0 10px 20px;padding:0}li{margin:5px 0}
"""


def _tbl(df: pd.DataFrame | None, max_rows: int = 60, cls: str = "") -> str:
    if df is None or len(df) == 0:
        return "<p><em>none</em></p>"
    d = df.head(max_rows)
    head = "".join(f"<th>{html.escape(str(c))}</th>" for c in d.columns)
    body = ""
    for _, r in d.iterrows():
        cells = ""
        for c in d.columns:
            v = r[c]
            txt = ("" if pd.isna(v) else
                   f"{v:,.4f}" if isinstance(v, float) else
                   f"{v:,}" if isinstance(v, (int,)) and not isinstance(v, bool) else
                   html.escape(str(v)))
            klass = f' class="{txt}"' if txt in ("PASS", "WARN", "FAIL") else ""
            cells += f"<td{klass}>{txt}</td>"
        body += f"<tr>{cells}</tr>"
    more = (f"<p class='sub'>showing {max_rows} of {len(df)} rows; the full table "
            f"is in the CSV beside this document.</p>" if len(df) > max_rows else "")
    return (f'<div class="scroll {cls}"><table><thead><tr>{head}</tr></thead>'
            f"<tbody>{body}</tbody></table></div>{more}")


def _n(v) -> str:
    """A count for the document, or a dash. The ledger dataset records its
    row counts under `realism.descriptives` rather than at the top level, and
    `'-':,` is a ValueError — which is how the first 2.0.0 run crashed after
    training, gating and comparing successfully."""
    try:
        return f"{int(v):,}"
    except (TypeError, ValueError):
        return "-"


def _desc(ds: dict) -> dict:
    return (ds.get("realism") or {}).get("descriptives") or {}


def _csv(d: Path, name: str) -> pd.DataFrame | None:
    p = d / name
    return pd.read_csv(p) if p.exists() else None


def _img(d: Path, rel: str, caption: str = "") -> str:
    if not (d / rel).exists():
        return ""
    cap = f'<p class="sub">{html.escape(caption)}</p>' if caption else ""
    return f'<img src="{rel}" alt="{html.escape(caption or rel)}">{cap}'


def write_model_document(artifact_dir: Path) -> Path:
    d = Path(artifact_dir)
    meta = json.loads((d / "metadata.json").read_text())
    spec = meta["spec"]
    m = meta["metrics"]
    oot, tr, va = m["oot"], m["train"], m["valid"]
    chal = m.get("oot_challenger", {})
    gates = pd.DataFrame(meta.get("gates", []))
    ds = meta.get("dataset", {})

    passed = meta.get("gate_summary") == "PASS"
    ro = oot.get("rank_order", {})

    warn = ""
    if meta.get("SYNTHETIC_WARNING"):
        warn = (f'<div class="warn"><strong>Synthetic data.</strong> '
                f'{html.escape(meta["SYNTHETIC_WARNING"])}</div>')

    parts: list[str] = []
    A = parts.append

    A(f"""<div class="wrap">
<h1>{html.escape(spec['name'])} <span class="badge {meta.get('gate_summary','')}">
{html.escape(meta.get('gate_summary',''))}</span></h1>
<p class="sub">Model Development Document &middot; version {html.escape(spec['version'])}
&middot; trained {html.escape(str(meta.get('saved_at','')))}
&middot; champion form: <code>{html.escape(meta.get('champion_kind',''))}</code></p>
<p>{html.escape(spec['description'])}</p>
{warn}

<div class="kpi">
  <div><div class="l">Gini (out-of-time)</div><div class="v">{oot['gini']:.3f}</div></div>
  <div><div class="l">KS</div><div class="v">{oot['ks']:.1f}</div></div>
  <div><div class="l">Top-decile lift</div><div class="v">{oot['top_decile_lift']:.2f}&times;</div></div>
  <div><div class="l">Rank-order breaks</div><div class="v">{ro.get('n_breaks','-')}</div></div>
  <div><div class="l">Bad rate</div><div class="v">{oot['bad_rate']:.1%}</div></div>
  <div><div class="l">Features</div><div class="v">{len(meta['selected_features'])}
   <span style="font-size:13px;color:#666">/ {meta['n_candidate_features']}</span></div></div>
</div>""")

    # ── 1. Acceptance ───────────────────────────────────────────────────────
    A("<h2>1. Acceptance gates</h2>")
    A("""<div class="note">Gates are two-sided on purpose. A model can fail for
being too weak <em>or</em> too strong: on a book like this a Gini above 0.60 or a
top-decile lift above 5&times; is far more likely to mean a leak than an unusually
good model. Every threshold is defined in <code>ml/pipeline/config.py</code>.</div>""")
    A(_tbl(gates))
    if not passed:
        A("""<div class="warn"><strong>This model did not pass.</strong> It has been
written to the artifact directory so the failure can be inspected, but it has
<em>not</em> been promoted to champion. Promotion on a FAIL is never automatic.</div>""")

    # ── 2. Data ─────────────────────────────────────────────────────────────
    A("<h2>2. Data</h2>")
    sp = meta["split"]
    A(f"""<table><tbody>
<tr><td>Rows (train / validation / out-of-time)</td>
<td>{sp['train']:,} / {sp['valid']:,} / {sp['oot']:,}</td></tr>
<tr><td>Periods — train</td><td>{sp['train_periods'][0]} to {sp['train_periods'][1]}</td></tr>
<tr><td>Periods — out-of-time</td><td>{sp['oot_periods'][0]} to {sp['oot_periods'][1]}</td></tr>
<tr><td>Bad rate (train / oot)</td><td>{tr['bad_rate']:.4f} / {oot['bad_rate']:.4f}</td></tr>
<tr><td>Source rows</td><td>{_n(ds.get('rows', _desc(ds).get('panel_rows')))}</td></tr>
<tr><td>Distinct accounts</td><td>{_n(ds.get('distinct_loans', _desc(ds).get('distinct_loans')))}</td></tr>
<tr><td>Training data hash</td><td><code>{meta.get('training_data_hash','')}</code></td></tr>
<tr><td>Dataset fingerprint</td><td><code>{ds.get('config_fingerprint') or ds.get('config', {}).get('fingerprint', '')}</code></td></tr>
<tr><td>Development panel</td><td>{meta.get('spec', {}).get('training_panel', 'book_simulator')}</td></tr>
</tbody></table>""")
    A("""<div class="note">The split is <strong>chronological</strong>, and the
out-of-time slice is read exactly once, at the end. Outlier caps, WOE bins,
feature selection and both estimators are fitted on train alone; validation is
used only for the stepwise stopping rule. A holdout that any fitting step has
seen is not a holdout.</div>""")
    A(_img(d, "eda/target_over_time.png", "Bad rate by period. The out-of-time window sits at the right."))

    # ── 3. EDA ──────────────────────────────────────────────────────────────
    A("<h2>3. Exploratory analysis</h2>")
    A('<div class="grid">')
    A(_img(d, "eda/missingness.png", "Missing by feature. Missing is never imputed — it becomes its own WOE bin."))
    A(_img(d, "eda/correlation_raw.png", "Raw numeric correlation."))
    A("</div>")
    A("<h3>Univariate profile</h3>")
    A(_tbl(_csv(d, "eda/univariate.csv"), 60))

    # ── 4. Binning ──────────────────────────────────────────────────────────
    A("<h2>4. Binning, WOE and Information Value</h2>")
    A("""<div class="note">Bins are <strong>monotonic by construction</strong>: the
event rate moves in one direction across the bins of an ordinal feature, so the
scorecard cannot say &ldquo;risk rises with DPD, except between 45 and 60&rdquo;.
WOE is ln(good/bad), so <strong>high WOE = low risk</strong> and every fitted
coefficient must be negative &mdash; see section 5.</div>""")
    A(_img(d, "eda/information_value.png"))
    iv = _csv(d, "eda/information_value.csv")
    A(_tbl(iv, 60))
    if meta.get("high_iv_flagged_for_review"):
        A(f"""<div class="warn"><strong>Flagged for review — IV above
{spec['gates']['iv_review']}:</strong>
{html.escape(', '.join(meta['high_iv_flagged_for_review']))}.
The familiar &ldquo;IV&nbsp;&gt;&nbsp;0.5 means a leak&rdquo; rule comes from
application scorecards. On a behaviour model over a delinquent book, days-past-due
and payment history genuinely are that strong, and every flagged feature here is
computed strictly before the observation date while the outcome is drawn strictly
after. They are kept deliberately; the hard drop sits at
{spec['gates']['iv_max']}.</div>""")
    A("<h3>WOE curves for the selected features</h3>")
    A('<div class="grid">')
    for f in meta["selected_features"][:12]:
        A(_img(d, f"eda/woe_{f}.png"))
    A("</div>")
    A("<h3>Coarse classing</h3>")
    A(_tbl(_csv(d, "eda/binning_tables.csv"), 70))

    # ── 5. Selection ────────────────────────────────────────────────────────
    A("<h2>5. Feature selection</h2>")
    A("""<div class="note">Five ordered steps, each logged with what it removed
and why. Correlation runs before VIF because pairwise redundancy is cheaper to
resolve and leaves VIF a better-conditioned problem. The sign check runs
<em>last</em> because a coefficient&rsquo;s sign is only meaningful alongside the
features that survived &mdash; a feature can look correctly signed alone and flip
under control, and that flip is exactly what the check is for.</div>""")
    A(_tbl(_csv(d, "evaluation/selection_log.csv")))
    A("<h3>Correlation pruning</h3>")
    A(_tbl(_csv(d, "evaluation/correlation_dropped.csv"), 40))
    A("<h3>Variance inflation</h3>")
    A(_tbl(_csv(d, "evaluation/vif.csv"), 40))
    A("<h3>Forward stepwise path</h3>")
    A(_tbl(_csv(d, "evaluation/sfs_path.csv"), 40))
    sd = _csv(d, "evaluation/sign_dropped.csv")
    if sd is not None and len(sd):
        A("<h3>Dropped on sign</h3>")
        A(_tbl(sd))
    A(_img(d, "eda/correlation_woe.png", "WOE-space correlation after transformation."))

    # ── 6. Scorecard ────────────────────────────────────────────────────────
    A("<h2>6. The scorecard</h2>")
    sc = meta.get("scorecard", {})
    A(f"""<p>Scaled at <strong>PDO {sc.get('pdo')}</strong>, base score
{sc.get('base_score')} at {sc.get('base_odds')}:1 odds
(factor {sc.get('factor')}, offset {sc.get('offset')}).
<strong>Points read higher = safer</strong>, the way a bureau score does, while the
model&rsquo;s probability reads higher = riskier. They are monotonic inverses; every
evaluation below uses the probability.</p>""")
    A("<h3>Risk bands</h3>")
    A("""<div class="note">Bands are cut at quantiles of the development score
distribution, not at fixed point values, and the observed bad rate per band is
recorded. A fixed cutoff is a guess about where the distribution will land: the
first fitted card put the whole book between 362 and 518 points against a
hardcoded A-grade at 640, so every borrower graded E and the band carried no
information.</div>""")
    A(_tbl(_csv(d, "evaluation/band_table.csv"), 10))
    A("<h3>Points by feature and bin</h3>")
    A(_tbl(_csv(d, "scorecard.csv"), 80))
    A(_img(d, "evaluation/score_distribution.png"))

    # ── 7. Evaluation ───────────────────────────────────────────────────────
    A("<h2>7. Performance</h2>")
    A(f"""<table><thead><tr><th>Sample</th><th>n</th><th>Bad rate</th><th>Gini</th>
<th>KS</th><th>Top-decile lift</th><th>Breaks</th></tr></thead><tbody>
<tr><td>Train</td><td>{tr['n']:,}</td><td>{tr['bad_rate']:.4f}</td>
<td>{tr['gini']:.4f}</td><td>{tr['ks']:.2f}</td>
<td>{tr['top_decile_lift']:.2f}</td><td>{tr['rank_order']['n_breaks']}</td></tr>
<tr><td>Validation</td><td>{va['n']:,}</td><td>{va['bad_rate']:.4f}</td>
<td>{va['gini']:.4f}</td><td>{va['ks']:.2f}</td>
<td>{va['top_decile_lift']:.2f}</td><td>{va['rank_order']['n_breaks']}</td></tr>
<tr><td><strong>Out-of-time</strong></td><td>{oot['n']:,}</td><td>{oot['bad_rate']:.4f}</td>
<td><strong>{oot['gini']:.4f}</strong></td><td><strong>{oot['ks']:.2f}</strong></td>
<td>{oot['top_decile_lift']:.2f}</td><td>{ro.get('n_breaks','-')}</td></tr>
<tr><td>Challenger (GBM, oot)</td><td>{chal.get('n',0):,}</td>
<td>{chal.get('bad_rate',0):.4f}</td><td>{chal.get('gini',0):.4f}</td>
<td>{chal.get('ks',0):.2f}</td><td>{chal.get('top_decile_lift',0):.2f}</td>
<td>{chal.get('rank_order',{}).get('n_breaks','-')}</td></tr>
</tbody></table>""")
    A(f"""<div class="note">The challenger must beat the scorecard by at least
{meta.get('challenger_margin_required')} Gini out-of-time to take the champion
slot. It did not here, so the interpretable model ships: a booster that wins by
noise costs the reason codes, the sign checks and the committee sign-off, and
those are worth more than a hundredth of Gini.</div>"""
      if meta.get("champion_kind") == "scorecard" else
      f"""<div class="warn">The challenger beat the scorecard by more than
{meta.get('challenger_margin_required')} Gini and has taken the champion slot.
Reason codes from the scorecard no longer describe the shipped model.</div>""")

    A("<h3>Decile table — out-of-time, descending by score</h3>")
    A("""<div class="note">Decile 1 is the riskiest tenth. <strong>Bad rate must
fall monotonically down the table</strong>; a decile whose bad rate rises above the
one before it is a rank-order break. Breaks in the top five are counted separately
because that is the end of the book the business acts on.</div>""")
    A(_tbl(_csv(d, "evaluation/decile_oot.csv"), 12))
    A('<div class="grid">')
    A(_img(d, "evaluation/decile_oot.png"))
    A(_img(d, "evaluation/ks_oot.png"))
    A(_img(d, "evaluation/roc.png"))
    A(_img(d, "evaluation/calibration_oot.png"))
    A("</div>")
    A("<h3>Calibration</h3>")
    A(_tbl(_csv(d, "evaluation/calibration_oot.csv"), 12))
    A("<h3>Training decile table</h3>")
    A(_tbl(_csv(d, "evaluation/decile_train.csv"), 12))

    # ── 8. Stability ────────────────────────────────────────────────────────
    A("<h2>8. Stability</h2>")
    A(_img(d, "evaluation/psi.png"))
    A(_tbl(_csv(d, "evaluation/psi.csv"), 30))
    A(f"<p>Score PSI, development against out-of-time: "
      f"<strong>{meta.get('score_psi_train_vs_oot')}</strong>.</p>")
    A("<h3>Performance by segment</h3>")
    A("""<div class="note">A model that works on the book as a whole but is dead in
one segment is not one model with a caveat &mdash; it is two models, one of which
failed. Segments below 200 rows are excluded from the gate.</div>""")
    A(_tbl(_csv(d, "evaluation/segment_performance.csv"), 40))

    # ── 9. Testing ──────────────────────────────────────────────────────────
    A("<h2>9. Adversarial and stability testing</h2>")
    boot, adv = meta.get("bootstrap_gini_oot", {}), meta.get("adversarial_validation", {})
    A(f"""<table><tbody>
<tr><td>Bootstrap Gini (out-of-time)</td><td>{boot.get('point')} &nbsp;
95% CI [{boot.get('ci_lower_2.5')}, {boot.get('ci_upper_97.5')}] over
{boot.get('n_bootstrap')} resamples</td></tr>
<tr><td>Adversarial validation AUC</td>
<td>{adv.get('auc')} &mdash; {html.escape(str(adv.get('verdict','')))}</td></tr>
</tbody></table>""")
    A(f"<p class='sub'>{html.escape(str(adv.get('note','')))}</p>")
    A("<h3>Leakage sniff</h3>")
    A("""<div class="note">The target is shuffled <em>within each period</em> and
each feature re-scored. A genuine predictor keeps most of its power, because the
population-level relationship survives the row-level link being cut. A leaked
feature collapses toward zero. Reported as evidence, not a verdict.</div>""")
    A(_tbl(_csv(d, "evaluation/leakage_sniff.csv"), 25))
    A("<h3>Ablation</h3>")
    A(_tbl(_csv(d, "evaluation/ablation.csv"), 25))

    # ── 10. Limitations ─────────────────────────────────────────────────────
    A("<h2>10. Limitations</h2>")
    A("""<ul>
<li><strong>The data is synthetic.</strong> Every number in this document
demonstrates that the pipeline runs end to end and reports believable figures. It
is not evidence about real borrowers. The simulator's difficulty is a
parameter &mdash; see the sweep table in <code>scripts/build_modelling_dataset.py
--sweep</code> &mdash; and it was set to land in a realistic band, not discovered
there.</li>
<li><strong>Calibration is fitted, not validated against realised cash.</strong>
The probability is well-calibrated against the simulated outcome. On a real book
it would need recalibrating against actual recovery before any rupee figure
derived from it could be trusted.</li>
<li><strong>No causal claim.</strong> This model ranks accounts by risk. It does
not say what to do about them, and it says nothing about which agent should be
sent &mdash; the historical assignment was made by the existing allocator, so
agent-level outcomes are confounded. That needs a randomisation slice, not a
better model.</li>
<li><strong>The out-of-time window is six periods.</strong> Long enough to detect
a shift, not long enough to characterise seasonality.</li>
</ul>""")

    A("</div>")

    doc = (f"<!doctype html><html><head><meta charset='utf-8'>"
           f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
           f"<title>{html.escape(spec['name'])} — Model Development Document</title>"
           f"<style>{CSS}</style></head><body>{''.join(parts)}</body></html>")
    path = d / "MODEL_DEVELOPMENT.html"
    path.write_text(doc, encoding="utf-8")
    return path
