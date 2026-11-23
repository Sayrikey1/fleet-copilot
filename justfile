set shell := ["bash", "-cu"]

# List recipes
default:
    @just --list

# Create the venv and install the project + dev tools
install:
    uv venv
    uv pip install -e ".[dev]"

# Build the SQLite database from the telemetry NDJSON
seed:
    uv run fleet-copilot seed

# Run the offline, scripted end-to-end demo (no API key needed)
demo:
    uv run fleet-copilot demo

# Run the deterministic evaluation suite (no API key needed)
test:
    uv run pytest -q

# Run the optional live evaluation against the real model (needs ANTHROPIC_API_KEY)
live-eval:
    uv run pytest -m live -q

# Lint + type-check
lint:
    uv run ruff check src tests
    uv run mypy

# Ask a grounded question (live; needs ANTHROPIC_API_KEY). Example:
#   just ask "Which devices are low on disk?" acme-001
ask question company="acme-001":
    uv run fleet-copilot ask "{{question}}" --company {{company}}

# Show the append-only audit trail for a tenant
audit company="acme-001":
    uv run fleet-copilot audit-log --company {{company}}
