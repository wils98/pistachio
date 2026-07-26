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

## Current state (as of this writing)

- `pistachio-serve.service`: **running** on the box, still serving Pass-1 sample/placeholder
  data — hasn't been re-run against the new DB-backed `reader.py` yet.
- `pistachio-run.timer`: **disabled**, unchanged from Pass 1.
- `/data-pistachio/{input,output}` and the old `PISTACHIO_INPUT_DIR`/
  `PISTACHIO_MAX_INPUT_AGE_DAYS` env vars are now **dead weight** — no longer read by any code
  path. Harmless to leave in place; optional cleanup, not done automatically.
- Box's `.env` still has the Pass-1 variables and needs updating to `PISTACHIO_DB_PATH`/
  `PISTACHIO_MAX_DATA_AGE_DAYS` before `main.py` will run there.

## Known limitations / what won't work yet

- **The model cannot produce output against real PSD data** — see the 1-100/20-80 finding
  above. This is the actual blocker now, not data plumbing.
- **`team_managed` is still `'CHC'`** (upstream's placeholder) — though it turns out this
  constant isn't referenced anywhere in the codebase at all (grepped: only appears in its own
  `config.py` definition), so it's currently inert either way.
- **Inherited correctness bug, not yet fixed**: the same `metrics_pitching.py` NaN-handling
  issue flagged in the original review — a missing rating is treated as *worse than the bottom
  of the table* (10x-amplified penalty) rather than defaulting to average. Still open.
- **`/data-pistachio` has no independent backup** — plain rootfs directory, not a Proxmox bind
  mount. Now moot for input (nothing reads it), still relevant for output if that ever holds
  something not trivially regenerable.
- **No independent sudoers scope** for `pistachio-*` units — restarts/reinstalls always need an
  interactive password.
- **Only tested against a full real player pool for the reader layer**, not the full metrics
  pipeline (blocked by the scale issue above) — so actual runtime/memory behavior of
  `metrics_*.py`'s row-wise `.apply()` calls against ~18k rows is still unverified.
- **No tests.**

## What's needed to calibrate the model to the PSD league

1. ~~Verify real field names before writing anything~~ — **done, see above.** Also surfaced the
   1-100 scale finding, which turned out to be the more load-bearing discovery.
2. **Rebuild the regression tables against a 1-100 domain**, not just retune their values.
   `BATTING_COMPONENTS_ADJUST_MAP`, `PITCHING_COMPONENTS_ADJUST_MAP`,
   `FIELDING_RUN_VALUES_VS_REPLACEMENT`, `POSITION_THRESHOLDS`, `PITCH_MINIMUM_RATING` all need
   re-keying to the actual scale PSD ratings come in on — this is a bigger structural change
   than "swap in new coefficients at the same bucket keys," which was the original assumption.
3. **Recalibrate the directly-portable constants**: `LEAGUE_WOBA`, `LEAGUE_RUNS_PER_PA`,
   `BASE_HITTING_RATES`, `BASE_PITCHING_RATES`, `RUNS_PER_WIN` — PSD's real league-average
   numbers, no regression required.
4. **Refit the `RUNS_PER_GAME_*_COEFF`/`_CONST` regression** and the deep tables from point 2
   against real PSD (ratings snapshot → subsequent performance) pairs. psd-ootp's DB has the
   right shape for this (point-in-time snapshots joinable to career stat history) but only ~3
   snapshot dates exist so far, spanning ~3 in-game weeks — needs a full season or more before
   there's enough spread and sample size to regress against.
5. **Fielding "potential" needs different treatment than hit/pitch** — per league clarification,
   OOTP's "potential position rating" is a formula (current fielding + accumulated playing
   time), not an independently scouted ceiling. Pistachio's `*P`-column pattern for hit/pitch
   potential doesn't have a natural fielding equivalent. Open design question, not decided.
