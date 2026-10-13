"""
CLI CODER — A terminal AI assistant.
  Normal chat  → direct LLM API call (via litellm) with streaming
  Coding tasks → /code <task> routes to mini-swe-agent with full context
  One-shot     → python main.py "question" prints one answer and exits
"""
import argparse
import datetime
import glob
import os
import platform
import shutil
import signal
import subprocess
import sys
import threading
from dataclasses import dataclass, field

import litellm
from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

import config
from config import (
    MODEL_ENV,
    PROVIDERS,
    choose_provider,
    configured_providers,
    ensure_setup,
    mask_key,
    provider_of,
)

__version__ = "1.3.0"

litellm.suppress_debug_info = True

BANNER = r"""
   ____ _     ___        ____ ___  ____  _____ ____
  / ___| |   |_ _|      / ___/ _ \|  _ \| ____|  _ \
 | |   | |    | | _____| |  | | | | | | |  _| | |_) |
 | |___| |___ | ||_____| |__| |_| | |_| | |___|  _ <
  \____|_____|___|      \____\___/|____/|_____|_| \_\

         Made by Kivitas : github.com/kivitas
"""


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


# Characters of history (plus the system prompt and the new message) sent per request.
# Older messages are left out of the request, but they stay in /history and /save.
CONTEXT_BUDGET_CHARS = _env_int("CLI_CODER_CONTEXT_CHARS", 120_000)
CHAT_TIMEOUT_SECONDS = 120
RUN_TIMEOUT_SECONDS = 120
MAX_READ_CHARS = 50_000
MAX_SNAPSHOT_FILES = 20_000
SKIP_DIRS = {"node_modules", "__pycache__", "venv"}
ANALYSIS_WORDS = ("analyse", "analyze", "summarize", "explain", "summary")

console = Console()


# ─── State ────────────────────────────────────────────────────

@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost: float = 0.0
    unpriced_replies: int = 0

    def record(self, prompt_tokens: int, completion_tokens: int, cost: float | None):
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
        if cost is None:
            self.unpriced_replies += 1
        else:
            self.cost += cost


@dataclass
class Session:
    model: str
    system_prompt: str | None = None
    history: list = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    last_prompt: str | None = None
    last_reply: str | None = None


# ─── Output helpers ───────────────────────────────────────────

def show_error(message: str):
    console.print(Text(f"[Error] {message}", style="red"))


def print_panel(title: str, rows):
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column(overflow="fold")
    for label, value in rows:
        table.add_row(Text(str(label)), Text(str(value)))
    console.print(Panel(table, title=title, title_align="left", border_style="dim", expand=False))


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "..."


def _format_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size // 1024} KB"
    return f"{size // (1024 * 1024)} MB"


# ─── Chat (streaming) ─────────────────────────────────────────

def estimate_tokens(text: str) -> int:
    """Rough token count (about 4 characters per token), used when the provider reports no usage."""
    return len(text) // 4 if text else 0


def build_messages(s: Session, user_msg: str) -> tuple[list, int]:
    """Build the request messages. Returns (messages, number of history messages left out)."""
    reserved = len(user_msg) + (len(s.system_prompt) if s.system_prompt else 0)
    budget = CONTEXT_BUDGET_CHARS - reserved

    kept = []
    used = 0
    for msg in reversed(s.history):
        size = len(msg["content"])
        if used + size > budget:
            break
        kept.append(msg)
        used += size
    kept.reverse()

    dropped = len(s.history) - len(kept)
    # Most providers expect the conversation to start with a user turn.
    while kept and kept[0]["role"] != "user":
        kept.pop(0)
        dropped += 1

    messages = []
    if s.system_prompt:
        messages.append({"role": "system", "content": s.system_prompt})
    messages.extend(kept)
    messages.append({"role": "user", "content": user_msg})
    return messages, dropped


def stream_reply(messages: list, model: str) -> tuple[str, list]:
    """Stream a reply, rendering the Markdown as it arrives. Returns (reply text, raw chunks)."""
    chunks = []
    parts = []
    with Live(Markdown(""), console=console, refresh_per_second=10, vertical_overflow="visible") as live:
        for chunk in litellm.completion(model=model, messages=messages, stream=True, timeout=CHAT_TIMEOUT_SECONDS):
            chunks.append(chunk)
            if not chunk.choices:
                continue
            content = getattr(chunk.choices[0].delta, "content", None)
            if content:
                parts.append(content)
                live.update(Markdown("".join(parts)))
    return "".join(parts), chunks


def measure_reply(chunks: list, messages: list, reply: str):
    """Return (prompt_tokens, completion_tokens, cost or None, estimated) for one reply."""
    response = None
    try:
        response = litellm.stream_chunk_builder(chunks, messages=messages)
    except Exception:
        response = None

    usage = getattr(response, "usage", None)
    if usage is not None and (usage.total_tokens or 0) > 0:
        prompt_tokens, completion_tokens, estimated = usage.prompt_tokens or 0, usage.completion_tokens or 0, False
    else:
        prompt_tokens = estimate_tokens("".join(m["content"] for m in messages))
        completion_tokens = estimate_tokens(reply)
        estimated = True

    cost = None
    if response is not None:
        try:
            cost = litellm.completion_cost(completion_response=response)
        except Exception:
            cost = None
    return prompt_tokens, completion_tokens, cost, estimated


def chat(user_msg: str, s: Session) -> bool:
    """Send a message to the LLM, stream the reply and record it. Returns True on success."""
    messages, dropped = build_messages(s, user_msg)
    if dropped:
        console.print(Text(f"  ({dropped} older message(s) left out to fit the context budget)", style="dim"))
    console.print("\n[bold cyan]AI >[/bold cyan]")

    try:
        reply, chunks = stream_reply(messages, s.model)
    except KeyboardInterrupt:
        print("\n  [Interrupted]\n")
        return False
    except Exception as e:
        show_error(str(e))
        print()
        return False

    prompt_tokens, completion_tokens, cost, estimated = measure_reply(chunks, messages, reply)
    s.usage.record(prompt_tokens, completion_tokens, cost)

    s.history.append({"role": "user", "content": user_msg})
    s.history.append({"role": "assistant", "content": reply})
    s.last_prompt = user_msg
    s.last_reply = reply

    prefix = "~" if estimated else ""
    footer = f"tokens {prefix}{prompt_tokens:,} in | {prefix}{completion_tokens:,} out"
    if cost is None:
        footer += " | cost n/a"
    elif cost:
        footer += f" | ${cost:.4f}"
    console.print()  # the live Markdown view does not end its own line
    console.print(Text(f"  {footer}", style="dim"))
    console.print()
    return True


# ─── SWE Agent ────────────────────────────────────────────────

def build_agent_context(s: Session) -> str:
    """Build context from recent chat history to give the coding agent."""
    if not s.history:
        return ""
    lines = ["\n## Recent conversation context:"]
    for msg in s.history[-6:]:
        role = "User" if msg["role"] == "user" else "Assistant"
        lines.append(f"{role}: {_truncate(msg['content'], 500)}")
    return "\n".join(lines)


def snapshot_files(directory: str) -> tuple[dict, bool]:
    """Map each file path to its mtime. Returns (snapshot, truncated) where truncated means the tree was too big."""
    snap = {}
    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS]
        for name in files:
            path = os.path.join(root, name)
            try:
                snap[path] = os.path.getmtime(path)
            except OSError:
                continue
            if len(snap) > MAX_SNAPSHOT_FILES:
                return snap, True
    return snap, False


def diff_snapshots(before: dict, after: dict, directory: str) -> tuple[list, list, list]:
    """Compare two snapshots and return (created, modified, deleted) as relative paths."""
    created = []
    modified = []
    deleted = []
    for path, mtime in after.items():
        if path not in before:
            created.append(os.path.relpath(path, directory))
        elif mtime != before[path]:
            modified.append(os.path.relpath(path, directory))
    for path in before:
        if path not in after:
            deleted.append(os.path.relpath(path, directory))
    return created, modified, deleted


def run_with_tracking(agent_name: str, cmd: list, task: str, s: Session):
    """Run an agent command, then show which files it created, changed or deleted."""
    cwd = os.getcwd()
    rows = [("Task", _truncate(task, 60))] if task else []
    rows.append(("CWD", cwd))
    print_panel(agent_name, rows)

    before, before_truncated = snapshot_files(cwd)
    try:
        subprocess.run(cmd, env=os.environ, cwd=cwd)
    except KeyboardInterrupt:
        print("\n  [Interrupted]")
    except Exception as e:
        show_error(str(e))
    after, after_truncated = snapshot_files(cwd)

    if before_truncated or after_truncated:
        print("  (Too many files to track changes in this folder; skipping the change summary.)\n")
        return

    created, modified, deleted = diff_snapshots(before, after, cwd)
    if not (created or modified or deleted):
        print("  No file changes detected.\n")
        return

    lines = [Text(f"+ {f}", style="green") for f in created]
    lines += [Text(f"~ {f}", style="yellow") for f in modified]
    lines += [Text(f"- {f}", style="red") for f in deleted]
    console.print(Panel(Group(*lines), title="Changes", title_align="left", border_style="dim", expand=False))

    summary = []
    if created:
        summary.append(f"Created: {', '.join(created)}")
    if modified:
        summary.append(f"Modified: {', '.join(modified)}")
    if deleted:
        summary.append(f"Deleted: {', '.join(deleted)}")
    if task:
        s.history.append({"role": "user", "content": f"[{agent_name} completed task: {task}]"})
    s.history.append({"role": "assistant", "content": f"{agent_name} finished. {'; '.join(summary)}"})
    print()


def find_mini_exe() -> str | None:
    """Find the mini-swe-agent entry point: next to this Python, in the launcher venv, or on PATH."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    candidate_dirs = [
        os.path.dirname(sys.executable),
        os.path.join(script_dir, ".env_cli", "Scripts"),
        os.path.join(script_dir, ".env_cli", "bin"),
    ]
    for directory in candidate_dirs:
        found = shutil.which("mini", path=directory)
        if found:
            return found
    return shutil.which("mini")


def run_code_agent(task: str, s: Session):
    """Launch mini-swe-agent for a coding task with context and change tracking."""
    mini = find_mini_exe()
    if not mini:
        show_error("mini-swe-agent not found. Re-run cli_coder.cmd, or run: pip install -r requirements.txt\n")
        return

    cwd = os.getcwd()
    full_task = f"{task}\n\nWorking directory: {cwd}{build_agent_context(s)}"
    # --exit-immediately: the agent exits when it finishes instead of waiting for follow-up input,
    # so the change summary below appears right away.
    cmd = [mini, "-t", full_task, "-y", "--exit-immediately", "--model", s.model]
    run_with_tracking("Coding Agent", cmd, task, s)


def install_hermes(hermes_exe: str) -> bool:
    install_cmd = "powershell -Command \"iex (irm https://hermes-agent.nousresearch.com/install.ps1)\""
    try:
        subprocess.run(install_cmd, shell=True, check=True)
    except subprocess.CalledProcessError:
        return False
    return os.path.isfile(hermes_exe)


def run_hermes(task: str, s: Session):
    """Launch Hermes Agent, installing it first (after confirmation) if it is missing."""
    if platform.system() != "Windows":
        print("  [Error] /hermes auto-install is currently only supported on Windows.\n")
        return

    localappdata = os.environ.get("LOCALAPPDATA")
    if not localappdata:
        print("  [Error] LOCALAPPDATA environment variable not found.\n")
        return

    hermes_exe = os.path.join(localappdata, "hermes", "bin", "hermes.exe")
    if not os.path.isfile(hermes_exe):
        print("  [Info] Hermes Agent is not installed.")
        print("  Installing it runs the install script from https://hermes-agent.nousresearch.com/install.ps1")
        answer = input("  Download and run it now? [y/N]: ").strip().lower()
        if answer != "y":
            print("  Cancelled.\n")
            return
        print("  Installing Hermes Agent (this may take a few minutes)...")
        if not install_hermes(hermes_exe):
            print(f"  [Error] Hermes Agent install failed, or hermes.exe was not found at {hermes_exe}.\n")
            return
        print("  [Success] Hermes Agent installed!")

    cwd = os.getcwd()
    full_task = f"{task}\n\nWorking directory: {cwd}{build_agent_context(s)}" if task else ""
    cmd = [hermes_exe]
    if full_task:
        cmd.append(full_task)
    run_with_tracking("Hermes Agent", cmd, task, s)


# ─── Process helpers ──────────────────────────────────────────

def _kill_process_tree(proc: subprocess.Popen):
    """Kill a shell command and everything it started (taskkill /T on Windows, the process group elsewhere)."""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            proc.kill()


def copy_to_clipboard(text: str) -> bool:
    """Copy text to the system clipboard. Returns False if no clipboard tool is available."""
    if os.name == "nt":
        # clip.exe reads UTF-16 with a byte-order mark correctly, which keeps non-ASCII text intact.
        cmd, data = ["clip"], text.encode("utf-16")
    elif sys.platform == "darwin":
        cmd, data = ["pbcopy"], text.encode("utf-8")
    else:
        candidates = [["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]]
        cmd = next((c for c in candidates if shutil.which(c[0])), None)
        if cmd is None:
            return False
        data = text.encode("utf-8")
    try:
        subprocess.run(cmd, input=data, check=True)
    except (OSError, subprocess.CalledProcessError):
        return False
    return True


# ─── Command table ────────────────────────────────────────────

COMMANDS = {}

HELP_SECTIONS = [
    ("Coding", [
        ("/code <task>", "Start the coding agent"),
        ("/hermes [task]", "Start Hermes Agent"),
        ("/run <cmd>", "Run a shell command (output goes to chat)"),
    ]),
    ("Files", [
        ("/ls [pattern]", "List files (supports glob)"),
        ("/read <file>", "Read file(s) into chat"),
        ("/cwd <path>", "Change working directory"),
        ("/tree", "Show directory tree"),
        ("/analyze", "Summarize the project"),
    ]),
    ("Chat", [
        ("/system <text>", "Set system prompt ('/system clear' removes it)"),
        ("/retry", "Regenerate the last reply"),
        ("/copy", "Copy the last reply to the clipboard"),
        ("/history", "Show conversation log"),
        ("/save", "Save chat to a .md file"),
        ("/clear", "Clear chat history"),
    ]),
    ("Config", [
        ("/model [name]", "Show or change the AI model"),
        ("/models", "Show default models per provider"),
        ("/key", "Set up or switch provider (API key or address)"),
        ("/info", "Show settings and token usage"),
        ("/reset", "Wipe config and start fresh"),
    ]),
    ("General", [
        ("/help", "Show this help"),
        ("exit", "Quit"),
    ]),
]


def command(*names):
    """Register a handler for one or more slash commands. Handlers take (args, session)."""
    def register(fn):
        for name in names:
            COMMANDS[name] = fn
        return fn
    return register


def handle_command(line: str, s: Session):
    parts = line.split(maxsplit=1)
    name = parts[0].lower()
    args = parts[1].strip() if len(parts) > 1 else ""
    handler = COMMANDS.get(name)
    if handler is None:
        print(f"  Unknown command: {name}. Type /help\n")
        return
    handler(args, s)


@command("/help")
def cmd_help(args, s):
    table = Table.grid(padding=(0, 2))
    table.add_column(no_wrap=True)
    table.add_column()
    for title, items in HELP_SECTIONS:
        table.add_row(Text(title, style="bold yellow"), "")
        for usage, desc in items:
            table.add_row(Text(f"  {usage}", style="bold cyan"), Text(desc))
        table.add_row("", "")
    console.print(Panel(table, title="CLI CODER commands", title_align="left", border_style="dim", expand=False))


@command("/clear")
def cmd_clear(args, s):
    s.history.clear()
    s.system_prompt = None
    s.last_prompt = None
    s.last_reply = None
    os.system("cls" if os.name == "nt" else "clear")
    print(BANNER)
    print("  Conversation and system prompt cleared.\n")


@command("/model")
def cmd_model(args, s):
    if args:
        s.model = args
        config.save(MODEL_ENV, args)
        print(f"  Model changed to: {args}\n")
    else:
        print(f"  Current model: {s.model}")
        print("  Change it with /model <provider/name>. See /models for the defaults.\n")


@command("/models")
def cmd_models(args, s):
    print("  Providers (* = current provider):")
    ready = configured_providers()
    for p in PROVIDERS:
        marker = "*" if p.family == provider_of(s.model) else " "
        status = "ready" if p in ready else "not set up"
        print(f"   {marker} {p.default_model:<34} {p.name} ({status})")
    print("  Set one up with /key. Any LiteLLM model string works with /model.")
    print("  See docs.litellm.ai/docs/providers\n")


@command("/cwd")
def cmd_cwd(args, s):
    if not args:
        print(f"  {os.getcwd()}\n")
    elif os.path.isdir(args):
        os.chdir(args)
        print(f"  Changed to: {os.getcwd()}\n")
    else:
        print(f"  [Error] Not a valid directory: {args}\n")


@command("/ls")
def cmd_ls(args, s):
    cwd = os.getcwd()
    print(f"\n  {cwd}\n")
    try:
        if args:
            safe_args = os.path.normpath(args)
            if safe_args == ".." or safe_args.startswith(".." + os.sep) or os.path.isabs(safe_args):
                print("  [Error] Path must be relative and within the current directory.\n")
                return
            entries = sorted(os.path.basename(e) for e in glob.glob(os.path.join(cwd, safe_args)))
        else:
            entries = sorted(os.listdir(cwd))

        for entry in entries:
            full = os.path.join(cwd, entry)
            if os.path.isdir(full):
                print(f"    [DIR]  {entry}/")
            else:
                try:
                    size = os.path.getsize(full)
                except OSError:
                    size = 0
                print(f"    {_format_size(size):>8}  {entry}")
        if not entries:
            print("    (empty)")
        print()
    except OSError as e:
        print(f"  [Error] {e}\n")


@command("/tree")
def cmd_tree(args, s, max_depth=3):
    cwd = os.getcwd()
    print(f"\n  {cwd}\n")
    skip = {".git", "__pycache__", "node_modules", ".env_cli", "venv", ".venv"}

    def _tree(path, prefix, depth):
        if depth > max_depth:
            return
        try:
            entries = sorted(os.listdir(path))
        except OSError:
            return
        entries = [e for e in entries if e not in skip and not e.startswith(".")]
        for i, entry in enumerate(entries):
            full = os.path.join(path, entry)
            last = i == len(entries) - 1
            connector = "└── " if last else "├── "
            if os.path.isdir(full):
                print(f"  {prefix}{connector}{entry}/")
                _tree(full, prefix + ("    " if last else "│   "), depth + 1)
            else:
                print(f"  {prefix}{connector}{entry}")

    _tree(cwd, "", 0)
    print()


def read_file_text(path: str, limit: int) -> str:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read(limit)


@command("/read")
def cmd_read(args, s):
    if not args:
        print("  Usage: /read <filename>\n")
        return
    cwd = os.getcwd()
    if "*" in args or "?" in args:
        matches = glob.glob(os.path.join(cwd, args))
    else:
        target = os.path.join(cwd, args) if not os.path.isabs(args) else args
        matches = [target]
    matches = [m for m in matches if os.path.isfile(m)]

    if not matches:
        print(f"  [Error] No files found: {args}\n")
        return

    loaded = 0
    for path in matches:
        name = os.path.relpath(path, cwd)
        try:
            with open(path, "rb") as f:
                if b"\x00" in f.read(1024):
                    print(f"  [Error] Skipping binary file: {name}\n")
                    continue
            content = read_file_text(path, MAX_READ_CHARS)
        except OSError as e:
            print(f"  [Error reading {name}] {e}")
            continue
        print(f"\n  ── {name} ──\n")
        print(content)
        s.history.append({"role": "user", "content": f"[File: {name}]\n```\n{content}\n```"})
        s.history.append({"role": "assistant", "content": f"I've read `{name}`. Ask me anything about it."})
        loaded += 1

    print(f"\n  ({loaded} file(s) loaded into chat context.)\n")


@command("/run")
def cmd_run(args, s):
    if not args:
        print("  Usage: /run <command>\n")
        return
    print()
    timed_out = threading.Event()
    try:
        proc = subprocess.Popen(
            args, shell=True, cwd=os.getcwd(),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, errors="replace", bufsize=1,
            start_new_session=os.name != "nt",  # own process group, so the whole command can be killed
        )
    except OSError as e:
        print(f"  [Error] {e}\n")
        return

    def on_timeout():
        timed_out.set()
        _kill_process_tree(proc)

    # The timer enforces the limit even while output is still streaming.
    timer = threading.Timer(RUN_TIMEOUT_SECONDS, on_timeout)
    timer.daemon = True
    timer.start()

    output_lines = []
    try:
        for line in proc.stdout:
            print(line, end="")
            output_lines.append(line)
        proc.wait()
    except KeyboardInterrupt:
        _kill_process_tree(proc)
        print("\n  [Interrupted]")
    finally:
        timer.cancel()
        proc.stdout.close()

    if timed_out.is_set():
        print(f"  [Error] Command timed out ({RUN_TIMEOUT_SECONDS}s limit).\n")
    elif proc.returncode:
        print(f"\n  Exit code: {proc.returncode}")

    # Feed the last 50 lines of output into the chat context.
    output = "".join(output_lines[-50:])
    if output.strip():
        s.history.append({"role": "user", "content": f"[Ran: {args}]\n```\n{output}\n```"})
        s.history.append({"role": "assistant", "content": f"Command `{args}` completed. I can see the output."})
    print()


@command("/system")
def cmd_system(args, s):
    if not args:
        if s.system_prompt:
            print(f"  Current system prompt: {s.system_prompt}\n")
        else:
            print("  No system prompt set. Usage: /system <prompt>\n")
        return
    if args.lower() == "clear":
        s.system_prompt = None
        print("  System prompt cleared.\n")
        return
    s.system_prompt = args
    print("  System prompt set.\n")


@command("/history")
def cmd_history(args, s):
    if not s.history:
        print("  No conversation history.\n")
        return
    print()
    for i, msg in enumerate(s.history):
        role = "You" if msg["role"] == "user" else "AI "
        print(f"  [{i + 1:>2}] {role}: {_truncate(msg['content'], 200)}")
    print()


@command("/save")
def cmd_save(args, s):
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"chat_{ts}.md"
    filepath = os.path.join(os.getcwd(), filename)
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(f"# CLI CODER Chat — {ts}\n\n")
            f.write(f"**Model:** {s.model}\n\n")
            if s.system_prompt:
                f.write(f"**System:** {s.system_prompt}\n\n")
            f.write("---\n\n")
            for msg in s.history:
                role = "**You**" if msg["role"] == "user" else "**AI**"
                f.write(f"{role}:\n\n{msg['content']}\n\n---\n\n")
        print(f"  Saved to: {filename}\n")
    except OSError as e:
        print(f"  [Error] {e}\n")


@command("/copy")
def cmd_copy(args, s):
    if not s.last_reply:
        print("  Nothing to copy yet.\n")
        return
    if copy_to_clipboard(s.last_reply):
        print("  Copied the last reply to the clipboard.\n")
    else:
        print("  [Error] No clipboard tool found (Windows: clip, macOS: pbcopy, Linux: xclip or wl-copy).\n")


@command("/retry")
def cmd_retry(args, s):
    if s.last_prompt is None:
        print("  Nothing to retry yet.\n")
        return
    tail = s.history[-2:]
    is_last_exchange = (
        len(tail) == 2
        and tail[0] == {"role": "user", "content": s.last_prompt}
        and tail[1] == {"role": "assistant", "content": s.last_reply}
    )
    if is_last_exchange:
        del s.history[-2:]
    if not chat(s.last_prompt, s) and is_last_exchange:
        s.history.extend(tail)


@command("/key")
def cmd_key(args, s):
    provider = choose_provider()
    if provider.family != provider_of(s.model):
        s.model = provider.default_model
        config.save(MODEL_ENV, s.model)
        print(f"  Model auto-set to: {s.model}\n")
    else:
        print(f"  Keeping current model: {s.model}\n")


@command("/info")
def cmd_info(args, s):
    u = s.usage
    cost_text = f"${u.cost:.4f}"
    if u.unpriced_replies:
        cost_text += f" ({u.unpriced_replies} repl{'y' if u.unpriced_replies == 1 else 'ies'} without price data)"
    rows = [
        ("Version", __version__),
        ("Model", s.model),
        ("CWD", os.getcwd()),
        ("System prompt", _truncate(s.system_prompt or "(none)", 60)),
        ("History", f"{len(s.history)} messages"),
        ("Context budget", f"{CONTEXT_BUDGET_CHARS:,} chars per request"),
        ("Tokens (session)", f"{u.prompt_tokens:,} in | {u.completion_tokens:,} out"),
        ("Cost (session)", cost_text),
    ]
    for p in PROVIDERS:
        key_value = os.environ.get(p.key_env) if p.key_env else None
        if key_value:
            rows.append((p.key_env, mask_key(key_value)))
        address = os.environ.get(p.endpoint_env) if p.endpoint_env else None
        if address:
            rows.append((p.endpoint_env, address))
    print_panel("Info", rows)


@command("/reset")
def cmd_reset(args, s):
    confirm = input("  Are you sure? This will delete your saved config. [y/N]: ").strip().lower()
    if confirm != "y":
        print("  Cancelled.\n")
        return
    if config.ENV_PATH.exists():
        os.remove(config.ENV_PATH)
    s.history.clear()
    print("  Config wiped. Restart cli_coder.cmd to set up again.\n")
    sys.exit(0)


@command("/analyze", "/analyse")
def cmd_analyze(args, s):
    cwd = os.getcwd()
    print("\n  Analyzing project... (reading context)")
    context = []

    readme_path = os.path.join(cwd, "README.md")
    if os.path.isfile(readme_path):
        context.append(f"README.md:\n{read_file_text(readme_path, 2000)}\n")

    # Prioritize core entrypoints, then read up to 3 python files for context
    py_files = glob.glob(os.path.join(cwd, "*.py"))
    py_files.sort(key=lambda x: 0 if os.path.basename(x).lower() in ("main.py", "app.py", "config.py") else 1)
    for path in py_files[:3]:
        context.append(f"{os.path.basename(path)}:\n{read_file_text(path, 2000)}\n")

    context_str = "\n".join(context)
    prompt = (
        "Based on the following files from my project, tell me what this project is about "
        "in a single short paragraph. Do not be overly verbose.\n\n" + context_str
    )
    chat(prompt, s)


@command("/code")
def cmd_code(args, s):
    if not args:
        print("  Usage: /code <describe what you want built, fixed, or analyzed>\n")
        return
    # Fast path for simple analysis requests, so they skip the slower agent loop
    if args.lower() in ANALYSIS_WORDS:
        cmd_analyze("", s)
        return
    run_code_agent(args, s)


@command("/hermes")
def cmd_hermes(args, s):
    run_hermes(args, s)


# ─── Main loop ────────────────────────────────────────────────

def repl(s: Session):
    while True:
        try:
            user_input = input("You > ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nGoodbye!")
            return

        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit"):
            print("Goodbye!")
            return

        try:
            if user_input.startswith("/"):
                handle_command(user_input, s)
            else:
                chat(user_input, s)
        except KeyboardInterrupt:
            print("\n  [Interrupted]\n")


def _use_utf8_output():
    """Box-drawing and Markdown characters crash cp1252 when output is piped or redirected on Windows."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv=None) -> int:
    _use_utf8_output()
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="CLI CODER: terminal AI assistant. Run with no arguments to start the chat.",
    )
    parser.add_argument("prompt", nargs="*", help="ask one question, print the answer and exit")
    parser.add_argument("--version", action="version", version=f"CLI CODER {__version__}")
    args = parser.parse_args(argv)

    if not args.prompt:
        os.system("cls" if os.name == "nt" else "clear")
        print(BANNER)

    try:
        model = ensure_setup()
    except (KeyboardInterrupt, EOFError):
        print("\n  Setup cancelled.")
        return 1

    s = Session(model=model)

    if args.prompt:
        return 0 if chat(" ".join(args.prompt), s) else 1

    print(f"  Model : {s.model}")
    print(f"  CWD   : {os.getcwd()}\n")
    cmd_help("", s)
    repl(s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
