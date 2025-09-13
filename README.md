# Fixtures

## Introduction

This repo is a proof-of-concept to demonstrate that Middlesex League fixtures
could potentially be generated using an OR-Tools constraint solver.

## Setup

Install Python (tested on 3.13) and Pipenv.

From the root of the repo:

```bash
pipenv install --dev
pipenv shell
```

## Running

Generate fixture schedules:

```bash
python genfixtures.py
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

This project is licensed under the Apache License, Version 2.0. See the [LICENSE](LICENSE) file for details.

```
Copyright 2025 Andrew Medworth

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
```
