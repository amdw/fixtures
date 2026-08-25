# Agent instructions

This project uses `pipenv` to manage its Python virtualenv (see `Pipfile`).

Agents are expected to be run in a shell where that virtualenv is already
activated (e.g. via `pipenv shell`, or invoked as `pipenv run <agent>`), so
that plain commands like `python foo.py` or `pytest` work without a
`pipenv run` prefix.

If the virtualenv does not appear to be active (e.g. `python -c "import ortools"`
fails, or `pipenv --venv` reports no venv for this project):

- If you are running interactively, with a user able to respond, do not work
  around it by prefixing every command with `pipenv run`. Instead, stop and
  ask the user to restart the agent from within the activated virtualenv.
- If you are running non-interactively (e.g. as an autonomous coding agent
  with no user available to respond), there is nobody to ask, so proceed
  using whatever means necessary to get a working environment instead of
  stalling — e.g. run `pipenv install --dev` and prefix commands with
  `pipenv run`, or otherwise set up the dependencies from `Pipfile`.

## Before committing

Before creating any git commit, run all of CI's build steps locally and
confirm they pass (see `.github/workflows/ci.yml` for the authoritative
list):

- `ruff format --check .`
- `ruff check .`
- `mypy .`
- `python all_tests.py`

Fix any failures (e.g. reformat with `ruff format .`) before committing,
rather than committing and fixing up afterwards.
