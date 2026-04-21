# Contributing to agent-triage

## Setup

```bash
git clone https://github.com/thebharathkumar/agent-triage.git
cd agent-triage
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Running tests

```bash
pytest --cov=triage --cov-report=term-missing
```

Coverage must stay at or above 90%.

## Linting and type checking

```bash
ruff check src/triage tests   # lint
mypy src/triage               # type check
```

Both must pass before opening a PR.

## Submitting a PR

1. Fork the repo and create a branch from `main`
2. Make your changes with tests
3. Ensure `pytest`, `ruff check`, and `mypy` all pass locally
4. Open a pull request with a clear description of what changed and why
