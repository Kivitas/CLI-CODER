# Architecture

CLI CODER has three layers: bootstrapping (Windows launcher), a REPL controller that handles chat and commands, and an autonomous agent router that hands coding tasks to mini-swe-agent. Windows-specific fixes live in a small patch script.

## 1. Bootstrapping (`cli_coder.cmd`, built locally)

The goal is "zero friction" on Windows, where Python versions, virtual environments and PATH settings are hard to manage.

- **`uv`**: the launcher downloads `uv.exe` (a fast Python package manager) if it is missing.
- **Isolated environment**: `uv` creates `.env_cli/` with Python 3.12 and installs `requirements.txt` into it. The environment is separate from any system Python.
- **Patching**: right after setup, the launcher runs `patch_deps.py` with the environment's Python (see section 4).

The launcher is built locally rather than shipped, so Windows SmartScreen does not flag it. See [INSTALLATION.md](INSTALLATION.md).

## 2. REPL Controller (`main.py`)

### Session state

A `Session` dataclass holds the model, system prompt, chat history, usage totals and the last exchange. Commands receive it as an argument, so tests can create a fresh session without touching global state.

### Providers

`config.py` holds the provider table, `PROVIDERS`. Each entry has a name, a default LiteLLM model string, and either an API key variable or a local endpoint (Ollama). `ensure_setup()` runs the setup menu when no provider is configured, and `/key` runs it again. Routing is handled by LiteLLM from the model string's prefix (`xai/`, `openrouter/`, `ollama_chat/`, and so on). Ollama uses the `ollama_chat/` prefix because it calls the chat endpoint. `provider_of()` treats `ollama_chat` and `ollama` as one provider, so `/key` and `/models` compare them correctly.

### Command table

Commands are registered with `@command("/name", ...)`. Each handler takes `(args, session)`. `handle_command()` splits the line, looks up the handler, and reports unknown commands. The REPL sends anything that does not start with `/` to `chat()`.

### Chat and streaming

1. `build_messages()` builds the request: the optional system prompt, then as much recent history as fits `CONTEXT_BUDGET_CHARS`, then the new message. Requests start with a user turn, because some providers reject other openings. The full history is never changed, only the request.
2. `stream_reply()` calls `litellm.completion(stream=True)` and renders the growing reply as Markdown with a `rich` `Live` view.
3. `measure_reply()` builds the complete response from the stream chunks with `litellm.stream_chunk_builder`. It uses the provider's token counts when present. Otherwise it estimates about four characters per token and marks the figures with `~`. Cost comes from `litellm.completion_cost`. If LiteLLM has no price for the model, the reply counts as unpriced.
4. The exchange is appended to history, and the footer shows the tokens and cost.

### Other commands

- `/retry` removes the last exchange, then re-sends its prompt. If the request fails, the old exchange is put back.
- `/run` starts the command in its own process group, reads output line by line, and feeds the last 50 lines into history. A timer kills the whole command after `RUN_TIMEOUT_SECONDS`. Ctrl+C kills it too.
- `/copy` pipes the last reply to `clip`, `pbcopy`, `xclip` or `wl-copy`.

## 3. Autonomous Agent Router (`mini-swe-agent`)

When you run `/code <task>` with a real task, the system switches from chat to agent mode:

1. **Context**: the last six history messages (truncated to 500 characters each) are added to the task.
2. **Snapshot**: `snapshot_files()` records each file's modification time. It stops and reports "too large" past `MAX_SNAPSHOT_FILES`, so a huge folder does not stall the app.
3. **Subprocess**: `mini -t <task> -y --exit-immediately --model <model>` runs in the current folder. `find_mini_exe()` looks next to the running Python first, then in `.env_cli/`, then on `PATH`.
4. **Diff**: after the agent exits, a second snapshot gives created, modified and deleted files. The summary is shown and added to history so the chat model knows what the agent did.

Simple analysis requests (`/code summarize`, `/code explain`, ...) skip the agent. `/analyze` reads the README and up to three Python files and sends them to the chat model.

## 4. Windows Patches (`patch_deps.py`)

The patches target one exact mini-swe-agent release (2.4.6, pinned in `requirements.txt`). They run only on Windows.

1. **Lazy prompt sessions.** Upstream creates `prompt_toolkit` sessions at import time. On Windows that crashes when no console screen buffer exists. The patched `prompt_user.py` replaces them with `_LazyPromptSession`, which creates the real `PromptSession` on first use and forwards attribute access. `interactive.py` imports `prompt_session` directly, so the name must stay a usable object. It was `None` in an earlier version, which made the agent's completion prompt fail.
2. **Windows command examples.** `mini.yaml` describes Unix commands to the agent. The patch replaces only that example block, with `dir`, `type` and `findstr` instructions. The boundary is the next line indented less than four spaces, so the agent settings after the block (`step_limit`, `cost_limit`, `mode`) are kept.

Both patches are idempotent (they check for a marker). The script exits with a non-zero code if a file is missing, which can happen if mini-swe-agent changes its layout.

## 5. Tests and CI

- `tests/` uses only `unittest`. Fake litellm chunks and a captured console (`tests/helpers.py`) let the tests run without network access or API keys.
- Some tests run against the installed mini-swe-agent. They check that the real `mini.yaml` still parses and keeps its settings after patching. They skip when the package is missing.
- GitHub Actions (`.github/workflows/ci.yml`) runs `ruff check .` and the test suite on Windows and Ubuntu, Python 3.12 and 3.13.
