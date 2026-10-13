# Contributing to CLI CODER

Thanks for your interest! Here's how to contribute.

## Getting Started

1. Fork the repo
2. Clone your fork
3. Set up a development environment (below)
4. Make your change, with tests
5. Open a pull request

## Development Setup

Python 3.12 or newer. On Windows you can also use the launcher, which creates `.env_cli/` for you.

```bash
python -m venv .venv
.venv\Scripts\activate           # Windows
pip install -r requirements.txt
pip install ruff
python patch_deps.py             # Windows only
```

- `litellm` and `mini-swe-agent` are pinned on purpose. See the comments in `requirements.txt` before changing them.
- Config is stored in `.env` (git-ignored). Never commit it.

## Running Checks

```bash
python -m unittest -v    # tests (no network calls; LLM responses are mocked)
ruff check .            # lint
```

CI runs both on every pull request, on Windows and Ubuntu.

## Code Layout

| File | Purpose |
|------|---------|
| `main.py` | REPL, slash commands, streaming chat, token accounting, coding agent routing |
| `config.py` | Providers, default models, `.env` handling and first-run setup |
| `patch_deps.py` | Windows-only fixes applied to the installed mini-swe-agent |
| `tests/` | Unit tests. `helpers.py` has the shared fakes for litellm and the console. |

Keep the code plain Python with no frameworks. `rich` is the only UI dependency. Match the style of the code around your change.

## Pull Requests

- Keep PRs focused on a single change.
- Add or update tests for behaviour changes. Tests should not call a real API. Mock `litellm` (see `tests/test_main.py`).
- Update `README.md` (command table) and `CHANGELOG.md` for user-visible changes.
- Explain any change to a pinned version in the PR description.
- Use the PR template. It has a checklist.

## Adding a New Provider

1. Add a `Provider(...)` entry to `PROVIDERS` in `config.py`. Give it a name, a default LiteLLM model string, and either `key_env` (an API key variable) or `endpoint_env` plus `default_endpoint` (for a local server such as Ollama).
2. Run `python -m unittest tests.test_config`. Its routing test checks that every default model resolves in LiteLLM.
3. Add the provider to the provider table in `README.md`. LiteLLM handles the API routing.

## Adding a New Slash Command

1. Write a handler in `main.py` with the `(args, s)` signature and register it with `@command("/yourcommand")`. `s` is the `Session` (model, history, usage).
2. Add the command to `HELP_SECTIONS` in `main.py`.
3. Add it to the command table in `README.md`.
4. Add tests in `tests/test_main.py`. Use `capture()` from `tests/helpers.py` to read the output.

## Reporting Issues

Bug reports and feature requests are welcome via GitHub Issues. For security problems, follow [SECURITY.md](SECURITY.md) and do not open a public issue.
