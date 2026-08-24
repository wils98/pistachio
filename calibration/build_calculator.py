"""
Generates calibration/calculator.html — a self-contained, offline
interactive tool for exploring how the native calibration turns ratings
into wOBA/WAR, step by step. No backend: the page embeds tables_native.py's
data as JSON and a JS port of rating_lookup.interpolate_lookup(), structured
to mirror the Python function 1:1 for easy side-by-side review.

Mirrors the *potential*-rating calculation path specifically (single value
per category — calc_potential_hitting_metrics_native/
calc_potential_pitching_metrics_native's exact math), including the
split-aware rebuild: each category has separate vsR/vsL tables (fit against
real split-specific performance, not a fit-time blend), and the single
potential value is run through both, then combined using the selected
bats/throws' REAL exposure weight (HANDEDNESS_WEIGHTS_NATIVE_HITTING/
_PITCHING) — not a flat 0.7/0.3. Shows offense-only WAR (no fielding/
position value) and lets the user pick Starter/Reliever directly rather
than deriving it from pitch-type ratings — both still stopgap-based, not
natively calibrated (see README.md's "deliberately out of scope").

Generated, not hand-typed — reviewed before being treated as final, same
discipline as fit_tables.py.

Usage:
    python calibration/build_calculator.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import (
    BASE_HITTING_RATES,
    BASE_PITCHING_RATES,
    BATTING_WOBA_WEIGHTS,
    PITCHING_WOBA_WEIGHTS,
    RUNS_PER_WIN,
    RELIEVER_VS_STARTER_AVERAGE_IP,
)
from tables_native import (
    BATTING_COMPONENTS_ADJUST_MAP_NATIVE,
    PITCHING_COMPONENTS_ADJUST_MAP_NATIVE,
    HANDEDNESS_WEIGHTS_NATIVE_HITTING,
    HANDEDNESS_WEIGHTS_NATIVE_PITCHING,
    RUNS_PER_GAME_HITTING_COEFF_NATIVE,
    RUNS_PER_GAME_HITTING_CONST_NATIVE,
    RUNS_PER_GAME_PITCHING_COEFF_NATIVE,
    RUNS_PER_GAME_PITCHING_CONST_NATIVE,
)

HITTING_CATEGORIES = ["babip", "avk", "gap", "pow", "eye", "speed"]
HITTING_OUTCOMES = ["hr_pct", "k_pct", "bb_pct", "1b_pct", "2b_pct", "3b_pct"]
PITCHING_CATEGORIES = ["Control", "pBABIP", "HRA", "Stuff", "Stamina"]
PITCHING_OUTCOMES = ["hr_vs", "bb_vs", "k_vs", "h_nothr_vs"]

HITTING_HAND_LABEL = "Bats"
PITCHING_HAND_LABEL = "Throws"
HITTING_HAND_OPTIONS = [("L", "Left"), ("R", "Right"), ("S", "Switch")]
PITCHING_HAND_OPTIONS = [("L", "Left"), ("R", "Right")]


def _category_range(table_by_side: dict) -> tuple[float, float]:
    keys = [float(k) for side in table_by_side.values() for k in side.keys()]
    return min(keys), max(keys)


def build_data() -> dict:
    hitting_ranges = {cat: _category_range(BATTING_COMPONENTS_ADJUST_MAP_NATIVE[cat]) for cat in HITTING_CATEGORIES}
    pitching_ranges = {cat: _category_range(PITCHING_COMPONENTS_ADJUST_MAP_NATIVE[cat]) for cat in PITCHING_CATEGORIES}

    return {
        "hitting": {
            "categories": HITTING_CATEGORIES,
            "outcomes": HITTING_OUTCOMES,
            "table": BATTING_COMPONENTS_ADJUST_MAP_NATIVE,
            "baseRates": {
                "hr_pct": BASE_HITTING_RATES["hr_pct_baserate"],
                "k_pct": BASE_HITTING_RATES["k_pct_baserate"],
                "bb_pct": BASE_HITTING_RATES["bb_pct_baserate"],
                "1b_pct": BASE_HITTING_RATES["1b_pct_baserate"],
                "2b_pct": BASE_HITTING_RATES["2b_pct_baserate"],
                "3b_pct": BASE_HITTING_RATES["3b_pct_baserate"],
            },
            "wobaWeights": {
                "hr_pct": BATTING_WOBA_WEIGHTS["hr_pct_wOBA_weight"],
                "bb_pct": BATTING_WOBA_WEIGHTS["bb_pct_wOBA_weight"],
                "1b_pct": BATTING_WOBA_WEIGHTS["1b_pct_wOBA_weight"],
                "2b_pct": BATTING_WOBA_WEIGHTS["2b_pct_wOBA_weight"],
                "3b_pct": BATTING_WOBA_WEIGHTS["3b_pct_wOBA_weight"],
            },
            "runsCoeff": RUNS_PER_GAME_HITTING_COEFF_NATIVE,
            "runsConst": RUNS_PER_GAME_HITTING_CONST_NATIVE,
            "ranges": hitting_ranges,
            "handednessWeights": HANDEDNESS_WEIGHTS_NATIVE_HITTING,
            "handLabel": HITTING_HAND_LABEL,
            "handOptions": HITTING_HAND_OPTIONS,
            "defaultHand": "R",
        },
        "pitching": {
            "categories": PITCHING_CATEGORIES,
            "outcomes": PITCHING_OUTCOMES,
            "table": PITCHING_COMPONENTS_ADJUST_MAP_NATIVE,
            "baseRates": {
                "hr_vs": BASE_PITCHING_RATES["hr_vs_baserate"],
                "bb_vs": BASE_PITCHING_RATES["bb_vs_baserate"],
                "k_vs": BASE_PITCHING_RATES["k_vs_baserate"],
                "h_nothr_vs": BASE_PITCHING_RATES["h_nothr_vs_baserate"],
            },
            "wobaWeights": {
                "hr_vs": PITCHING_WOBA_WEIGHTS["hr_vs_wOBA_weight"],
                "bb_vs": PITCHING_WOBA_WEIGHTS["bb_vs_wOBA_weight"],
                "h_nothr_vs": PITCHING_WOBA_WEIGHTS["h_nothr_vs_wOBA_weight"],
            },
            "runsCoeff": RUNS_PER_GAME_PITCHING_COEFF_NATIVE,
            "runsConst": RUNS_PER_GAME_PITCHING_CONST_NATIVE,
            "ranges": pitching_ranges,
            "handednessWeights": HANDEDNESS_WEIGHTS_NATIVE_PITCHING,
            "handLabel": PITCHING_HAND_LABEL,
            "handOptions": PITCHING_HAND_OPTIONS,
            "defaultHand": "R",
        },
        "runsPerWin": RUNS_PER_WIN,
        "relieverIpShare": RELIEVER_VS_STARTER_AVERAGE_IP,
    }


OUTCOME_LABELS = {
    "hr_pct": "HR%", "k_pct": "K%", "bb_pct": "BB%",
    "1b_pct": "1B%", "2b_pct": "2B%", "3b_pct": "3B%",
    "hr_vs": "HR%", "bb_vs": "BB%", "k_vs": "K%", "h_nothr_vs": "H(no-HR)%",
}
CATEGORY_LABELS = {
    "babip": "BABIP", "avk": "Avoid-K", "gap": "Gap", "pow": "Power", "eye": "Eye", "speed": "Speed",
    "Control": "Control", "pBABIP": "pBABIP", "HRA": "HR Allowed", "Stuff": "Stuff", "Stamina": "Stamina",
}

HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>Ratings → wOBA/WAR Calculator</title>
<meta name="viewport" content="width=device-width, initial-scale=1" />
<style>
:root {{
  --bg: #f3efe4;
  --surface: #fffdf7;
  --surface-2: #ece6d4;
  --border: #d8d0bc;
  --text: #22261d;
  --text-dim: #6b6a56;
  --accent: #b5762a;
  --accent-soft: #e8d6b8;
  --positive: #1f8f6b;
  --negative: #c0432f;
  --font-ui: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  --font-mono: ui-monospace, "SF Mono", "Cascadia Code", "Roboto Mono", Menlo, Consolas, monospace;
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --bg: #11150f;
    --surface: #171d15;
    --surface-2: #1e2519;
    --border: #2c3527;
    --text: #eef1e8;
    --text-dim: #9aa593;
    --accent: #e8a33d;
    --accent-soft: #3a2e14;
    --positive: #5fd0a0;
    --negative: #e2665a;
  }}
}}
:root[data-theme="dark"] {{
  --bg: #11150f; --surface: #171d15; --surface-2: #1e2519; --border: #2c3527;
  --text: #eef1e8; --text-dim: #9aa593; --accent: #e8a33d; --accent-soft: #3a2e14;
  --positive: #5fd0a0; --negative: #e2665a;
}}
:root[data-theme="light"] {{
  --bg: #f3efe4; --surface: #fffdf7; --surface-2: #ece6d4; --border: #d8d0bc;
  --text: #22261d; --text-dim: #6b6a56; --accent: #b5762a; --accent-soft: #e8d6b8;
  --positive: #1f8f6b; --negative: #c0432f;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0; background: var(--bg); color: var(--text); font-family: var(--font-ui);
  font-size: 15px; line-height: 1.5;
}}
.wrap {{ max-width: 1280px; margin: 0 auto; padding: 2rem 1.5rem 4rem; }}
header {{ margin-bottom: 1.75rem; }}
h1 {{
  font-size: 1.5rem; font-weight: 800; text-transform: uppercase; letter-spacing: 0.04em;
  margin: 0 0 0.35rem; text-wrap: balance;
}}
header p {{ color: var(--text-dim); margin: 0; max-width: 68ch; }}
.tabs {{ display: flex; gap: 0.5rem; margin: 1.5rem 0 1.25rem; }}
.tab {{
  font-family: var(--font-ui); font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em;
  font-size: 0.8rem; padding: 0.55rem 1.1rem; border-radius: 999px; border: 1px solid var(--border);
  background: var(--surface); color: var(--text-dim); cursor: pointer;
}}
.tab[aria-selected="true"] {{ background: var(--accent); color: var(--bg); border-color: var(--accent); }}
.tab:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; }}
.grid {{ display: grid; grid-template-columns: minmax(260px, 320px) 1fr; gap: 1.5rem; align-items: start; }}
@media (max-width: 900px) {{ .grid {{ grid-template-columns: 1fr; }} }}
.card {{
  background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 1.25rem;
}}
.card + .card {{ margin-top: 1rem; }}
.card h2 {{
  font-size: 0.75rem; font-weight: 800; text-transform: uppercase; letter-spacing: 0.08em;
  color: var(--text-dim); margin: 0 0 1rem; display: flex; justify-content: space-between; align-items: baseline;
}}
.card h2 .hint {{ font-weight: 500; text-transform: none; letter-spacing: 0; font-size: 0.72rem; color: var(--text-dim); }}
.field {{ display: flex; flex-direction: column; gap: 0.35rem; margin-bottom: 1rem; }}
.field:last-child {{ margin-bottom: 0; }}
.field-label {{ display: flex; justify-content: space-between; align-items: baseline; font-size: 0.85rem; }}
.field-label span:first-child {{ font-weight: 600; }}
.field-value {{ font-family: var(--font-mono); font-variant-numeric: tabular-nums; color: var(--accent); font-weight: 700; }}
input[type="range"] {{
  width: 100%; accent-color: var(--accent); height: 1.4rem; background: transparent; cursor: pointer;
}}
.extrap-flag {{
  font-size: 0.7rem; color: var(--negative); font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em;
  visibility: hidden;
}}
.extrap-flag.on {{ visibility: visible; }}
.toggle-row {{ display: flex; gap: 0.5rem; }}
.toggle-row button {{
  flex: 1; font-family: var(--font-ui); font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em;
  font-size: 0.78rem; padding: 0.5rem; border-radius: 6px; border: 1px solid var(--border);
  background: var(--surface-2); color: var(--text-dim); cursor: pointer;
}}
.toggle-row button[aria-pressed="true"] {{ background: var(--accent-soft); color: var(--accent); border-color: var(--accent); }}
.exposure-note {{
  font-size: 0.78rem; color: var(--text-dim); background: var(--surface-2); border-radius: 6px;
  padding: 0.55rem 0.7rem; margin-top: 0.6rem; font-family: var(--font-mono); font-variant-numeric: tabular-nums;
}}
.exposure-note b {{ color: var(--text); font-family: var(--font-ui); }}
.table-scroll {{ overflow-x: auto; }}
table {{ width: 100%; border-collapse: collapse; font-size: 0.83rem; }}
th, td {{
  padding: 0.4rem 0.55rem; text-align: right; border-bottom: 1px solid var(--border);
  font-variant-numeric: tabular-nums; font-family: var(--font-mono); white-space: nowrap;
}}
th:first-child, td:first-child {{ text-align: left; font-family: var(--font-ui); }}
thead th {{
  font-family: var(--font-ui); font-weight: 700; text-transform: uppercase; font-size: 0.66rem;
  letter-spacing: 0.05em; color: var(--text-dim); border-bottom: 1px solid var(--border);
}}
tr.total-row td {{ font-weight: 800; border-top: 2px solid var(--border); border-bottom: none; color: var(--text); }}
tr.base-row td {{ color: var(--text-dim); }}
tr.side-r td:first-child, tr.side-l td:first-child {{ padding-left: 1.3rem; color: var(--text-dim); font-style: italic; }}
td.pos {{ color: var(--positive); }}
td.neg {{ color: var(--negative); }}
.summary {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 0.75rem; margin-top: 1rem; }}
.stat {{ background: var(--surface-2); border-radius: 8px; padding: 0.85rem 1rem; }}
.stat .label {{ font-size: 0.68rem; text-transform: uppercase; letter-spacing: 0.06em; color: var(--text-dim); font-weight: 700; }}
.stat .value {{ font-family: var(--font-mono); font-variant-numeric: tabular-nums; font-size: 1.5rem; font-weight: 800; margin-top: 0.15rem; }}
.step {{ color: var(--text-dim); font-size: 0.8rem; margin-top: 0.9rem; font-family: var(--font-mono); }}
.step b {{ color: var(--text); }}
.impact-row {{ display: grid; grid-template-columns: 5.5rem 1fr 4.2rem; align-items: center; gap: 0.6rem; margin-bottom: 0.55rem; }}
.impact-row:last-child {{ margin-bottom: 0; }}
.impact-label {{ font-size: 0.82rem; font-weight: 600; }}
.impact-track {{ background: var(--surface-2); border-radius: 4px; height: 0.6rem; overflow: hidden; }}
.impact-fill {{ background: var(--accent); height: 100%; border-radius: 4px; }}
.impact-value {{ font-family: var(--font-mono); font-variant-numeric: tabular-nums; font-size: 0.78rem; text-align: right; color: var(--text-dim); }}
.impact-hint {{ font-size: 0.75rem; color: var(--text-dim); margin-top: 0.75rem; }}
.panel {{ display: none; }}
.panel.active {{ display: block; }}
footer {{ margin-top: 2.5rem; color: var(--text-dim); font-size: 0.78rem; max-width: 68ch; }}
footer a {{ color: var(--accent); }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>Ratings → wOBA / WAR</h1>
    <p>Move a rating and watch the calculation rebuild itself, step by step &mdash; the same math
    <code>calibration/metrics_hitting_native.py</code> / <code>metrics_pitching_native.py</code> run,
    fit separately against real vs-RHP/vs-LHP performance and blended by real exposure weights
    (not a flat 70/30). Offense/pitching value only &mdash; no fielding, no role-eligibility
    thresholds (still on the old scale; see calibration/README.md).</p>
  </header>

  <div class="tabs" role="tablist">
    <button class="tab" id="tab-hitting" role="tab" aria-selected="true" aria-controls="panel-hitting">Hitting</button>
    <button class="tab" id="tab-pitching" role="tab" aria-selected="false" aria-controls="panel-pitching">Pitching</button>
  </div>

  <div id="panel-hitting" class="panel active" role="tabpanel" aria-labelledby="tab-hitting">
    <div class="grid">
      <div class="card">
        <h2>Ratings</h2>
        <div id="hitting-inputs"></div>
        <div class="field">
          <div class="field-label"><span id="hitting-hand-label"></span></div>
          <div class="toggle-row" id="hitting-hand-toggle"></div>
          <div class="exposure-note" id="hitting-exposure"></div>
        </div>
      </div>
      <div>
        <div class="card">
          <h2>Rating impact <span class="hint">wOBA per +10 rating points, this bats side</span></h2>
          <div id="hitting-impact"></div>
          <div class="impact-hint">Constant per category (the fit is linear) &mdash; ranks which
          rating moves wOBA most, independent of where its slider currently sits.</div>
        </div>
        <div class="card">
          <h2>Rate build-up <span class="hint">vs RHP, vs LHP, then real-exposure blend</span></h2>
          <div class="table-scroll"><table id="hitting-breakdown"></table></div>
        </div>
        <div class="card">
          <h2>wOBA</h2>
          <div class="table-scroll"><table id="hitting-woba"></table></div>
          <div class="summary" id="hitting-summary"></div>
        </div>
      </div>
    </div>
  </div>

  <div id="panel-pitching" class="panel" role="tabpanel" aria-labelledby="tab-pitching">
    <div class="grid">
      <div class="card">
        <h2>Ratings</h2>
        <div id="pitching-inputs"></div>
        <div class="field">
          <div class="field-label"><span id="pitching-hand-label"></span></div>
          <div class="toggle-row" id="pitching-hand-toggle"></div>
          <div class="exposure-note" id="pitching-exposure"></div>
        </div>
        <div class="field">
          <div class="field-label"><span>Role</span></div>
          <div class="toggle-row">
            <button id="role-sp" aria-pressed="true">Starter</button>
            <button id="role-rp" aria-pressed="false">Reliever</button>
          </div>
        </div>
      </div>
      <div>
        <div class="card">
          <h2>Rating impact <span class="hint">pwOBA improvement per +10 points, this throws side</span></h2>
          <div id="pitching-impact"></div>
          <div class="impact-hint">Constant per category (the fit is linear) &mdash; ranks which
          rating suppresses pwOBA most, independent of where its slider currently sits.</div>
        </div>
        <div class="card">
          <h2>Rate build-up <span class="hint">vs RHB, vs LHB, then real-exposure blend</span></h2>
          <div class="table-scroll"><table id="pitching-breakdown"></table></div>
        </div>
        <div class="card">
          <h2>pwOBA</h2>
          <div class="table-scroll"><table id="pitching-woba"></table></div>
          <div class="summary" id="pitching-summary"></div>
        </div>
      </div>
    </div>
  </div>

  <footer>
    Generated by <code>calibration/build_calculator.py</code> from
    <code>calibration/tables_native.py</code> &mdash; a reviewed literal export, not recomputed
    at runtime. Each category is fit separately against real vs-R/vs-L performance (not a
    shared table), so its two rows below can genuinely disagree, not just scale together.
    Red-flagged categories are extrapolating past that side's fitted range &mdash; still
    well-behaved by design (see <code>rating_lookup.py</code>), but the least-anchored part
    of the model.
  </footer>
</div>

<script>
const DATA = {data_json};
const OUTCOME_LABELS = {outcome_labels_json};
const CATEGORY_LABELS = {category_labels_json};

// Mirrors rating_lookup.interpolate_lookup() 1:1.
function interpolateLookup(value, table) {{
  const keys = Object.keys(table).sort((a, b) => parseFloat(a) - parseFloat(b));
  let v = (value === null || Number.isNaN(value)) ? parseFloat(keys[0]) : value;

  let lo, hi;
  if (v <= parseFloat(keys[0])) {{
    lo = keys[0]; hi = keys[1];
  }} else if (v >= parseFloat(keys[keys.length - 1])) {{
    lo = keys[keys.length - 2]; hi = keys[keys.length - 1];
  }} else {{
    for (let i = 0; i < keys.length - 1; i++) {{
      if (parseFloat(keys[i]) <= v && v <= parseFloat(keys[i + 1])) {{ lo = keys[i]; hi = keys[i + 1]; break; }}
    }}
  }}

  const x0 = parseFloat(lo), x1 = parseFloat(hi);
  const frac = (v - x0) / (x1 - x0);
  const y0 = table[lo], y1 = table[hi];
  const out = {{}};
  for (const k in y0) {{ out[k] = y0[k] + (y1[k] - y0[k]) * frac; }}
  return out;
}}

function fmt(n, d) {{ return (n < 0 ? "" : "+") + n.toFixed(d === undefined ? 4 : d); }}
function fmtPlain(n, d) {{ return n.toFixed(d === undefined ? 4 : d); }}

const state = {{ hitting: {{ hand: "R" }}, pitching: {{ hand: "R" }} }};

function buildInputs(container, side) {{
  const cfg = DATA[side];
  container.innerHTML = "";
  cfg.categories.forEach(cat => {{
    const [lo, hi] = cfg.ranges[cat];
    const pad = Math.max(5, Math.round((hi - lo) * 0.25));
    const min = Math.floor(lo - pad), max = Math.ceil(hi + pad);
    const start = Math.round((lo + hi) / 2);

    const field = document.createElement("div");
    field.className = "field";
    field.innerHTML = `
      <div class="field-label">
        <span>${{CATEGORY_LABELS[cat]}}</span>
        <span><span class="extrap-flag" data-flag="${{side}}-${{cat}}">extrapolating</span>
        <span class="field-value" data-value="${{side}}-${{cat}}">${{start}}</span></span>
      </div>
      <input type="range" min="${{min}}" max="${{max}}" value="${{start}}" data-slider="${{side}}-${{cat}}" data-cat="${{cat}}" data-side="${{side}}" />
    `;
    container.appendChild(field);
  }});
}}

function buildHandToggle(side, onChange) {{
  const cfg = DATA[side];
  document.getElementById(`${{side}}-hand-label`).textContent = cfg.handLabel;
  const container = document.getElementById(`${{side}}-hand-toggle`);
  container.innerHTML = "";
  cfg.handOptions.forEach(([code, label]) => {{
    const btn = document.createElement("button");
    btn.textContent = label;
    btn.setAttribute("aria-pressed", code === state[side].hand ? "true" : "false");
    btn.addEventListener("click", () => {{
      state[side].hand = code;
      [...container.children].forEach(b => b.setAttribute("aria-pressed", "false"));
      btn.setAttribute("aria-pressed", "true");
      onChange();
    }});
    container.appendChild(btn);
  }});
}}

function wireInputs(side, onChange) {{
  DATA[side].categories.forEach(cat => {{
    document.querySelector(`[data-slider="${{side}}-${{cat}}"]`).addEventListener("input", onChange);
  }});
}}

function readInputs(side) {{
  const out = {{}};
  DATA[side].categories.forEach(cat => {{
    out[cat] = parseFloat(document.querySelector(`[data-slider="${{side}}-${{cat}}"]`).value);
  }});
  return out;
}}

function updateFlags(side, values) {{
  DATA[side].categories.forEach(cat => {{
    const [lo, hi] = DATA[side].ranges[cat];
    const v = values[cat];
    const flag = document.querySelector(`[data-flag="${{side}}-${{cat}}"]`);
    const valueEl = document.querySelector(`[data-value="${{side}}-${{cat}}"]`);
    valueEl.textContent = v;
    flag.classList.toggle("on", v < lo || v > hi);
  }});
}}

function computeForPitcherSide(side, values, pitcherSide) {{
  const cfg = DATA[side];
  const rates = Object.assign({{}}, cfg.baseRates);
  const perCategory = {{}};
  cfg.categories.forEach(cat => {{
    const adj = interpolateLookup(values[cat], cfg.table[cat][pitcherSide]);
    perCategory[cat] = adj;
    cfg.outcomes.forEach(o => {{ rates[o] += adj[`${{o}}_adj`]; }});
  }});
  return {{ rates, perCategory }};
}}

function computeSide(side, values) {{
  const cfg = DATA[side];
  const hand = state[side].hand;
  const weights = cfg.handednessWeights[hand] || cfg.handednessWeights[cfg.defaultHand];

  const r = computeForPitcherSide(side, values, "R");
  const l = computeForPitcherSide(side, values, "L");

  const rates = {{}};
  cfg.outcomes.forEach(o => {{ rates[o] = r.rates[o] * weights.R + l.rates[o] * weights.L; }});

  let woba = 0;
  const wobaTerms = {{}};
  for (const o in cfg.wobaWeights) {{
    const term = cfg.wobaWeights[o] * rates[o];
    wobaTerms[o] = term;
    woba += term;
  }}

  const runsPer162 = woba * cfg.runsCoeff - cfg.runsConst;
  const war = runsPer162 / DATA.runsPerWin;

  return {{ r, l, weights, rates, woba, wobaTerms, runsPer162, war }};
}}

function renderBreakdown(tableEl, side, result) {{
  const cfg = DATA[side];
  let html = "<thead><tr><th>Category</th>";
  cfg.outcomes.forEach(o => {{ html += `<th>${{OUTCOME_LABELS[o]}}</th>`; }});
  html += "</tr></thead><tbody>";

  html += '<tr class="base-row"><td>Base rate</td>';
  cfg.outcomes.forEach(o => {{ html += `<td>${{fmtPlain(cfg.baseRates[o])}}</td>`; }});
  html += "</tr>";

  cfg.categories.forEach(cat => {{
    html += `<tr><td>${{CATEGORY_LABELS[cat]}} (blended)</td>`;
    cfg.outcomes.forEach(o => {{
      const vR = result.r.perCategory[cat][`${{o}}_adj`];
      const vL = result.l.perCategory[cat][`${{o}}_adj`];
      const v = vR * result.weights.R + vL * result.weights.L;
      const cls = v > 0 ? "pos" : (v < 0 ? "neg" : "");
      html += `<td class="${{cls}}">${{fmt(v)}}</td>`;
    }});
    html += "</tr>";
    html += `<tr class="side-r"><td>vs R (${{(result.weights.R * 100).toFixed(0)}}%)</td>`;
    cfg.outcomes.forEach(o => {{ html += `<td>${{fmt(result.r.perCategory[cat][`${{o}}_adj`])}}</td>`; }});
    html += "</tr>";
    html += `<tr class="side-l"><td>vs L (${{(result.weights.L * 100).toFixed(0)}}%)</td>`;
    cfg.outcomes.forEach(o => {{ html += `<td>${{fmt(result.l.perCategory[cat][`${{o}}_adj`])}}</td>`; }});
    html += "</tr>";
  }});

  html += '<tr class="total-row"><td>Total rate</td>';
  cfg.outcomes.forEach(o => {{ html += `<td>${{fmtPlain(result.rates[o])}}</td>`; }});
  html += "</tr></tbody>";
  tableEl.innerHTML = html;
}}

function renderWoba(tableEl, summaryEl, side, result) {{
  const cfg = DATA[side];
  let html = "<thead><tr><th>Component</th><th>Rate</th><th>Weight</th><th>Contribution</th></tr></thead><tbody>";
  for (const o in cfg.wobaWeights) {{
    html += `<tr><td>${{OUTCOME_LABELS[o]}}</td><td>${{fmtPlain(result.rates[o])}}</td><td>&times;${{cfg.wobaWeights[o]}}</td><td>${{fmtPlain(result.wobaTerms[o])}}</td></tr>`;
  }}
  html += `<tr class="total-row"><td colspan="3">wOBA</td><td>${{fmtPlain(result.woba, 4)}}</td></tr>`;
  html += "</tbody>";
  tableEl.innerHTML = html;

  let warLabel = "WAR", warValue = result.war;
  let stepHtml = `<b>${{fmtPlain(result.woba, 4)}}</b> &times; ${{cfg.runsCoeff}} &minus; ${{cfg.runsConst}} = <b>${{fmtPlain(result.runsPer162, 2)}}</b> runs/650 PA &divide; ${{DATA.runsPerWin}} runs/win`;
  if (side === "pitching") {{
    const roleMult = window.__pitchRole === "rp" ? DATA.relieverIpShare : 1;
    warValue = -result.war * roleMult;
    warLabel = window.__pitchRole === "rp" ? "WAR (reliever share)" : "WAR (starter)";
    stepHtml = `&minus;(<b>${{fmtPlain(result.woba, 4)}}</b> &times; ${{cfg.runsCoeff}} &minus; ${{cfg.runsConst}}) &divide; ${{DATA.runsPerWin}} &times; ${{roleMult.toFixed(3)}} role share`;
  }}

  summaryEl.innerHTML = `
    <div class="stat"><div class="label">${{side === "hitting" ? "wOBA" : "pwOBA"}}</div><div class="value">${{fmtPlain(result.woba, 3)}}</div></div>
    <div class="stat"><div class="label">${{warLabel}}</div><div class="value">${{fmtPlain(warValue, 1)}}</div></div>
  `;
  summaryEl.nextElementSibling?.remove();
  const step = document.createElement("div");
  step.className = "step";
  step.innerHTML = stepHtml;
  summaryEl.after(step);
}}

function renderExposure(side, result) {{
  const cfg = DATA[side];
  const noun = side === "hitting" ? "PA" : "batters faced";
  const opp = side === "hitting" ? ["RHP", "LHP"] : ["RHB", "LHB"];
  document.getElementById(`${{side}}-exposure`).innerHTML =
    `Real ${{cfg.handLabel.toLowerCase()}} ${{state[side].hand}} exposure: <b>${{(result.weights.R * 100).toFixed(1)}}%</b> ${{noun}} vs ${{opp[0]}}, ` +
    `<b>${{(result.weights.L * 100).toFixed(1)}}%</b> vs ${{opp[1]}} (calibration/extract_pairs.py, real 2101-2104 stats).`;
}}

// Each category's table is a straight line (fit deliberately linear — see
// rating_lookup.py), so its slope (== the original OLS coefficient) is
// recoverable directly from any two bucket points; using the table's own
// first/last keys avoids relying on a fixed 5-point bucket gap. Sums each
// category's per-outcome slope weighted by the wOBA weights, blended R/L by
// the currently selected hand's real exposure weight — same blend the main
// calculation uses, just applied to the coefficients instead of the rates.
function tableSlope(table) {{
  const keys = Object.keys(table).sort((a, b) => parseFloat(a) - parseFloat(b));
  const lo = keys[0], hi = keys[keys.length - 1];
  const x0 = parseFloat(lo), x1 = parseFloat(hi);
  const y0 = table[lo], y1 = table[hi];
  const slope = {{}};
  for (const k in y0) {{ slope[k] = (y1[k] - y0[k]) / (x1 - x0); }}
  return slope;
}}

function computeMarginalImpact(side) {{
  const cfg = DATA[side];
  const weights = cfg.handednessWeights[state[side].hand] || cfg.handednessWeights[cfg.defaultHand];
  const impacts = cfg.categories.map(cat => {{
    const slopeR = tableSlope(cfg.table[cat]["R"]);
    const slopeL = tableSlope(cfg.table[cat]["L"]);
    let perPoint = 0;
    for (const o in cfg.wobaWeights) {{
      const blended = slopeR[`${{o}}_adj`] * weights.R + slopeL[`${{o}}_adj`] * weights.L;
      perPoint += blended * cfg.wobaWeights[o];
    }}
    return {{ cat, perPoint }};
  }});
  impacts.sort((a, b) => Math.abs(b.perPoint) - Math.abs(a.perPoint));
  return impacts;
}}

function renderImpact(container, side, impacts) {{
  const maxAbs = Math.max(...impacts.map(i => Math.abs(i.perPoint))) || 1;
  const sign = side === "pitching" ? -1 : 1; // pitching: lower pwOBA is better, flip for "improvement"
  container.innerHTML = impacts.map(({{ cat, perPoint }}) => {{
    const per10 = perPoint * 10 * sign;
    const pct = (Math.abs(perPoint) / maxAbs) * 100;
    return `
      <div class="impact-row">
        <div class="impact-label">${{CATEGORY_LABELS[cat]}}</div>
        <div class="impact-track"><div class="impact-fill" style="width:${{pct}}%"></div></div>
        <div class="impact-value">${{per10 >= 0 ? "+" : ""}}${{per10.toFixed(4)}}</div>
      </div>
    `;
  }}).join("");
}}

function refreshHitting() {{
  const values = readInputs("hitting");
  updateFlags("hitting", values);
  const result = computeSide("hitting", values);
  renderBreakdown(document.getElementById("hitting-breakdown"), "hitting", result);
  renderWoba(document.getElementById("hitting-woba"), document.getElementById("hitting-summary"), "hitting", result);
  renderExposure("hitting", result);
  renderImpact(document.getElementById("hitting-impact"), "hitting", computeMarginalImpact("hitting"));
}}

function refreshPitching() {{
  const values = readInputs("pitching");
  updateFlags("pitching", values);
  const result = computeSide("pitching", values);
  renderBreakdown(document.getElementById("pitching-breakdown"), "pitching", result);
  renderWoba(document.getElementById("pitching-woba"), document.getElementById("pitching-summary"), "pitching", result);
  renderExposure("pitching", result);
  renderImpact(document.getElementById("pitching-impact"), "pitching", computeMarginalImpact("pitching"));
}}

buildInputs(document.getElementById("hitting-inputs"), "hitting");
buildInputs(document.getElementById("pitching-inputs"), "pitching");
wireInputs("hitting", refreshHitting);
wireInputs("pitching", refreshPitching);
buildHandToggle("hitting", refreshHitting);
buildHandToggle("pitching", refreshPitching);

window.__pitchRole = "sp";
document.getElementById("role-sp").addEventListener("click", () => {{
  window.__pitchRole = "sp";
  document.getElementById("role-sp").setAttribute("aria-pressed", "true");
  document.getElementById("role-rp").setAttribute("aria-pressed", "false");
  refreshPitching();
}});
document.getElementById("role-rp").addEventListener("click", () => {{
  window.__pitchRole = "rp";
  document.getElementById("role-rp").setAttribute("aria-pressed", "true");
  document.getElementById("role-sp").setAttribute("aria-pressed", "false");
  refreshPitching();
}});

const tabHitting = document.getElementById("tab-hitting");
const tabPitching = document.getElementById("tab-pitching");
tabHitting.addEventListener("click", () => {{
  tabHitting.setAttribute("aria-selected", "true");
  tabPitching.setAttribute("aria-selected", "false");
  document.getElementById("panel-hitting").classList.add("active");
  document.getElementById("panel-pitching").classList.remove("active");
}});
tabPitching.addEventListener("click", () => {{
  tabPitching.setAttribute("aria-selected", "true");
  tabHitting.setAttribute("aria-selected", "false");
  document.getElementById("panel-pitching").classList.add("active");
  document.getElementById("panel-hitting").classList.remove("active");
}});

refreshHitting();
refreshPitching();
</script>
</body>
</html>
"""


def main():
    data = build_data()
    html = HTML_TEMPLATE.format(
        data_json=json.dumps(data),
        outcome_labels_json=json.dumps(OUTCOME_LABELS),
        category_labels_json=json.dumps(CATEGORY_LABELS),
    )
    out_path = Path(__file__).resolve().parent / "calculator.html"
    out_path.write_text(html, encoding="utf-8")
    print(f"Wrote {out_path} ({len(html):,} bytes)")


if __name__ == "__main__":
    main()
