# Architecture

CLI CODER is designed to be a completely self-contained, zero-configuration AI development environment for Windows. The architecture is split into three main components: Bootstrapping, the REPL Controller, and the Autonomous Agent Router.

## 1. Bootstrapping Layer (`cli_coder.cmd`)

The primary design goal is "zero friction". On Windows, managing Python versions, virtual environments, and PATH variables is historically difficult.

- **`uv` Integration**: When `cli_coder.cmd` is executed, it checks for `uv.exe` (a blazingly fast Python package manager written in Rust). If it's missing, it downloads it directly via PowerShell.
- **Isolated Environment**: It creates an isolated virtual environment (`.env_cli`) using a specific Python version, preventing conflicts with the user's system Python.
- **Patch Injection (`patch_deps.py`)**: Before launching the application, a post-install script modifies upstream dependencies that are known to break on Windows:
  1. Fixes a crash in `prompt_toolkit` related to Windows console buffer initialization.
  2. Injects a Windows-specific system prompt into `mini-swe-agent` (`mini.yaml`), stripping out Linux-specific commands (`sed`, `cat`, `ls`) and replacing them with native CMD/PowerShell instructions (`type`, `findstr`, `dir`, and python scripts).

## 2. REPL Controller (`main.py`)

The main interface is a Read-Eval-Print Loop (REPL) that manages chat history and system prompts.

- **LiteLLM**: Used as the universal LLM router. It allows seamless switching between OpenAI, Anthropic, Gemini, Groq, and Mistral using standard API keys without changing application logic.
- **Streaming & Context**: Commands and chat messages are fed into a sliding window context. File reads (`/read`) and command outputs (`/run`) are appended directly into the LLM's conversation history.
- **Fast-Path Routing**: The controller intercepts specific `/code` tasks. If the user asks for analysis, summarization, or explanations (e.g., `/code analyse`), it gathers the local file context in memory and routes it through the standard, fast chat stream—bypassing the heavier, rate-limit-prone autonomous agent loop.

## 3. Autonomous Agent Router (`mini-swe-agent`)

When a user requests a complex task (e.g., `/code fix the crashing bug in main.py`), the system shifts from "chat mode" to "agent mode".

1. **Context Enrichment**: The current chat history is summarized and attached to the task prompt, allowing the agent to know what was discussed previously.
2. **Pre-Run Snapshot**: The system takes a timestamp snapshot of all files in the working directory.
3. **Subprocess Invocation**: `mini-swe-agent` is spawned in a subshell. It uses ReAct (Reasoning and Acting) loops to explore the file system, read code, write python scripts, edit files, and run tests.
4. **Post-Run Diffing**: Once the agent finishes (via the `COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` keyword), the system takes another snapshot, computes a diff (Created, Modified, Deleted), and presents a clean summary to the user. This summary is also fed back into the main REPL history so the primary chat LLM knows what the agent just accomplished.
