# Agent instructions

This project uses `pipenv` to manage its Python virtualenv (see `Pipfile`).

Agents are expected to be run in a shell where that virtualenv is already
activated (e.g. via `pipenv shell`, or invoked as `pipenv run <agent>`), so
that plain commands like `python foo.py` or `pytest` work without a
`pipenv run` prefix.

If the virtualenv does not appear to be active (e.g. `python -c "import ortools"`
fails, or `pipenv --venv` reports no venv for this project), do not work
around it by prefixing every command with `pipenv run`. Instead, stop and
ask the user to restart the agent from within the activated virtualenv.
