# Pistachio (PSD fork)

**Pistachio** is a projection system for the computer game *Out of the Park Baseball 2026 (OOTP 26)*.

This is a fork of [squirrelplays/pistachio](https://github.com/squirrelplays/pistachio),
customized for the PSD league and deployed alongside
[psd-ootp](https://github.com/wils98/psd-ootp) on the same server (see that repo's
`deploy/PROVISIONING.md` for the box's overall layout). Kept as a separate app —
own directory, own venv, own systemd units — not merged into psd-ootp's own codebase.
The projection model itself (the regression tables in `config.py`, the wOBA/WAR math in
`metrics_*.py`) is unchanged from upstream so far; recalibrating it for the PSD league's
OSA-only ratings environment is tracked as separate, later work.

### What changed from upstream

- `config.py`: `filepath`/`export_filepath` are now read from `PISTACHIO_INPUT_DIR` /
  `PISTACHIO_OUTPUT_DIR` (env or `.env`) instead of hardcoded local paths — same code runs
  for local dev and the deployed timer. `pistachio_filepath` is now derived automatically
  (the directory this file lives in) rather than needing to be set by hand.
- `main.py`: refuses to run (clear error, non-zero exit) if the required input CSVs are
  missing or older than `PISTACHIO_MAX_INPUT_AGE_DAYS` (default 3) — matters once this runs
  unattended on a timer against CSVs shipped from another machine, so a stalled transfer
  fails loudly instead of silently re-serving stale projections.
- `exporter.py`: creates the output directory if it doesn't exist yet, instead of erroring.
- `serve.py` (new): a minimal static file server for the generated HTML pages, deployed via
  `deploy/pistachio-serve.service` — separate from `main.py`'s batch run.
- Dependency management: `pyproject.toml` + `uv` instead of conda/`environment.yml`.

### Deployment data contract

`PISTACHIO_INPUT_DIR` must contain, refreshed regularly from whatever machine actually runs
OOTP:

- `players.csv`
- `players_scouted_ratings.csv`
- `players_career_batting_stats.csv`
- `players_career_pitching_stats.csv`

How those get there (scp, rsync, etc.) is outside this repo's concern — `main.py`'s
freshness check just assumes something is keeping them current.

## What It Projects

- **Position players**:  
  - wOBA  
  - WAR (by position)

- **Pitchers**:  
  - "Pitching wOBA"  
  - WAR (for starters and relievers)

## Output

When run successfully, the system generates five HTML pages of projections:

- `pitchers.html`: Pitchers and pitching prospects  
- `hitters.html`: Hitters  
- `hit_prospects.html`: Hitter prospects
- `draft_h.html` / `draft_p.html`: Draft-pool prospects, hitting and pitching potential
  respectively (potential-only columns — draft-pool players aren't signed to an org yet, so
  there's no "current" performance to project). Includes an `avail` column (like the existing
  `flag` column) marking players not yet selected in this year's draft.

## Based on OOTP 26

These projections are built from the ground up for **OOTP 26**.  While not perfect, the testing method is intended to be rigorous.

The underlying testing data and methodology is set out in detail in this Google Sheet:
https://docs.google.com/spreadsheets/d/19f0pZUqyonjDa2AwHckd8Al9H-wmBC6nvM-Y0RzzhSs/edit?gid=202842399#gid=202842399

---

## Configuration Instructions

Set `PISTACHIO_INPUT_DIR` / `PISTACHIO_OUTPUT_DIR` via environment or `.env` (see above).
You still need to update `config.py` directly for these league-specific values:

- `ID = 3332`: Your scout’s `coach_id` from `coaches.csv`  
- `team_managed = 'CHC'`: Your in-game team abbreviation

⚠️ You **must** update these before running `main.py`, or it won’t work.

### Other optional Config Settings

- `club_lookup`: Maps team numbers to abbreviations (default set to MLB)
- `POSITION_THRESHOLDS`: Minimum fielding ratings by position
- Pitcher thresholds: Defines starter vs reliever status (default setting is that a starter has at least 3 pitches rated 45 or above and stamina at 40 or above; a reliever has at least two pitches rated 45 or above, with no stamina condition)

ℹ️ The code expects the game to output ratings on the **20–80 scale** in increments of **5**. It won't work well on other settings.

---

## Additional Info

- **Player IDs** saved in `flagged.txt` can be found in outputs by:
  - Typing `flag` in the search bar
  - Using the 'Custom Search Builder' in the HTML to search for 'flag equals flag'

This is useful for tracking:
- Draft prospects
- Free agents
- Waiver wire
- Any other custom shortlists created in-game

---

## Extras

- Examples of the html outputs are included in the `outputs` folder  
  (Note: these will be overwritten once you successfully run the code in main.py with your own stuff based on your OOTP save)

- Feedback and pull requests welcome

🧵 [OOTP Forum Post (by "Squirrel")](https://forums.ootpdevelopments.com/showthread.php?t=361580)