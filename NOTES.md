# Pistachio @ PSD — deployment notes

Working log of what's been done to get [squirrelplays/pistachio](https://github.com/squirrelplays/pistachio)
running alongside [psd-ootp](https://github.com/wils98/psd-ootp) on CT 106, what's still not
functional, and what's actually required to calibrate the projection model to the PSD league.
Written after the initial deploy pass — not a design doc, a status snapshot.

## What we did

**Forked and adapted the code** (this repo, `wils98/pistachio`):
- `config.py`: `filepath`/`export_filepath` now read from `PISTACHIO_INPUT_DIR` /
  `PISTACHIO_OUTPUT_DIR` (env or `.env`) instead of hardcoded macOS paths.
  `pistachio_filepath` is derived automatically (`Path(__file__).parent`) instead of needing
  to be set by hand. `ID`/`team_managed` are **still the upstream placeholder values**
  (`3332` / `'CHC'`) — see Known Limitations.
- `main.py`: added `check_input_ready()` — refuses to run (clear error, exit 1) if any of the
  four required CSVs are missing from `PISTACHIO_INPUT_DIR`, or older than
  `PISTACHIO_MAX_INPUT_AGE_DAYS` (default 3). Verified both failure modes actually fire.
- `exporter.py`: creates the output directory if missing (previously crashed).
- `serve.py` (new): minimal stdlib `http.server`-based static file server for the generated
  HTML pages, reading host/port from `PISTACHIO_SERVE_HOST`/`PISTACHIO_SERVE_PORT`.
- `pyproject.toml` (new): `uv`-managed dependencies (numpy, pandas, jinja2), `environment.yml`
  / conda removed.
- The regression model itself — every table in `config.py` (`BASE_HITTING_RATES`,
  `BATTING_COMPONENTS_ADJUST_MAP`, `PITCHING_COMPONENTS_ADJUST_MAP`,
  `FIELDING_RUN_VALUES_VS_REPLACEMENT`, wOBA weights, `RUNS_PER_GAME_*` regression constants,
  `RUNS_PER_WIN`) and all the math in `metrics_*.py` — is **untouched from upstream**. None of
  this reflects the PSD league yet.

**Deployed to `psd-ootp` (CT 106)**, mirroring `/opt/psd-ootp`'s conventions:
- `/opt/pistachio` — code, owned `wils:ootp` (group-readable, same pattern as `/opt/psd-ootp`).
- `/data-pistachio/{input,output}` — owned `ootp:ootp`, mode `770` (group-writable, so `wils`
  can also drop CSVs into `input/` via the existing SSH deploy key). Plain directories on the
  container's own root filesystem, **not** a dedicated Proxmox-level bind mount like `/data`/
  `/data-backup` — see Known Limitations.
- Three systemd units in `deploy/`: `pistachio-run.service` (oneshot, runs `main.py`),
  `pistachio-run.timer` (was `OnCalendar=*-*-* 07:00:00`, **currently disabled** — see Current
  State), `pistachio-serve.service` (`Type=simple`, runs `serve.py`, `Restart=on-failure`).
- `.env` on the box sets the input/output dirs, `PISTACHIO_MAX_INPUT_AGE_DAYS=3`,
  `PISTACHIO_SERVE_HOST=100.103.31.93` (the box's Tailscale IP), `PISTACHIO_SERVE_PORT=8100`.

**Verified end-to-end** with synthetic sample CSVs (2 fake players, not real league data):
- `uv sync` installs cleanly on the box.
- `main.py` runs the full pipeline (load → ratings → metrics → WAR → export) without error and
  writes all three HTML pages.
- Missing-input and stale-input guards both correctly abort with a clear message and exit 1.
- `pistachio-serve.service` is reachable at `http://100.103.31.93:8100/` from another
  Tailscale-connected machine (confirmed both by curl and by the user browsing it directly).

## Current state (as of this writing)

- `pistachio-serve.service`: **running**, serving sample/placeholder data.
- `pistachio-run.timer`: **disabled** — nothing runs automatically. Deliberate: not ready for
  unattended runs yet (real CSV transfer mechanism doesn't exist, model isn't calibrated,
  `ID`/`team_managed` aren't set for real). Trigger a run manually any time with:
  ```bash
  cd /opt/pistachio && .venv/bin/python main.py
  ```
- `/data-pistachio/input/` currently holds the synthetic sample CSVs (kept intentionally, per
  request, rather than cleared) — **not real PSD league data**.

## Known limitations / what won't work yet

- **No real CSV transfer mechanism exists.** `check_input_ready()` assumes *something* keeps
  `/data-pistachio/input/` fresh from whatever machine runs OOTP. That something hasn't been
  built. Turning the timer back on today would just fail daily with a stale/missing-input
  error once the sample data ages past 3 days.
- **`ID`/`team_managed` are still upstream's values** (`3332` / `'CHC'`), not the real PSD
  scout `coach_id` or the user's actual team. Any run against real data will silently filter
  ratings by the wrong scout/team until these are corrected in `config.py`.
- **The projection model is not calibrated to PSD.** It's fit (regression tables, base rates,
  wOBA weights, runs-per-win) to the upstream author's own league and game-engine settings.
  Numbers will render and look plausible but are not trustworthy for PSD until recalibrated —
  see below.
- **Inherited correctness bug, not yet fixed**: in `metrics_pitching.py`'s rate-adjustment
  logic, a missing (`NaN`) rating is treated identically to a rating *worse than the bottom of
  the table* (a 10x-amplified penalty), rather than defaulting to league-average like the
  hitting side does. Unscouted/lightly-scouted pitchers will likely get implausibly bad
  projections. Not addressed in this pass — flagged during the original review, still open.
- **`/data-pistachio` has no independent backup.** Unlike `/data`/`/data-backup`, this is a
  plain directory on the container's rootfs, not a separate Proxmox bind mount — a deliberate
  simplification for this trial pass. Low stakes today (everything in it is either regenerable
  from OOTP or re-exportable), but worth revisiting before this holds anything that isn't
  trivially reproducible.
- **No independent sudoers scope for pistachio's own units.** Restarting/reinstalling
  `pistachio-*` services always needs an interactive password — never wired into the
  passwordless scope the way `psd-ootp-web`/`psd-ootp-ingest` restarts are.
- **Only tested at toy scale** (2 synthetic players). Real behavior — runtime, memory, output
  file size, row-wise `.apply()` performance in `metrics_*.py` — against a full league + minors
  player pool (likely thousands of rows) is unverified.
- **No tests.** Neither upstream nor this fork has any automated tests for the projection math
  or the pipeline.

## What's needed to calibrate the model to the PSD league

This is the substantial remaining work — everything above is plumbing, this is the actual
projection accuracy.

1. **Verify real field names before writing anything.** The local OOTP-client CSV export
   (`players_scouted_ratings.csv` etc., what *pistachio* reads) is a different data path than
   psd-ootp's own StatsPlus API scrape — its field shape hasn't been checked against a real
   payload yet. In particular: does it expose handedness-split ratings (vs LHP/RHP power, eye,
   contact, control, stuff, etc.)? psd-ootp's own ingested (OSA-sourced, API) ratings do **not**
   capture any such split — confirmed by grep, nothing exists there, not even unparsed. Whether
   the local CSV export differs is unknown and needs checking against one real export before
   assuming pistachio's R/L-split model (`adjust_rates(row, side)` in `metrics_hitting.py` /
   `metrics_pitching.py`) can work as-is for PSD.
2. **Set `ID` and `team_managed` in `config.py`** to the real PSD scout `coach_id` and team
   abbreviation.
3. **Recalibrate the directly-portable constants** to PSD's actual league context:
   `LEAGUE_WOBA`, `LEAGUE_RUNS_PER_PA`, `BASE_HITTING_RATES`, `BASE_PITCHING_RATES`,
   `RUNS_PER_WIN` (scales with scoring environment). These just need PSD's real league-average
   numbers plugged in — no regression required, low effort once the data's in hand.
4. **Refit the deep tables** — `BATTING_COMPONENTS_ADJUST_MAP`, `PITCHING_COMPONENTS_ADJUST_MAP`,
   `FIELDING_RUN_VALUES_VS_REPLACEMENT`, and the `RUNS_PER_GAME_*_COEFF`/`_CONST` regression.
   These are fit coefficients specific to how *this league's* OOTP engine settings turn a
   20-80 rating into actual outcomes — swapping in different numbers without redoing the
   regression would produce confidently-wrong output. Requires:
   - A large sample of PSD players with both a historical ratings snapshot **and** enough
     subsequent real performance (PA/IP) for the outcome side to be meaningful.
   - psd-ootp's own DB already has the right *shape* for this (point-in-time rating snapshots
     joinable to career stat history — see `player_ratings_history` /
     `player_batting_stats_history` / `player_pitching_stats_history`), but as of this writing
     only ~3 snapshot dates exist, spanning about 3 in-game weeks. Needs a full season or more
     of accumulated history before there's enough spread and sample size to regress against.
   - Some fields the model needs (vsL/vsR power/eye/contact/gap/babip, pBABIP, HRA-vs-split)
     aren't first-class columns in psd-ootp's ingest yet — they don't exist there at all for
     OSA-sourced ratings (see point 1). If PSD's local CSV export does carry these, that's a
     separate ingest path than psd-ootp's API-based one and would need its own handling.
5. **Fielding "potential" needs a different treatment than hit/pitch.** Per league-specific
   clarification: OOTP's "potential position rating" isn't an independently scouted ceiling —
   it's a formula derived from current fielding ratings plus accumulated playing time at that
   position. Pistachio's hit/pitch model treats "potential" as a separate scouted talent tier
   (`*P` columns); fielding doesn't have an equivalent, so the model shouldn't invent one.
   Open design question: skip a forward-looking defensive projection entirely (use current
   fielding ratings for both "now" and "future" views), or attempt to approximate the game's
   own experience-based growth formula. Not decided yet.
6. **Build the actual CSV transfer mechanism** from wherever OOTP runs into
   `/data-pistachio/input/` on the box — the piece `check_input_ready()` assumes exists but
   doesn't yet. Whatever form this takes (scp, rsync, something else) is what would let the
   timer be turned back on safely.
