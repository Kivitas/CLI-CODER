"""
CLI CODER — A terminal AI assistant.
  Normal chat  → direct LLM API call (via litellm) with streaming
  Coding tasks → /code <task> routes to mini-swe-agent with full context
"""
import os
import sys
import subprocess
import datetime
import glob
import litellm
from config import ensure_setup

BANNER = r"""
   ____ _     ___        ____ ___  ____  _____ ____
  / ___| |   |_ _|      / ___/ _ \|  _ \| ____|  _ \
 | |   | |    | | _____| |  | | | | | | |  _| | |_) |
 | |___| |___ | ||_____| |__| |_| | |_| | |___|  _ <
  \____|_____|___|      \____\___/|____/|_____|_| \_\

         Made by Kivitas : github.com/kivitas
"""

HELP_TEXT = """
  ╭──────────────────────────────────────────────╮
  │               COMMANDS                       │
  ├──────────────────────────────────────────────┤
  │                                              │
  │  Coding                                      │
  │    /code <task>    Start coding agent         │
  │    /hermes [task]  Start Hermes Agent         │
  │    /run <cmd>      Run a shell command        │
  │                                              │
  │  Files                                       │
  │    /ls [pattern]   List files (supports glob) │
  │    /read <file>    Read file into chat        │
  │    /cwd <path>     Change working directory   │
  │    /tree           Show directory tree        │
  │    /analyze        Summarize the project      │
  │                                              │
  │  Chat                                        │
  │    /system <text>  Set system prompt          │
  │    /history        Show conversation log      │
  │    /save           Save chat to .md file      │
  │    /clear          Clear chat history         │
  │                                              │
  │  Config                                      │
  │    /model <name>   Change AI model            │
  │    /key            Change API key / provider  │
  │    /info           Show current settings      │
  │    /reset          Wipe config & start fresh  │
  │                                              │
  │  /help             Show this help             │
  │  exit              Quit                       │
  ╰──────────────────────────────────────────────╯
"""

# ─── State ────────────────────────────────────────────────────

history = []
system_prompt = None
total_tokens = 0


# ─── Chat (streaming) ────────────────────────────────────────

def chat(user_msg: str, model: str) -> str:
    """Send a message to the LLM and stream the response."""
    global total_tokens
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.extend(history)
    messages.append({"role": "user", "content": user_msg})

    try:
        response = litellm.completion(model=model, messages=messages, stream=True)
        print("\nAI > ", end="", flush=True)
        full_reply = []
        for chunk in response:
            delta = chunk.choices[0].delta
            if delta and delta.content:
                print(delta.content, end="", flush=True)
                full_reply.append(delta.content)
        reply = "".join(full_reply)
        print("\n")

        history.append({"role": "user", "content": user_msg})
        history.append({"role": "assistant", "content": reply})
        return None  # already printed
    except Exception as e:
        print(f"\n[Error] {e}\n")
        return None


# ─── SWE Agent ────────────────────────────────────────────────

def _build_agent_context() -> str:
    """Build context from recent chat history to give the coding agent."""
    if not history:
        return ""
    # Take last 6 messages max
    recent = history[-6:]
    lines = ["\n## Recent conversation context:"]
    for msg in recent:
        role = "User" if msg["role"] == "user" else "Assistant"
        content = msg["content"]
        if len(content) > 500:
            content = content[:500] + "..."
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


def _snapshot_files(directory: str) -> dict:
    """Take a snapshot of file modification times."""
    snap = {}
    try:
        for root, dirs, files in os.walk(directory):
            # Skip hidden dirs and common noise
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ('node_modules', '__pycache__', 'venv')]
            for f in files:
                fp = os.path.join(root, f)
                try:
                    snap[fp] = os.path.getmtime(fp)
                except OSError:
                    pass
    except OSError:
        pass
    return snap


def _diff_snapshots(before: dict, after: dict, directory: str) -> tuple:
    """Compare snapshots and return (created, modified, deleted) file lists."""
    created = []
    modified = []
    deleted = []
    for fp, mtime in after.items():
        if fp not in before:
            created.append(os.path.relpath(fp, directory))
        elif mtime != before[fp]:
            modified.append(os.path.relpath(fp, directory))
    for fp in before:
        if fp not in after:
            deleted.append(os.path.relpath(fp, directory))
    return created, modified, deleted


def run_code_agent(task: str, model: str):
    """Launch mini-swe-agent for a coding task with context and change tracking."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    mini_exe = os.path.join(script_dir, ".env_cli", "Scripts", "mini.exe")

    if not os.path.isfile(mini_exe):
        print("  [Error] mini-swe-agent not found. Re-run cli_coder.cmd to reinstall.")
        return

    cwd = os.getcwd()

    # Build enriched task with context
    context = _build_agent_context()
    full_task = f"{task}\n\nWorking directory: {cwd}{context}"

    print(f"\n  ╭─ Coding Agent ─────────────────────────╮")
    print(f"  │  Task : {task[:38]:<38} │")
    print(f"  │  CWD  : {cwd[:38]:<38} │")
    print(f"  │  Model: {model[:38]:<38} │")
    print(f"  ╰─────────────────────────────────────────╯\n")

    # Snapshot files before agent runs
    before = _snapshot_files(cwd)

    cmd = [mini_exe, "-t", full_task, "-y", "--model", model]

    try:
        subprocess.run(cmd, env=os.environ, cwd=cwd)
    except KeyboardInterrupt:
        print("\n  [Interrupted]")
    except Exception as e:
        print(f"  [Error] {e}")

    # Snapshot after and show diff
    after = _snapshot_files(cwd)
    created, modified, deleted = _diff_snapshots(before, after, cwd)

    if created or modified or deleted:
        print(f"\n  ╭─ Changes ───────────────────────────────╮")
        for f in created:
            print(f"  │  + {f:<40}│")
        for f in modified:
            print(f"  │  ~ {f:<40}│")
        for f in deleted:
            print(f"  │  - {f:<40}│")
        print(f"  ╰──────────────────────────────────────────╯")

        # Record in chat history
        summary = []
        if created: summary.append(f"Created: {', '.join(created)}")
        if modified: summary.append(f"Modified: {', '.join(modified)}")
        if deleted: summary.append(f"Deleted: {', '.join(deleted)}")
        history.append({"role": "user", "content": f"[Coding agent completed task: {task}]"})
        history.append({"role": "assistant", "content": f"Coding agent finished. {'; '.join(summary)}"})
    else:
        print("\n  No file changes detected.")

    print()


def cmd_hermes(task: str):
    """Launch Hermes Agent or install it if missing."""
    import platform
    if platform.system() != "Windows":
        print("  [Error] /hermes auto-install is currently only supported on Windows.")
        return

    localappdata = os.environ.get("LOCALAPPDATA")
    if not localappdata:
        print("  [Error] LOCALAPPDATA environment variable not found.")
        return

    hermes_exe = os.path.join(localappdata, "hermes", "bin", "hermes.exe")
    
    if not os.path.isfile(hermes_exe):
        print("  [Info] Hermes Agent is not installed. Installing it now (this may take a few minutes)...")
        install_cmd = "powershell -Command \"iex (irm https://hermes-agent.nousresearch.com/install.ps1)\""
        try:
            subprocess.run(install_cmd, shell=True, check=True)
            print("  [Success] Hermes Agent installed successfully!")
        except subprocess.CalledProcessError:
            print("  [Error] Failed to install Hermes Agent.")
            return

    cwd = os.getcwd()
    print(f"\n  ╭─ Hermes Agent ─────────────────────────╮")
    if task:
        print(f"  │  Task : {task[:38]:<38} │")
    print(f"  │  CWD  : {cwd[:38]:<38} │")
    print(f"  ╰─────────────────────────────────────────╯\n")

    cmd = [hermes_exe]
    if task:
        cmd.append(task)

    try:
        subprocess.run(cmd, env=os.environ, cwd=cwd)
    except KeyboardInterrupt:
        print("\n  [Interrupted]")
    except Exception as e:
        print(f"  [Error] {e}")

    print()


def cmd_analyze(model: str):
    """Quickly summarize the codebase without launching the SWE agent."""
    cwd = os.getcwd()
    print("\n  Analyzing project... (reading context)")
    context = []
    
    # Read README.md if it exists
    readme_path = os.path.join(cwd, "README.md")
    if os.path.isfile(readme_path):
        with open(readme_path, "r", encoding="utf-8", errors="ignore") as f:
            context.append(f"README.md:\n{f.read()[:2000]}\n")
    
    # Prioritize core entrypoints, then read up to 3 python files for context
    py_files = glob.glob(os.path.join(cwd, "*.py"))
    py_files.sort(key=lambda x: 0 if os.path.basename(x).lower() in ("main.py", "app.py", "config.py") else 1)
    
    for pf in py_files[:3]:
        name = os.path.basename(pf)
        with open(pf, "r", encoding="utf-8", errors="ignore") as f:
            context.append(f"{name}:\n{f.read()[:2000]}\n")
            
    context_str = "\n".join(context)
    prompt = f"Based on the following files from my project, tell me what this project is about in a single short paragraph. Do not be overly verbose.\n\n{context_str}"
    
    # Call chat() to stream the response naturally
    chat(prompt, model)


# ─── Command handlers ────────────────────────────────────────

def cmd_help():
    print(HELP_TEXT)

def cmd_clear():
    global system_prompt
    history.clear()
    system_prompt = None
    os.system("cls" if os.name == "nt" else "clear")
    print(BANNER)
    print("  Conversation and system prompt cleared.\n")

def cmd_model(args: str, model: str) -> str:
    if args:
        from config import save
        save("CLI_CODER_MODEL", args)
        print(f"  Model changed to: {args}\n")
        return args
    else:
        print(f"  Current model: {model}\n")
        return model

def cmd_cwd(args: str):
    if not args:
        print(f"  {os.getcwd()}\n")
    elif os.path.isdir(args):
        os.chdir(args)
        print(f"  Changed to: {os.getcwd()}\n")
    else:
        print(f"  [Error] Not a valid directory: {args}\n")

def cmd_ls(args: str):
    cwd = os.getcwd()
    print(f"\n  {cwd}\n")
    try:
        if args:
            safe_args = os.path.normpath(args)
            if safe_args.startswith("..") or os.path.isabs(safe_args):
                print("  [Error] Path must be relative and within the current directory.\n")
                return
            # Glob pattern
            entries = sorted(glob.glob(os.path.join(cwd, safe_args)))
            entries = [os.path.basename(e) for e in entries]
        else:
            entries = sorted(os.listdir(cwd))
        for entry in entries:
            full = os.path.join(cwd, entry)
            if os.path.isdir(full):
                print(f"    [DIR]  {entry}/")
            else:
                size = os.path.getsize(full)
                if size < 1024:
                    s = f"{size} B"
                elif size < 1024 * 1024:
                    s = f"{size // 1024} KB"
                else:
                    s = f"{size // (1024 * 1024)} MB"
                print(f"    {s:>8}  {entry}")
        if not entries:
            print("    (empty)")
        print()
    except Exception as e:
        print(f"  [Error] {e}\n")

def cmd_tree(max_depth=3):
    """Show directory tree of CWD."""
    cwd = os.getcwd()
    print(f"\n  {cwd}\n")
    skip = {'.git', '__pycache__', 'node_modules', '.env_cli', 'venv', '.venv'}
    def _tree(path, prefix, depth):
        if depth > max_depth:
            return
        try:
            entries = sorted(os.listdir(path))
        except PermissionError:
            return
        entries = [e for e in entries if e not in skip and not e.startswith('.')]
        for i, entry in enumerate(entries):
            full = os.path.join(path, entry)
            connector = "└── " if i == len(entries) - 1 else "├── "
            if os.path.isdir(full):
                print(f"  {prefix}{connector}{entry}/")
                ext = "    " if i == len(entries) - 1 else "│   "
                _tree(full, prefix + ext, depth + 1)
            else:
                print(f"  {prefix}{connector}{entry}")
    _tree(cwd, "", 0)
    print()

def cmd_read(args: str):
    if not args:
        print("  Usage: /read <filename>\n")
        return
    # Support multiple files via glob
    cwd = os.getcwd()
    if '*' in args or '?' in args:
        matches = glob.glob(os.path.join(cwd, args))
    else:
        target = os.path.join(cwd, args) if not os.path.isabs(args) else args
        matches = [target] if os.path.isfile(target) else []

    if not matches:
        print(f"  [Error] No files found: {args}\n")
        return

    for path in matches:
        name = os.path.relpath(path, cwd)
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(50000)  # Cap at 50KB
            print(f"\n  ── {name} ──\n")
            print(content)
            history.append({"role": "user", "content": f"[File: {name}]\n```\n{content}\n```"})
            history.append({"role": "assistant", "content": f"I've read `{name}`. Ask me anything about it."})
        except Exception as e:
            print(f"  [Error reading {name}] {e}")

    print(f"\n  ({len(matches)} file(s) loaded into chat context.)\n")

def cmd_run(args: str):
    if not args:
        print("  Usage: /run <command>\n")
        return
    print()
    try:
        # Stream output in real time
        proc = subprocess.Popen(
            args, shell=True, cwd=os.getcwd(),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1
        )
        output_lines = []
        for line in proc.stdout:
            print(line, end="")
            output_lines.append(line)
        proc.wait(timeout=120)

        if proc.returncode != 0:
            print(f"\n  Exit code: {proc.returncode}")

        # Feed output into chat context
        output = "".join(output_lines[-50:])  # Last 50 lines max
        if output.strip():
            history.append({"role": "user", "content": f"[Ran: {args}]\n```\n{output}\n```"})
            history.append({"role": "assistant", "content": f"Command `{args}` completed. I can see the output."})

    except subprocess.TimeoutExpired:
        if os.name == 'nt':
            subprocess.run(f"taskkill /F /T /PID {proc.pid}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            proc.kill()
        print("  [Error] Command timed out (120s limit).\n")
    except Exception as e:
        print(f"  [Error] {e}")
    print()

def cmd_system(args: str):
    global system_prompt
    if not args:
        if system_prompt:
            print(f"  Current system prompt: {system_prompt}\n")
        else:
            print("  No system prompt set. Usage: /system <prompt>\n")
        return
    system_prompt = args
    print(f"  System prompt set.\n")

def cmd_history():
    if not history:
        print("  No conversation history.\n")
        return
    print()
    for i, msg in enumerate(history):
        role = "You" if msg["role"] == "user" else "AI "
        content = msg["content"]
        if len(content) > 200:
            content = content[:200] + "..."
        print(f"  [{i+1:>2}] {role}: {content}")
    print()

def cmd_save():
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"chat_{ts}.md"
    filepath = os.path.join(os.getcwd(), filename)
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(f"# CLI CODER Chat — {ts}\n\n")
            f.write(f"**Model:** {os.environ.get('CLI_CODER_MODEL', 'unknown')}\n\n")
            if system_prompt:
                f.write(f"**System:** {system_prompt}\n\n")
            f.write("---\n\n")
            for msg in history:
                role = "**You**" if msg["role"] == "user" else "**AI**"
                f.write(f"{role}:\n\n{msg['content']}\n\n---\n\n")
        print(f"  Saved to: {filename}\n")
    except Exception as e:
        print(f"  [Error] {e}\n")

def cmd_key():
    from config import setup_api_key, save as cfg_save
    setup_api_key()
    from config import DEFAULT_MODELS
    for key_name, model_name in DEFAULT_MODELS.items():
        if os.environ.get(key_name):
            cfg_save("CLI_CODER_MODEL", model_name)
            print(f"  Model auto-set to: {model_name}\n")
            return model_name
    return None

def cmd_info(model: str):
    print(f"\n  ╭─ Info ──────────────────────────────────╮")
    print(f"  │  Model   : {model:<30}│")
    print(f"  │  CWD     : {os.getcwd()[:30]:<30}│")
    sp = (system_prompt[:27] + "...") if system_prompt and len(system_prompt) > 30 else (system_prompt or "(none)")
    print(f"  │  System  : {sp:<30}│")
    print(f"  │  History : {len(history)} messages{'':<20}│")
    print(f"  │  Tokens  : {total_tokens:<30}│")
    from config import SUPPORTED_KEYS
    for k in SUPPORTED_KEYS:
        val = os.environ.get(k)
        if val:
            masked = val[:6] + "..." + val[-4:]
            print(f"  │  Key     : {masked:<30}│")
    print(f"  ╰──────────────────────────────────────────╯\n")

def cmd_reset():
    from config import ENV_PATH
    confirm = input("  Are you sure? This will delete your saved config. [y/N]: ").strip().lower()
    if confirm == "y":
        if ENV_PATH.exists():
            os.remove(ENV_PATH)
        history.clear()
        print("  Config wiped. Restart cli_coder.cmd to set up again.\n")
        sys.exit(0)
    else:
        print("  Cancelled.\n")


# ─── Main loop ────────────────────────────────────────────────

def main():
    os.system("cls" if os.name == "nt" else "clear")
    print(BANNER)

    ensure_setup()

    model = os.environ.get("CLI_CODER_MODEL", "gemini/gemini-3.5-flash")
    print(f"  Model : {model}")
    print(f"  CWD   : {os.getcwd()}")
    print(HELP_TEXT)

    while True:
        try:
            user_input = input("You > ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nGoodbye!")
            break

        if not user_input:
            continue

        low = user_input.lower()

        # --- Commands ---
        if low in ("exit", "quit"):
            print("Goodbye!")
            break

        if low == "/help":
            cmd_help(); continue

        if low == "/clear":
            cmd_clear(); continue

        if low.startswith("/model"):
            model = cmd_model(user_input[6:].strip(), model); continue

        if low.startswith("/cwd"):
            cmd_cwd(user_input[4:].strip()); continue

        if low.startswith("/ls"):
            cmd_ls(user_input[3:].strip()); continue

        if low == "/tree":
            cmd_tree(); continue

        if low.startswith("/analyze") or low.startswith("/analyse"):
            cmd_analyze(model); continue

        if low.startswith("/read"):
            cmd_read(user_input[5:].strip()); continue

        if low.startswith("/run"):
            cmd_run(user_input[4:].strip()); continue

        if low.startswith("/system"):
            cmd_system(user_input[7:].strip()); continue

        if low == "/history":
            cmd_history(); continue

        if low == "/save":
            cmd_save(); continue

        if low == "/key":
            new_model = cmd_key()
            if new_model:
                model = new_model
            continue

        if low == "/info":
            cmd_info(model); continue

        if low == "/reset":
            cmd_reset(); continue

        if low.startswith("/code"):
            task = user_input[5:].strip()
            if not task:
                print("  Usage: /code <describe what you want built, fixed, or analyzed>\n")
                continue
            
            # Fast-path for simple analysis to avoid SWE agent rate limits
            if task.lower() in ("analyse", "analyze", "summarize", "explain", "summary"):
                cmd_analyze(model)
                continue
                
            run_code_agent(task, model)
            continue
            
        if low.startswith("/hermes"):
            task = user_input[7:].strip()
            cmd_hermes(task)
            continue

        if low.startswith("/"):
            print(f"  Unknown command: {user_input.split()[0]}. Type /help\n")
            continue

        # --- Normal chat (streamed) ---
        chat(user_input, model)


if __name__ == "__main__":
    main()
