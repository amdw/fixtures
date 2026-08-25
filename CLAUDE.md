# Agent instructions

This project uses `uv` to manage its Python virtualenv (see `pyproject.toml`
and `uv.lock`).

Agents are expected to be run in a shell where that virtualenv is already
activated (e.g. via `source .venv/bin/activate`, or invoked as `uv run
<agent>`), so that plain commands like `python foo.py` or `pytest` work
without a `uv run` prefix.

If the virtualenv does not appear to be active (e.g. `python -c "import ortools"`
fails):

- If you are running interactively, with a user able to respond, do not work
  around it by prefixing every command with `uv run`. Instead, stop and
  ask the user to restart the agent from within the activated virtualenv.
- If you are running non-interactively (e.g. as an autonomous coding agent
  with no user available to respond), there is nobody to ask, so proceed
  using whatever means necessary to get a working environment instead of
  stalling — e.g. run `uv sync --dev` and prefix commands with `uv run`, or
  otherwise set up the dependencies from `pyproject.toml`.

## Before considering a change ready to commit

As soon as a change is otherwise complete — regardless of whether you are
about to run `git commit` yourself right away, or an interactive session
means that step is left to the user and may happen later, or not at all —
run all of CI's build steps locally and confirm they pass (see
`.github/workflows/ci.yml` for the authoritative list):

- `ruff format --check .`
- `ruff check .`
- `mypy .`
- `python all_tests.py`

Fix any failures (e.g. reformat with `ruff format .`) as part of finishing
the change, rather than presenting or committing it and fixing up afterwards.

## Before running `git commit` itself

- If you are running interactively, with a user able to respond, do not run
  `git commit` until the user has had the opportunity to review the change,
  even if they asked you to implement something that would naturally end in
  a commit. Finish the work (including the checks above), present it, and
  wait for the user to confirm before committing.
- If you are running non-interactively (e.g. as an autonomous coding agent
  with no user available to review), there is nobody to review the change
  first, so go ahead and commit once the checks above pass.
