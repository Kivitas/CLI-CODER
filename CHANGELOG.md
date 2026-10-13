# Changelog

## v1.3.0 (2026-10-13)

### Added
- **Markdown rendering** for streamed replies (headings, lists, code blocks), via `rich`.
- **Token and cost tracking**: each reply shows its token count and cost; `/info` shows session totals.
- **Context budget**: older messages are left out of each request once the budget is reached (`CLI_CODER_CONTEXT_CHARS`, default 120000). Nothing is deleted from `/history` or `/save`.
- `/retry` regenerates the last reply.
- `/copy` copies the last reply to the clipboard (Windows `clip`, macOS `pbcopy`, Linux `xclip`/`wl-copy`).
- `/models` lists the default model for each provider.
- `/system clear` removes the system prompt.
- **One-shot mode**: `python main.py "question"` prints one answer and exits. `python main.py --version` prints the version.
- `/hermes` asks for confirmation before downloading and running its installer, and checks that `hermes.exe` exists afterwards.
- Unit tests (`python -m unittest`) and linting (`ruff check .`).
- GitHub Actions CI on Windows and Ubuntu, Python 3.12 and 3.13.
- Dependabot, issue and pull request templates, and `SECURITY.md`.

### Added (providers)
- **xAI (Grok)** (`XAI_API_KEY`), **OpenRouter** (`OPENROUTER_API_KEY`) and **Ollama** (local, no key; `OLLAMA_API_BASE`) join OpenAI, Anthropic, Gemini, Groq and Mistral.
- Setup and `/key` work for all eight providers. Ollama asks for the server address instead of a key.
- `/models` shows each provider's default model and whether it is set up.
- A test checks that every default model resolves through LiteLLM, so a broken model route fails CI.

### Changed
- Default models: OpenAI `openai/gpt-5.6` (was `gpt-4o`), Anthropic `anthropic/claude-sonnet-5-5` (was `claude-sonnet-4-20250514`), xAI `xai/grok-4.6`, OpenRouter `openrouter/openrouter/auto`, Ollama `ollama_chat/llama3.2`, Groq `groq/openai/gpt-oss-120b` (was `llama3-70b-8192`, which is not in LiteLLM's model list).
- `/code` runs mini-swe-agent with `--exit-immediately`, so it exits when the task finishes and the change summary appears straight away.
- `/code` looks for mini-swe-agent next to the running Python first, so running from source works without the launcher.
- `requirements.txt` pins `mini-swe-agent==2.4.6` (the release `patch_deps.py` is written for) and keeps `litellm==1.82.6`. Both pins are documented in the file.
- `/info` shows only the last four characters of each API key.
- Output is UTF-8 even when redirected or piped on Windows.
- `/ls` shows the rest of the listing when one entry is a broken symlink.
- `/read` skips folders when a glob matches them.

### Fixed
- **`/code` could fail when the agent finished.** The Windows patch set `prompt_session` to `None`, but mini-swe-agent calls `.prompt()` on it for the completion prompt. The patch now creates the session lazily and forwards calls to it.
- **The Windows patch removed agent settings.** The YAML rewrite also deleted `step_limit`, `cost_limit` and `mode` under `agent:`. It now replaces only the example block.
- `/info` always showed 0 tokens.
- `/key` set the default model of the first saved key rather than the provider you picked.
- An empty API key was accepted during setup. Setup now asks again.
- `/run`: the timeout was not enforced while output was streaming, Ctrl+C during a command closed the whole app, and on Linux/macOS only the shell was killed, not the command it ran. Timeouts and Ctrl+C now stop the whole command.

### Upgrade notes
- If you built the launcher before v1.3.0, delete the `.env_cli` folder and run `cli_coder.cmd` again. That rebuilds the environment with the pinned versions and the fixed patches.
- Your `.env` file and chat history are not affected.

## v1.2.0 (2026-09-04)

### Features
- **Hermes Agent Integration**: Added `/hermes` command to auto-install and launch the self-improving Hermes Agent from Nous Research.

## v1.1.0 (2026-07-19)

### Features
- Interactive AI chat with streaming responses
- Coding agent via `mini-swe-agent` (`/code`)
- Multi-provider support out of the box (OpenAI, Anthropic, Gemini, Groq, Mistral)
- File operations: `/ls`, `/read`, `/tree`, `/cwd`
- Shell command execution: `/run`
- Chat management: `/system`, `/history`, `/save`, `/clear`
- Config management: `/model`, `/key`, `/info`, `/reset`
- File change tracking and diff reporting after coding agent runs
- Chat context automatically passed to coding agent
- Zero-setup environment: `cli_coder.cmd` automatically downloads Python/`uv` and creates an isolated venv.

### Enhancements & Bug Fixes (v1.1.0)
- **Smart Code Routing**: `/code analyse`, `/code summarize`, etc., bypass the SWE agent and use a fast reading path to prevent API rate limits.
- **Dedicated Summaries**: Added `/analyze` command.
- **Windows SWE Patching**: Automatically intercepts and rewrites the internal agent prompt (`mini.yaml`) to replace Unix instructions (`sed`, `cat`) with Windows CMD instructions, dramatically reducing agent failure loops.
