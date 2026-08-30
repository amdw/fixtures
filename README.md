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
file named `spec.yaml`, placed inside the run folder you want it published
under (e.g. `runs/2025-26-season/spec.yaml`), then solve it:

```bash
python solve.py runs/2025-26-season/spec.yaml
```

This writes `solution.yaml` next to the spec. Re-running overwrites it in
place, so it's safe to rerun after editing the spec.

Only `spec.yaml` and `solution.yaml` are committed: the report (HTML pages plus
CSV exports) is a build artifact, assembled from that pair under `_site/` on
every GitHub Pages deploy (and gitignored) -- see "Publishing via GitHub Pages"
below. The fixed `spec.yaml` name is what lets the deploy discover each run
folder. To preview the report locally without deploying, run `build_site.py`
(see that section); `report.py` renders a single run's files into a directory
you name, which is mainly useful when iterating on report formatting.

`solve.py` runs the constraint solver and writes its result to a
`solution.yaml` file (canonical, solver-independent) next to the spec,
defaulting the output directory to the spec file's own directory.
`report.py` turns a `solution.yaml` back into the report (HTML and CSV),
reading the original spec alongside it for club/venue/name and excluded-fixture
details that aren't part of the solution itself. Keeping these as two separate
steps (rather than one combined command) means the report can be regenerated
-- e.g. after a formatting change -- without re-running the (comparatively
slow) solver: just rerun the report build against the existing
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
  1:
    scheme: double_round         # or single_round (Berger table; teams list = draw order)
    teams: [albany-1, hackney-1]

club_constraints:
  albany:
    home_dates: [2025-09-01, 2025-09-15, 2025-09-29]
    max_concurrent_matches: { home: 2 }
  hackney:
    home_dates: [2025-09-08, 2025-09-22]
    max_concurrent_matches: { home: 2 }
```

That's only a fraction of what the format supports -- per-club/per-team date
exclusions, per-scope (home/away/any) concurrency limits with per-date
overrides, pinned and withheld fixtures, and more. For the full field-by-field
reference, see:

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
  [`runs/example/`](runs/example/), and its report is built into
  `_site/runs/example/` (locally via `build_site.py`, and on every Pages
  deploy).

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

It only makes sense alongside the spec it was solved from -- the report build
resolves each entry's team IDs against that spec's `teams` to recover each
team's division and display name.

The report build writes, for each run, into `_site/runs/<run path>/`:

- `all-matches.html` — every fixture (date, division, home, away, venue
  name, start time, time limit), followed by a list of the full venue
  name and address of every home club appearing in the table
- `division-<n>.html` — one page per division (as above, minus division),
  with its own venues list covering just that division's home clubs
- `club-<id>.html` — one page per club, headed by that club's full venue
  name and address, with a consolidated table of all the club's matches
  (followed by a count of the distinct dates the club is in action on,
  split into total / home / away) followed by one table per team
- `all-matches.csv` — one row per match, `home_team` vs `away_team`, with
  `date` (ISO `yyyy-mm-dd`), `division`, and the home club's `venue`,
  `venue_address`, `start_time` and `time_limit`
- `all-matches-by-team.csv` — two rows per match, one from each team's point
  of view (`team`, `opponent`, `home_or_away`), so a club can filter its own
  team's fixtures straight into a calendar

Each team appears as its display name, its club's name (`*_club`) and its
index within that club (`*_index`), so the club and team number are available
directly even when the display name is a `name_override`.
- `index.html` — links to all of the above (the two CSV files are linked from
  its "All matches" section)

Both CSV files cover every division, and list withheld matches (a spec's
`exclude_fixtures`) with an empty `date`, mirroring the HTML report's "TBC"
rows.

None of these files are committed: they're all build artifacts, derived from
`spec.yaml` + `solution.yaml` (the report pages and CSV) and from each other
(the index pages), and regenerated on every GitHub Pages deploy -- see
"Publishing via GitHub Pages" below.

Venue name (not the address), start time and time limit on each match are
always the *home* team's club's values. If the spec sets `name`, every HTML
page shows it in a banner above the page title; if `draft: true` is also set,
that banner is made prominent and prefixed "DRAFT". (The CSV files carry the
fixture data only, with no such banner.)

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

The whole published site is assembled under `_site/` and published from the
`main` branch at <https://amdw.github.io/fixtures/> by
`.github/workflows/pages.yml`. Everything under `_site/` is a build artifact,
not source -- gitignored, and regenerated before every deploy from whatever
`runs/*/` folders (each a committed `spec.yaml` + `solution.yaml` pair) and
`spec-schema.json` are committed:

- `build_site.py` builds every run's report into `_site/runs/<run path>/` --
  the per-fixture HTML pages, the per-run and top-level index pages, *and* the
  CSV exports -- from each run folder's `spec.yaml` + `solution.yaml`. It runs
  `report.py` per run, so it needs the same dependencies (`pyyaml`, and
  `ortools` via `fmodel`); it does *not* re-run the (slow) solver.
- `build_schema_docs.py` renders `spec-schema.json` into
  `_site/schema-docs/`; it needs `json-schema-for-humans`.

Both write straight into `_site/`, so the workflow uploads that directory
as-is -- there's no separate copy/assemble step. It installs the core
dependencies plus the `docs` dependency group (`json-schema-for-humans`) --
but not the dev toolchain -- so both scripts can run.

Pull requests targeting `main` run the same `build` job but stop short of
deploying: the built site is attached to the run as a downloadable `_site`
artifact, so a reviewer can check what a PR would publish before it's merged.
Only pushes to `main` (and manual `workflow_dispatch` runs) go on to deploy.

Run the build locally any time you want a preview that exactly matches what
gets deployed:

```bash
uv run python3 build_site.py
uv run python3 build_schema_docs.py
python -m http.server --directory _site
```

Open <http://localhost:8000/>.

## License

This project is licensed under the Apache License, Version 2.0. See the
[LICENSE](LICENSE) file for details.
