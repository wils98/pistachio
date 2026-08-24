# calibration/ — native PSD regression refit

Net-new workstream that fits pistachio's regression tables to **real PSD league data**
(ratings-snapshot → same-season performance), replacing upstream's other-league coefficients.
Nothing here is imported by the live pipeline (`main.py`/`config.py`/`metrics_*.py`) — it runs
standalone and produces its own `*_native.html` pages for side-by-side comparison.

## Workflow

```
extract_team_runs.py  ─→ data/team_*_runs.csv      ─→ fit_runs_per_game.py   (Workstream A)
extract_pairs.py      ─→ data/{hitting,pitching}_pairs.csv ─→ fit_tables.py  (Workstream B)
                                                        └─→ writes tables_native.py
extract_thresholds.py ─→ data/{role,position}_dataset.csv, writes thresholds_native.py (Phase A)
metrics_{hitting,pitching}_native.py  — mirror the production formulas, import tables_native
main_native.py                        — standalone end-to-end run → hitters_native.html / pitchers_native.html
build_calculator.py                   — standalone → calculator.html (interactive, offline)
```

Run everything with `PISTACHIO_DB_PATH` pointing at (a copy of) psd-ootp's DB. The fit scripts
need `uv sync --extra calibration` (statsmodels/matplotlib); `main_native.py`/`build_calculator.py`
themselves need only the base deps — `tables_native.py` is literal constants, nothing is refit
at runtime.

## Data & methodology (settled 2026-07-30, see NOTES.md Pass 6/7; extended 2026-08-22, Pass 8)

Four concurrent (same-season), **split-aware** MLB-only pairs — population driven from the
**stats side** (`level_id=1`, `is_latest=1`), never the rating row's own unreliable `level_id`.
Split-aware means: real per-player vsR/vsL performance
(`player_batting_stats_history.split_id` — 2=vsL, 3=vsR) is paired with the *matching side's*
rating (real vsR outcome ↔ `powR`/`ctrlR`, not a blend of both sides) — a `PA`/`BF ≥ 30`
per-split floor drops noisy small samples:

| season | ratings snapshot | usable pairs, hitting (R/L) | pitching (R/L) |
|---|---|---|---|
| 2101 | `2101-12-31` (real dump) | 367 / 334 | 353 / 344 |
| 2102 | `2102-12-31` (real dump) | 512 / 455 | 505 / 478 |
| 2103 | `2104-04-28` (proxy — no true 2103 dump) | 549 / 472 | 528 / 469 |
| 2104 | `2104-10-11` (real, season-end) | 559 / 482 | 534 / 485 |

2104 was added once that season finished (Pass 8) — its `MAX(as_of_game_date)` for ratings
matches the stats' own `MAX(as_of_game_date)` exactly, a genuine end-of-season pairing, not a
proxy like 2103 needed. (Team-level `g`/`w`/`l` are never populated for live-ingested seasons,
even finished ones — only the historical-backfill import sets them; `extract_team_runs.py`
defaults `g` to 162 there, justified by 2104's total PA already closely matching the other three
confirmed-162-game seasons.)

**Workstream A** (wOBA→runs): OLS of team-season runs/162 on team wOBA (128 team-seasons/side
as of Pass 8, R²≈0.93), slope rescaled to the per-650-PA player scale the formula consumes,
CONST anchored at league-average wOBA (the same structural zero upstream's constants encode).
The rescaled PSD hitting slope (~547) independently landed within ~1.5% of upstream's 554.8 —
the slope is near-universal; the league-context constant is what needed recalibrating. (First
attempt shipped the raw team-scale slope — ~10x too steep, a real prospect projected at 153
WAR; caught in verification, documented in `fit_runs_per_game.py`'s docstring.) Unaffected by
the split-aware rework below — this regression operates on already-blended team wOBA.

**Workstream B** (per-category tables) — **split-aware, rebuilt same day.** One joint
multivariate OLS per outcome (6 hitting, 4 pitching outcomes; all categories as simultaneous
predictors), fit **separately per side** against real split-specific performance — not a
per-bucket binned mean (would splinter the sample into sparse cells and bake cross-category
correlation into every table), and not the first version's fit-time composite blend either (see
"What changed" below). Linear deliberately (no polynomial/spline): a straight line keeps
constant slope at the edges, so `rating_lookup.interpolate_lookup()`'s edge-slope extrapolation
reproduces the fit exactly. Zero-point at each side's own sample mean (not "50" — PSD's 1-100
ratings aren't centered there), reconciling with the already-calibrated `BASE_*_RATES`.

**Handedness exposure weights — also real data now, not a flat 0.7/0.3.**
`HANDEDNESS_WEIGHTS_NATIVE_HITTING`/`_PITCHING` in `tables_native.py` give each player's real
bats/throws-conditional exposure (`extract_pairs.compute_exposure_weights()`, real
2101-2103 stats joined to each player's `bats`/`throws`), replacing config.py's flat
`HANDEDNESS_WEIGHTS={"R":0.7,"L":0.3}` applied identically to every player. Real platooning is
substantial: a lefty batter faces RHP ~83.7% of the time vs. a righty batter's ~72.7%; a lefty
pitcher faces RHB ~79.6% of the time vs. a righty pitcher's ~57.1% (opposing managers stack the
platoon-advantaged batter side specifically against LHP). `reader.py`'s `add_scouted_ratings()`
now also selects `bats`/`throws` (small, additive change) so `metrics_*_native.py` can look up
the right weight per player.

### What changed from the first version (same day)

The first version of Workstream B fit **one shared table per category**, built from a
fit-time composite regressor (`0.7*R + 0.3*L`) against *aggregate* (not split) outcomes, then
applied that same table to R and L ratings separately at inference. That works, but it assumes
a rating's relationship to outcomes is identical regardless of pitcher handedness — real
split-specific outcome data (`split_id=2`/`3`) says otherwise, and was sitting unused. The
rebuild fits genuinely separate `vsR`/`vsL` tables per category directly against that real
split data. Result: markedly better fit quality (e.g. hitting-vs-RHP R² 0.44–0.68 vs. the old
blended fit's 0.19–0.42) and better real-world predictive power — see Verification below.

## Role/eligibility thresholds (Phase A, done — Pass 9, 2026-08-24)

`PITCH_MINIMUM_RATING`, `POSITION_THRESHOLDS`, `MINIMUM_STARTER_STAMINA` were still calibrated to
the old 20-80 stopgap domain even after Workstream A/B went native. `extract_thresholds.py` refits
all three directly against real PSD outcomes — not a regression like Workstream A/B, but a binary
classification threshold: for each rating, the cutoff maximizing Youden's J (sensitivity +
specificity − 1) for "rating ≥ cutoff" predicting the real label. Real labels: starter/reliever
from `player_pitching_stats_history` (`gs`/`g` ≥ 0.5 share, `g` ≥ 10), primary position from
`player_fielding_stats_history` (most real games played that season, `g` ≥ 20), across all 4
seasons (`extract_pairs.SEASON_RATING_PAIRS`). Writes reviewed literals to `thresholds_native.py`.

| threshold | old (stopgap-domain) | new (native) | Youden's J | n |
|---|---|---|---|---|
| `MINIMUM_STARTER_STAMINA` | 40 | 37 | 0.535 | 1,939 pitcher-seasons |
| `PITCH_MINIMUM_RATING` | 45 | 23 | 0.591 (joint w/ stamina) | 1,939 |
| `POSITION_THRESHOLDS` (C/CF/RF/LF/SS/2B/3B, per-component) | see `config.py` | see `thresholds_native.py` | 0.09–1.000 | 1,562 fielder-seasons |

Catcher components (`Cfram`/`Cabil`/`Carm`) all hit J=1.000 — a real, wide gap between catchers
and everyone else on these components, not a precisely-pinned decision boundary; treat those
three as "clearly separated," not "precisely calibrated." Outfield-arm components generally
scored weakest (J as low as 0.09) — real primary position is driven far more by range than arm,
which the refit surfaces but doesn't fully resolve.

`main_native.py` now computes `pitches`/`pitchesP`/`field`/`sprp`/`sprpP` directly on raw ratings
via `count_pitches_native()`/`can_field_native()`, no stopgap involved — verified end-to-end: 0%
NaN on `field`/`stamina`/`pitches`, no collapse to all-one-class (2,280 rp / 1,472 sp / 14,435
non-pitchers; position eligibility spans all 7 non-corner-infield/outfield keys with a sane mix
of single- and multi-position players).

## Deliberately out of scope

- **Fielding VALUE** (`FIELDING_RUN_VALUES_VS_REPLACEMENT`) — Phase B, not started; no verified
  fielding-outcome dataset yet (see the approved plan's Phase B investigation steps — the
  `framing`-for-pitchers anomaly and `zr`/`eff` units need resolving first). `main_native.py`
  still runs the existing stopgap-based `metrics_fielding.py` for `*_def` only — this is
  independent of the now-native "field" eligibility flag above (which position you're *eligible*
  for vs. how many runs your defense there is *worth* are different questions).
- **Cutover** — the live pipeline is untouched; promoting these tables into `config.py` (and
  deleting the stopgap) is a separate, deliberate decision once Phase B is resolved and the
  side-by-side comparison has been reviewed.

## Verification results (2026-08-22, local copy of live DB, 4-season split-aware version)

- Full native run: 18,187 players, 0% NaN, `wOBA` 0.155…0.466, `war_pitching` −7.2…4.2 — sane
  ranges, no ceiling clustering (the exact Pass-5 bug class, rechecked).
- **Genuine held-out generalization** (`fit_tables.py`'s leave-one-season-out CV, 2104 fold —
  the model fit *without* 2104, evaluated against 2104 it never saw): strong on the
  highest-signal categories, e.g. hitting-R `k_pct` Pearson 0.827, `hr_pct` 0.654, `bb_pct`
  0.691; pitching-R `k_vs` 0.701. This is the honest predictive-power number — 2104 is now part
  of the *final* model's training data, so a same-model-same-data comparison against 2104 would
  be in-sample, not a real test.
- In-sample check against real 2104 outcomes (aggregate wOBA/pwOBA, real R+L splits
  recombined) still shows native clearly ahead of the production stopgap: hitting Spearman
  **0.616 vs. 0.496**; pitching **0.454 vs. 0.359** — consistent with, not a substitute for,
  the held-out CV number above.
- `calculator.html`'s JS port re-verified for exact numeric parity against the real Python
  functions post-rebuild, including the bats-conditional blend.

### Prior verification (2026-07-30, 3-season version, kept for history)

- Against real 2103 outcomes, split-aware beat the first (non-split-aware) version's own
  real-outcome check: hitting Spearman 0.635 vs. 0.563; pitching 0.432 vs. 0.364 — confirming
  the split-aware rebuild was a genuine improvement, not just a different number.
