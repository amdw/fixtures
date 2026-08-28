# Fixtures

## Introduction

This repo generates [Middlesex League](https://www.middlesexchess.com/mcca-for-chess-clubs/middlesex-league) fixture schedules using an [OR-Tools
constraint solver](https://developers.google.com/optimization/cp).

## Setup

Install Python (tested on 3.14) and [uv](https://docs.astral.sh/uv/).

From the root of the repo:

```bash
uv sync --dev
source .venv/bin/activate
```

Alternatively, prefix individual commands with `uv run` (e.g. `uv run python
all_tests.py`) instead of activating the venv — useful in non-interactive
contexts, since the venv already exists once `uv sync --dev` has been run.

## New run setup

Describe the clubs, teams, divisions and constraints in a YAML specification
file named `spec.yaml`, placed inside the run folder you want its output
written to (e.g. `runs/2025-26-season/spec.yaml`), then run the solver against
it, followed by the report generator:

```bash
python solve.py runs/2025-26-season/spec.yaml
python report.py runs/2025-26-season/spec.yaml runs/2025-26-season/solution.yaml runs/2025-26-season
```

This solves the fixtures and writes the HTML report alongside the spec in
`runs/2025-26-season/`. Re-running both overwrites the previous solution and
report in place, so it's safe to rerun after editing the spec.

Only `spec.yaml` and `solution.yaml` need committing: the HTML report is a
build artifact, regenerated from that pair on every GitHub Pages deploy (and
gitignored) -- see "Publishing via GitHub Pages" below. The fixed `spec.yaml`
name is what lets the deploy discover each run folder; running `report.py`
by hand is only needed to preview report-formatting changes locally.

`solve.py` runs the constraint solver and writes its result to a
`solution.yaml` file (canonical, solver-independent) next to the spec,
defaulting the output directory to the spec file's own directory.
`report.py` turns a `solution.yaml` back into the HTML report, reading the
original spec alongside it for club/venue/name and excluded-fixture details
that aren't part of the solution itself. Keeping these as two separate steps
(rather than one combined command) means the HTML report can be regenerated
-- e.g. after a report formatting change -- without re-running the
(comparatively slow) solver: just rerun `report.py` against the existing
`solution.yaml`. It also keeps solve-only flags (e.g. `--earliest-match-date`,
which excludes newly scheduled fixtures before a given date, defaulting to
today) on `solve.py` alone, rather than needing to be added to a combined
command too.

### Spec format

A minimal spec needs `clubs`, `teams`, `divisions` and `club_constraints`:

```yaml
name: "2025-26 Season"           # optional; shown as a subtitle on every report page
min_gap_days: 7                  # optional, defaults shown here

clubs:
  albany:                        # club ID: stable, referenced from teams/club_constraints/etc.
    name: Albany
    home_venue_name: Albany Sports Hall
    home_venue_address: 1 Sports Hall Road, London N1 1AA
    home_start_time: "19:30"
    home_time_limit: "75+15"     # chess time control: 75 min + 15 sec/move
  hackney:
    name: Hackney
    home_venue_name: Hackney Community Centre
    home_venue_address: 2 Community Lane, London E8 2BB
    home_start_time: "19:00"
    home_time_limit: "60+15"

teams:
  albany-1:                      # team ID: stable, referenced from divisions
    club: albany
    index: 1
  hackney-1:
    club: hackney
    index: 1

divisions:                       # each team's division: the only place it's given
  1: [albany-1, hackney-1]

club_constraints:
  albany:
    home_dates: [2025-09-01, 2025-09-15, 2025-09-29]
    max_concurrent_home_matches: 2
  hackney:
    home_dates: [2025-09-08, 2025-09-22]
    max_concurrent_home_matches: 2
```

That's only a fraction of what the format supports -- per-club/per-team date
exclusions, concurrency limits with per-date overrides, pinned and withheld
fixtures, and more. For the full field-by-field reference, see:

- **[spec-schema.json](spec-schema.json)**, the JSON Schema every field is
  defined in (rendered to a browsable reference page at
  <https://amdw.github.io/fixtures/schema-docs/spec-schema.html>, published
  alongside every other run's report by the same GitHub Pages build -- see
  "Publishing via GitHub Pages" below). Adding a
  `# yaml-language-server: $schema=<path-to-spec-schema.json>` comment atop a
  spec file also gets you inline validation and autocomplete while editing it,
  in any editor using the [YAML language
  server](https://github.com/redhat-developer/yaml-language-server) (e.g. VS
  Code's `redhat.vscode-yaml` extension) -- see
  [`runs/example/spec.yaml`](runs/example/spec.yaml) for an example.
- **[`runs/example/spec.yaml`](runs/example/spec.yaml)**, a full worked
  example; its `solution.yaml` sits alongside it in
  [`runs/example/`](runs/example/), and its report renders into the same
  folder (locally via `report.py`, and on every Pages deploy).

New constraint types can be added to `fixturespec.py` and `fmodel.Parameters`
as they're needed; `spec-schema.json` should be kept in step with them (a
test in `spec_schema_test.py` checks the schema still accepts a
representative spec, to catch it drifting out of sync).

### Solution and report output

`solve.py` writes `solution.yaml`, listing every scheduled fixture as a
home/away team ID pair (the same IDs used under `teams` and by
`fixed_fixtures`/`exclude_fixtures` in the spec) plus its date:

```yaml
fixtures:
  - home: albany-1
    away: hackney-1
    date: 2025-09-01
```

It only makes sense alongside the spec it was solved from -- `report.py`
resolves each entry's team IDs against that spec's `teams` to recover each
team's division and display name.

`report.py` then writes, into the run's folder:

- `all-matches.html` — every fixture (date, division, home, away, venue
  name, start time, time limit), followed by a list of the full venue
  name and address of every home club appearing in the table
- `division-<n>.html` — one page per division (as above, minus division),
  with its own venues list covering just that division's home clubs
- `club-<id>.html` — one page per club, headed by that club's full venue
  name and address, with a consolidated table of all the club's matches
  followed by one table per team
- `index.html` — links to all of the above

None of these HTML pages are committed: they're all build artifacts, derived
from `spec.yaml` + `solution.yaml` (the report pages) and from each other (the
index pages), and regenerated on every GitHub Pages deploy -- see "Publishing
via GitHub Pages" below.

Venue name (not the address), start time and time limit on each match are
always the *home* team's club's values. If the spec sets `name`, every page
shows it in a banner above the page title; if `draft: true` is also set,
that banner is made prominent and prefixed "DRAFT".

## Development

### Testing

```bash
python all_tests.py
```

### Code Quality

```bash
# Format code with Ruff
uv run ruff format .

# Check for code issues (add --fix to auto-fix where possible)
uv run ruff check .

# Run the type checker
uv run mypy .
```

### Generating synthetic test fixtures

`genfixtures.py` generates a schedule from synthetic date rules (useful for
exercising the solver without a real spec file):

```bash
python genfixtures.py
```

### Publishing via GitHub Pages

The root `index.html`, `schema-docs/spec-schema.html` (plus whatever support
files `json-schema-for-humans` copies alongside it) and everything under
`runs/` are plain static HTML, published from the `main` branch at
<https://amdw.github.io/fixtures/> by `.github/workflows/pages.yml`. All of
these are build artifacts, not source -- gitignored, and regenerated before
every deploy from whatever `runs/*/` folders (each a committed `spec.yaml` +
`solution.yaml` pair) and `spec-schema.json` are committed:

- `build_html.py` regenerates every HTML page under `runs/` -- the report
  pages *and* the per-run and top-level index pages -- from each run folder's
  `spec.yaml` + `solution.yaml`. It renders the report pages via `report.py`,
  so it needs the same dependencies (`pyyaml`, and `ortools` via `fmodel`); it
  does *not* re-run the (slow) solver.
- `build_schema_docs.py` renders `spec-schema.json`; it needs
  `json-schema-for-humans`.

The workflow installs the dev dependencies first, so both can run. Run them
locally any time you want to preview without a full deploy:

```bash
uv run python3 build_html.py
uv run python3 build_schema_docs.py
python -m http.server
```

Open <http://localhost:8000/> — it's the same files the Pages workflow
deploys (the rest of the repo is visible too, but harmless; the workflow just
doesn't copy it into the deployed site). For a preview that's an exact match
of what gets deployed, replicate the workflow's "Assemble site" step first
and serve that instead:

```bash
mkdir -p _site
cp index.html _site/index.html
cp -r schema-docs _site/schema-docs
cp -r runs _site/runs
python -m http.server --directory _site
```

## License

This project is licensed under the Apache License, Version 2.0. See the
[LICENSE](LICENSE) file for details.
