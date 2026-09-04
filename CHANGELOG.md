# Changelog

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
