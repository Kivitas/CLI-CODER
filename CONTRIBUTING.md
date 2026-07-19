# Contributing to CLI CODER

Thanks for your interest! Here's how to contribute.

## Getting Started

1. Fork the repo
2. Clone your fork
3. Run `cli_coder.cmd` to set up the environment automatically
4. Make your changes

## Development Setup

- Python 3.12+ required (auto-installed via `uv`)
- Dependencies managed via `uv` (auto-downloaded on first run)
- Virtual environment lives in `.env_cli/` (gitignored)
- Config stored in `.env` (gitignored)

## Pull Requests

- Keep PRs focused on a single change
- Test your changes by running `cli_coder.cmd`
- Follow existing code style — plain Python, no frameworks

## Adding a New Provider

1. Add the provider to `PROVIDERS` dict in `config.py`
2. Add a default model to `DEFAULT_MODELS` in `config.py`
3. That's it — LiteLLM handles the API routing automatically

## Adding a New Slash Command

1. Create a `cmd_yourcommand()` function in `main.py`
2. Add routing in the main `while` loop
3. Add it to `HELP_TEXT`

## Reporting Issues

Bug reports and feature requests are welcome via GitHub Issues.
