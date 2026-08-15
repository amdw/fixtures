# Fixtures

## Introduction

This repo generates Middlesex League fixture schedules using an OR-Tools
constraint solver.

## Setup

Install Python (tested on 3.14) and Pipenv.

From the root of the repo:

```bash
pipenv install --dev
pipenv shell
```

Alternatively, prefix individual commands with `pipenv run` (e.g. `pipenv run
python run_tests.py`) instead of entering a `pipenv shell` session — useful
in non-interactive contexts, since the venv already exists once
`pipenv install --dev` has been run.

## Generating fixtures

Describe the clubs, teams, divisions and constraints in a YAML specification
file, placed inside the run folder you want its output written to (e.g.
`runs/2025-26-season/spec.yaml`), then run the solver against it:

```bash
python run.py runs/2025-26-season/spec.yaml runs/2025-26-season
```

This solves the fixtures, and writes the HTML report alongside the spec in
`runs/2025-26-season/`. Re-running it overwrites the previous report in place,
so it's safe to rerun after editing the spec.

### Spec file format

```yaml
name: "2025-26 Season"           # optional, defaults to "" (no subtitle shown)
draft: false                    # optional, defaults shown here
min_gap_days: 7                 # optional, defaults shown here
latest_internal_match_date: 2025-12-31  # optional, no default (no cutoff applied)

clubs:
  albany:                       # club ID: stable, referenced from teams/club_constraints/etc.
    name: Albany
    home_venue: Albany Sports Hall
    home_start_time: "19:30"
    home_time_limit: "75+15"      # chess time control: 75 min + 15 sec/move
  hackney:
    name: Hackney
    home_venue: Hackney Community Centre
    home_start_time: "19:00"
    home_time_limit: "60+15"

teams:
  albany-1:                     # team ID: stable, referenced from divisions
    club: albany
    index: 1
    division: 1
  hackney-1:
    club: hackney
    index: 1
    division: 1
  hackney-5:
    club: hackney
    index: 5
    division: 3
    name_override: "Hackney Herons"  # optional; used everywhere instead of "Hackney 5"

divisions:
  1: [albany-1, hackney-1]
  3: [hackney-5]

club_constraints:
  defaults:                       # optional; spec-wide defaults, overridable per club
    max_concurrent_home_matches: 2  # applies to any club not given its own entry below

  albany:
    home_dates: [2025-09-01, 2025-09-15, 2025-09-29]
    unavailable_away_dates: [2025-12-25]
    max_concurrent_home_matches: 3  # shorthand for {default: 3}

  hackney:
    home_dates: [2025-09-08, 2025-09-22]
    max_concurrent_home_matches:
      default: 2
      overrides:                  # per-date overrides of that club's default
        2025-09-08: 3
    max_home_dates_used: 1        # optional; caps how many of these home dates get used

fixed_fixtures:                   # optional; pin specific fixtures to a specific date
  - home: albany-1
    away: hackney-1
    date: 2025-09-01

exclude_fixtures:                 # optional; withhold fixtures from scheduling entirely
  fixtures:                       # individual home/away pairs, to arrange later
    - home: hackney-1
      away: albany-1
```

`club_constraints` groups every constraint that can vary by club — currently
`home_dates`, `unavailable_away_dates`, `max_concurrent_home_matches` and
`max_home_dates_used` — under that club's own ID, alongside an optional
`defaults` entry (a sibling of the club entries, not itself a club) for
constraint types that support a spec-wide default overridable per club.
Today that's just `max_concurrent_home_matches`; if
`club_constraints.defaults.max_concurrent_home_matches` is omitted, every
club must have its own `max_concurrent_home_matches` entry — there's no
built-in fallback value. `home_dates` and `unavailable_away_dates` default to
empty if a club's entry omits them (or the club has no entry at all).

Club and team IDs are your own stable keys (letters/digits/hyphens are safest,
since they're also used to build report filenames) — used to cross-reference
clubs from teams, teams from `divisions`, and clubs from `club_constraints`.
Every team's `division` must match the division list it's listed under in
`divisions`, and every team must appear in exactly one division list. Dates
are plain ISO8601 (`yyyy-mm-dd`), quoted or unquoted. Per-team constraint
overrides (as opposed to per-club) are planned but not yet supported. New
constraint types can be added to `fixturespec.py` and `fmodel.Parameters` as
they're needed.

`fixed_fixtures` pins specific fixtures (by home/away team ID) to a
specific date, forcing them into the solved schedule as given rather than
letting the solver choose. For each entry, `home` and `away` must be teams
in the same division, and `date` must be one of the home team's club's
`home_dates`.

`exclude_fixtures` withholds fixtures from scheduling entirely — e.g. to
arrange in a later run, once dates are confirmed — rather than pinning
them. Give whole club IDs under `clubs` or whole team IDs under `teams`
(each excludes every fixture that club's/team's teams would otherwise play
within their division, in both directions), and/or individual home/away
pairs under `fixtures`. All three are optional and can be combined. A
fixture can't be both fixed and excluded; excluded fixtures still appear in
the HTML report, at the bottom of every relevant table, with "TBC" in
place of a date.

`latest_internal_match_date`, if set, is the latest date allowed for a
fixture between two teams of the same club (e.g. Hendon 1 v Hendon 2) —
useful for requiring "derby" matches to be settled earlier in the season
than the rest of the fixture list. It has no effect on fixtures between
teams from different clubs. If omitted, no such cutoff is applied. A
`fixed_fixtures` entry between two teams of the same club must not be
dated after it.

### HTML report

Each run's folder contains:

- `all-matches.html` — every fixture (date, division, home, away, venue,
  start time, time limit)
- `division-<n>.html` — one page per division (as above, minus division)
- `club-<id>.html` — one page per club, with a consolidated table of all
  the club's matches followed by one table per team
- `index.html` — links to all of the above (fully derived from the files
  above; see below, it doesn't need to be committed or hand-maintained)

Venue, start time and time limit on each match are always the *home* team's
club's values.

If the spec sets `name`, every page shows it in a banner above the page
title (including `index.html`, which recovers it from one of the other
report files rather than needing the spec re-read). If `draft: true` is
also set, that banner is made prominent and prefixed "DRAFT".

## Generating synthetic test fixtures

`genfixtures.py` generates a schedule from synthetic date rules (useful for
exercising the solver without a real spec file):

```bash
python genfixtures.py
```

## Publishing runs via GitHub Pages

The root `index.html` and everything under `runs/` are plain static HTML, and
are published in this repo with GitHub Pages. The `main` branch build is
published at <https://amdw.github.io/fixtures/>.

Both index layers are build artifacts, not source: `index.html` is
gitignored everywhere (root and per-run), and `.github/workflows/pages.yml`
runs `build_indexes.py` before every deploy to regenerate them straight from
whatever `runs/*/` folders are committed. `build_indexes.py` has no
third-party dependencies (unlike `run.py`, it needs neither ortools nor
pyyaml), so nothing needs to be installed for that step. Run it locally
(`python build_indexes.py`) any time you want to preview the index pages
without going through `run.py`.

### Previewing locally

Regenerate the index pages, then serve the repo root with Python's built-in
web server:

```bash
python build_indexes.py
python -m http.server
```

Open <http://localhost:8000/> — it's the same `index.html` and `runs/` the
Pages workflow deploys (the rest of the repo is visible too, but harmless;
the workflow just doesn't copy it into the deployed site). For a preview
that's an exact match of what gets deployed, replicate the workflow's
"Assemble site" step first and serve that instead:

```bash
mkdir -p _site
cp index.html _site/index.html
cp -r runs _site/runs
python -m http.server --directory _site
```

## Testing

```bash
python run_tests.py
```

## Code Quality

This project uses automated code quality tools:

### Formatting and Linting

```bash
# Format code with Ruff
pipenv run ruff format .

# Check for code issues
pipenv run ruff check .

# Auto-fix issues where possible
pipenv run ruff check . --fix
```

### Type Checking

```bash
# Run type checker
pipenv run mypy .
```

## License

This project is licensed under the Apache License, Version 2.0. See the
[LICENSE](LICENSE) file for details.
