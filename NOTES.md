# Pistachio @ PSD — deployment notes

Working log of what's been done to get [squirrelplays/pistachio](https://github.com/squirrelplays/pistachio)
running alongside [psd-ootp](https://github.com/wils98/psd-ootp) on CT 106, what's still not
functional, and what's actually required to calibrate the projection model to the PSD league.
Written after the initial deploy pass, then updated after switching the data source to
psd-ootp's DB — not a design doc, a status snapshot.

## What we did

**Pass 1 — deploy the fork, CSV-based (superseded, see Pass 2):**
- `config.py`/`main.py` adapted for env-driven local-CSV paths, `serve.py` added,
  `pyproject.toml` replacing conda. Deployed to `/opt/pistachio` on CT 106, mirroring
  `/opt/psd-ootp`'s ownership conventions. Verified end-to-end with synthetic sample data.
- This whole approach assumed a CSV transfer mechanism from wherever OOTP runs — never built,
  and now unnecessary (see Pass 2).

**Pass 2 — switch the data source to psd-ootp's own DB:**

No new CSV pipeline needed after all — psd-ootp already ingests everything pistachio needs
(player bio, scouted ratings, career stats) via its own live StatsPlus scrape. This also
sidesteps the OOTP26→27 version question entirely: the DB always reflects whatever the league
is *currently* running, since it's continuously re-ingested.

- `config.py`: `filepath`/`PISTACHIO_INPUT_DIR` and the CSV-column-allowlist/rename-map
  constants (`PLAYERS_COLUMNS`, `SCOUTED_RATINGS_RENAMES`, etc.) removed entirely. Added
  `DB_PATH` (env `PISTACHIO_DB_PATH`, defaults to `/data/ootp.db`). `PITCH_RATING_COLUMNS`/
  `POTENTIAL_PITCH_RATING_COLUMNS` now list psd-ootp's own short field names (`fst`, `snk`, …)
  instead of old verbose CSV headers. `ID` (per-scout `coach_id` filter) removed outright —
  this league is OSA-sourced (one shared rating stream for every team, confirmed live:
  `player_ratings_history.source` is `'osa'` for all 17,807 rated players, 0 `'scout'` rows),
  so there's no scout to filter by. `club_lookup` (the old numeric-org→abbreviation dict)
  removed — psd-ootp's own `team_id` numbering doesn't match OOTP's original numbering pistachio
  assumed (verified: psd-ootp's `team_id`s start at 31, not 1-30), so `load_players()` now joins
  the DB's own `team` table directly instead.
- `reader.py`: fully rewritten to query the DB read-only
  (`file:{DB_PATH}?mode=ro&immutable=1`, mirrors psd-ootp's own `/api/sql` safety pattern)
  instead of `pd.read_csv`. `load_players()` joins `player`+`team`. `add_scouted_ratings()`
  pulls each player's most recent `source='osa'` snapshot and unpacks `extra_json` (handedness
  splits + potential-babip/hra/pbabip), `defense_json` (fielding grades), and `pitches_json`
  (per-pitch-type grades) in Python — psd-ootp's schema/parser were **not** touched; all the
  "elevation" happens on pistachio's side at read time. `add_hitting_career_stats()`/
  `add_pitching_career_stats()` query `player_batting_stats_history`/
  `player_pitching_stats_history` (`level_id=1, split_id=1`, most recent `season_year`,
  summed); pitching converts `outs → IP` (`/3`), a conversion the old CSV never needed.
  `count_pitches()`/`can_field()`/`is_flagged()` untouched — pure pandas ops over
  already-correctly-named columns.
- `main.py`: `check_input_ready()` (file-mtime based) replaced with `check_data_ready()` — DB
  connectivity check + staleness check against `raw_payload.fetched_at_utc` (the real
  wall-clock fetch time; `player_ratings_history.as_of_game_date` is OOTP's in-game calendar,
  not elapsed real time, so it can't be used for this). Same "fail loudly rather than silently
  serve stale projections" intent as before, correct signal this time.
- **Verified for real**, not just against synthetic data: ran the new `reader.py` against a
  local copy of the live DB (109 MB, safe to copy — read-only access either way). All 17,807
  OSA-rated players load correctly, org names resolve via the real `team` table, ratings/
  defense/pitch columns populate with 0% null rate. Cross-checked player 26 (Juan Ramos) by
  hand against psd-ootp's own `/api/players/26` endpoint — every value (`pow_r=9`, `pow_l=2`,
  `ks_r=36`, `ctrl_r=41`, etc.) matches exactly.

### Correction to an earlier claim in this log

Pass 1 (and the conversation leading to it) said psd-ootp's ratings ingest does **not** capture
handedness-split ratings at all — that was wrong. A direct query against the *live* DB (not
just grepping the ingest source code) shows they're real and present, just sitting in
`extra_json` rather than promoted to columns. The earlier "confirmed by grep, nothing exists"
conclusion missed that psd-ootp's generic unmapped-header fallback puts unrecognized fields
into `extra_json` without ever naming them in the parser source — a code grep for field names
can't see that; only checking actual data can. Lesson: for "does the data support X," check the
data, not the code that produced it.

## Important finding: ratings are on a 1-100 scale, not 20-80

Checked across ~2000 real players before wiring anything further: `power` ranges 1–116 (avg
10.2), `control` 0–96 (avg 8.5), `stamina` 1–103 (avg 24.6) — same story for every
`extra_json` split field. This is **by league design**, not a bug or a missing conversion —
per the PSD rule book: individual tool ratings (actual and potential) are configured to a
native 1-100 scale; only the aggregate `Overall`/`Potential` grade is 20-80. Pistachio's model
uses individual tool ratings exclusively (never the aggregate grade), so there is no
"convert 1-100 to 20-80" step to write — raw values pass through as-is.

**But the model itself cannot run against this data yet.** Confirmed directly: `main.py`
crashes with `KeyError: '41'` inside `metrics_pitching.py`'s `adjust_rates()` — every
regression table in `config.py` is keyed to exact string buckets at multiples of 5 (`"35",
"40", "45", ...`), a safe assumption when input is always a real 20-80 scouting grade, but
false for 1-100 raw data landing on every integer. This also surfaced a pre-existing
inconsistency upstream never hit: `metrics_hitting.py`'s equivalent code clamps/rounds
defensively (`table.get(str(clamped), {})`, degrades to `{}` on a miss), while
`metrics_pitching.py`'s does a bare dict index (`table[str_value]`, crashes on a miss) —
never exercised before because pistachio's original CSV data only ever contained exact
multiples of 5.

This means recalibrating the model isn't just "get more accurate numbers for PSD" — it's a
prerequisite for the pipeline to produce *any* output at all against real PSD data. Not
attempted in this pass (explicitly out of scope for "wire up the data source" — see plan).

**Pass 3 — unblock the crash (stopgap) + recalibrate the portable constants (real), in parallel:**

Two independent workstreams, run together deliberately (see the approved plan for the reasoning
on why they don't interact — base-rate changes are purely additive and can't reorder players,
so neither blocks or invalidates the other).

- **Workstream A (interim stopgap, NOT real calibration)** — `reader.py` gained
  `apply_native_scale_stopgap()`, called in `main.py` right after `add_scouted_ratings()`.
  Rescales every rating column feeding a table lookup or threshold (hit/pitch tools + splits +
  potential, fielding grades, pitch-type grades, stamina, speed) to its league-wide percentile,
  mapped onto 20-80 and rounded to the nearest 5 — uniform target domain across all columns so
  `POSITION_THRESHOLDS`/`PITCH_MINIMUM_RATING` (bare thresholds outside any table) stay
  meaningful without their own edits. This alone fixes the crash: every value now lands on an
  exact table key. `metrics_pitching.py` also gained a shared `_lookup_adjustment()` helper
  (replacing duplicated logic in both `calc_pitching_metrics()` and
  `calc_potential_pitching_metrics()`) that fixes two bugs at the same time: NaN now defaults to
  the floor bucket (matching `metrics_hitting.py`'s pattern) instead of the amplified
  below-floor penalty, and `Stamina`'s previously-silent skip-on-miss now clamps like every
  other category. **Still not real calibration** — the regression coefficients themselves are
  still upstream's own league's values, just being fed rescaled input instead of crashing on it.
- **Workstream B (real calibration)** — new `tools/calibrate_league_constants.py`, a human-run
  script (not wired into the live pipeline — these constants should stay reviewed literals, not
  silently drift as more games get played). Computed `BASE_HITTING_RATES`, `BASE_PITCHING_RATES`,
  `LEAGUE_WOBA`, `LEAGUE_RUNS_PER_PA` from real PSD 2104-season stats — **see Pass 4 below: the
  first version of this used the wrong tables and was off by >2x, caught and fixed same day.**
  `RUNS_PER_WIN` needed a different source entirely: `team_batting_stats_history.g` (games)
  looked unreliably populated this early in the season, so the script instead takes
  `--runs-per-team-per-game` from OOTP's own in-game league history report (user-supplied: 5.16
  for 2104) and applies Tango's commonly-cited `RPW = 1.5*RPG + 3` — **10.74**, transcribed into
  `config.py`.
- **Verified**: Workstream A alone — full run, 17,807 players, 0% NaN on `best`/`war_hitting`/
  `war_pitching`, Spearman(`best` WAR, real `Overall` grade) = 0.604 (p≈0), Spearman(`bestP` WAR,
  real `Potential` grade) = 0.307 (p≈0) — both positive and highly significant, confirming the
  stopgap preserves sensible relative ordering. Combined (both workstreams merged): same
  zero-NaN result, `wOBA` range 0.213–0.576 (8,976 unique values, not clustered), `best` WAR
  range -5.9–17.3 — no walls of identical values, no runaway outliers.

**Pass 4 — found and worked around a real bug in psd-ootp's own ingest (not pistachio's):**

The user spotted Workstream B's PA figure (129,606) didn't match OOTP's own in-game "Team
Statistics" report for the same date (46,583, screenshot-verified). Chasing it down:

- `player_batting_stats_history`/`player_pitching_stats_history` store the **wrong column as
  `player_id`**. StatsPlus's `playerbatstatsv2`/`playerpitchstatsv2` CSVs have two ID columns —
  `id` (OOTP's own internal per-stat-row surrogate key, per its API docs: *"matches the
  `players_career_batting_stats` table in the same order it's dumped from OOTP"*) and `player_id`
  (the real player) — and psd-ootp's parser appears to store `id`, not `player_id`. Confirmed:
  `player.player_id` ranges 26–92,928 (17,807 real players); the stats tables' `player_id` ranges
  393–701,903, and 98% of its distinct values (1,401/1,424 in one sample) don't exist in `player`
  at all. Directly matched specific bad values (2174, 2901, …) to the raw archived payload's `id`
  column for real players 59/81.
- **Reported to whoever owns psd-ootp's ingest** (separate system, separate fix — not touched
  here). Likely fix location: `src/ingest/parse.py`'s handling of `playerbatstatsv2`/
  `playerpitchstatsv2` (and possibly `playerfieldstatsv2` — same risk, not independently
  checked). Once fixed, `psd-ootp-ingest reparse` can backfill the 3 already-archived snapshots
  from `raw_payload.body` without re-fetching from StatsPlus.
- **Worked around for our purposes**: `team_batting_stats_history`/`team_pitching_stats_history`
  have no such column and are unaffected — verified to match the in-game report exactly (46,583
  total PA, per-team breakdown matched precisely, e.g. Orlando 1,512). `calibrate_league_constants.py`
  now sources from those tables instead (team pitching stats have no `bf` column — since
  league-wide `sum(batters faced) == sum(plate appearances)` exactly, hitting's PA is used as the
  stand-in denominator). Final corrected constants, all now matching the real 46,583-PA sample:
  `hr_pct_baserate=0.0284`, `k_pct_baserate=0.1729`, `bb_pct_baserate=0.0796`,
  `1b_pct_baserate=0.1799`, `2b_pct_baserate=0.0418`, `3b_pct_baserate=0.0027`,
  `LEAGUE_RUNS_PER_PA=0.1293`, `LEAGUE_WOBA=0.3307` (cross-checked 0.3319 the independent way).
  `RUNS_PER_WIN=10.74` unaffected (never touched the buggy tables).
- **`reader.py`'s per-player `pa`/`ip` columns are still built from the buggy tables** — they
  were already fixed this pass to dedupe by each player's latest snapshot (a real, separate,
  necessary fix — snapshots are cumulative-to-date, not deltas, so summing across all 3 dates
  double/triple-counted even before this bug was found), but that fix can't repair data sourced
  from the wrong `player_id` in the first place. Left as-is deliberately: once psd-ootp's parser
  is fixed and reparsed, these columns should self-correct with no further pistachio changes
  needed, since the dedup logic itself is already correct.

## Current state (as of this writing)

- `pistachio-serve.service`: **running** on the box, still serving Pass-1 sample/placeholder
  data — hasn't been re-run against Pass 2/3's changes yet.
- `pistachio-run.timer`: **disabled**, unchanged from Pass 1.
- `/data-pistachio/{input,output}` and the old `PISTACHIO_INPUT_DIR`/
  `PISTACHIO_MAX_INPUT_AGE_DAYS` env vars are now **dead weight** — no longer read by any code
  path. Harmless to leave in place; optional cleanup, not done automatically.
- Box's `.env` needs the Pass 2 update (`PISTACHIO_DB_PATH`/`PISTACHIO_MAX_DATA_AGE_DAYS`)
  before `main.py` will run there — see deploy steps.

## Known limitations / what won't work yet

- **The regression tables are still upstream's own league's coefficients.** Workstream A makes
  them *runnable* against PSD data (correct relative ordering), not *accurate* — absolute
  numbers stay untrustworthy until the tables are rebuilt natively for the 1-100 domain (see
  below). This is the real remaining gap, not a data-plumbing one anymore.
- **`pa`/`ip` display columns are unreliable** until psd-ootp's ingest parser bug (Pass 4) is
  fixed and reparsed — they're sourced from `player_batting_stats_history`/
  `player_pitching_stats_history`, which currently store the wrong `player_id`. Doesn't affect
  any projection math (pistachio's model is entirely rating-driven, these are display-only), but
  the numbers shown in the exported tables for current-season PA/IP are not trustworthy today.
- **`team_managed` is still `'CHC'`** (upstream's placeholder) — inert either way, isn't
  referenced anywhere in the codebase outside its own definition.
- **`/data-pistachio` has no independent backup** — plain rootfs directory, not a Proxmox bind
  mount. Moot for input (nothing reads it), still relevant for output if that ever holds
  something not trivially regenerable.
- **No independent sudoers scope** for `pistachio-*` units — restarts/reinstalls always need an
  interactive password.
- **No tests.**

## What's needed to calibrate the model to the PSD league

1. ~~Verify real field names before writing anything~~ — **done.** Surfaced the 1-100 scale
   finding, the more load-bearing discovery.
2. ~~Recalibrate the directly-portable constants~~ — **done, Pass 3 Workstream B.**
3. ~~Unblock the crash so the pipeline can run at all~~ — **done (interim stopgap), Pass 3
   Workstream A.** Not the same as real calibration — see Known Limitations.
4. **Rebuild the regression tables natively for the 1-100 domain** — the real fix Workstream A
   stands in for. `BATTING_COMPONENTS_ADJUST_MAP`, `PITCHING_COMPONENTS_ADJUST_MAP`,
   `FIELDING_RUN_VALUES_VS_REPLACEMENT`, `POSITION_THRESHOLDS`, `PITCH_MINIMUM_RATING` all need
   re-keying and refitting to PSD's actual scale and league behavior — not just new coefficients
   at the same bucket keys. Once this lands, Workstream A's `apply_native_scale_stopgap()` call
   in `main.py` should be deleted (it's isolated to one function, one call site, by design).
5. **Refit the `RUNS_PER_GAME_*_COEFF`/`_CONST` regression** and the deep tables from point 4
   against real PSD (ratings snapshot → subsequent performance) pairs. psd-ootp's DB has the
   right shape (point-in-time snapshots joinable to career stat history) but only ~3 snapshot
   dates exist so far, spanning ~3 in-game weeks — needs a full season or more before there's
   enough spread and sample size to regress against.
6. **Fielding "potential" needs different treatment than hit/pitch** — per league clarification,
   OOTP's "potential position rating" is a formula (current fielding + accumulated playing
   time), not an independently scouted ceiling. Pistachio's `*P`-column pattern for hit/pitch
   potential doesn't have a natural fielding equivalent. Open design question, not decided.
