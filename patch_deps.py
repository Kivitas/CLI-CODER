"""
Post-install patches for mini-swe-agent (needed on Windows).

1. minisweagent/agents/utils/prompt_user.py builds prompt_toolkit sessions at
   import time. On Windows that can crash with NoConsoleScreenBufferError.
   The patched module creates each session on first use instead.
2. minisweagent/config/mini.yaml ships Unix command examples. They are replaced
   with Windows CMD examples. Only the example block changes; the agent settings
   after it (step_limit, cost_limit, mode) are kept.

Run after installing requirements:  python patch_deps.py
Both patches are idempotent, so re-running is safe.
"""
import re
import site
import sys
from pathlib import Path

PROMPT_USER_MARKER = "# CLI CODER patch: lazy prompt_toolkit sessions"
YAML_MARKER = "You are running in Windows CMD.exe"

PROMPT_USER_SOURCE = '''\
# CLI CODER patch: lazy prompt_toolkit sessions
from prompt_toolkit.formatted_text.html import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.shortcuts import PromptSession

from minisweagent import global_config_dir

_history = FileHistory(global_config_dir / "interactive_history.txt")


class _LazyPromptSession:
    """Create the real PromptSession on first use instead of at import time.

    Creating it at import time crashes on Windows when no console screen buffer
    is available. Attribute access is forwarded, so `prompt_session.prompt(...)`
    works the same as with a plain PromptSession.
    """

    def __init__(self, **kwargs):
        self._kwargs = kwargs
        self._session = None

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        if self._session is None:
            self._session = PromptSession(**self._kwargs)
        return getattr(self._session, name)


prompt_session = _LazyPromptSession(history=_history)
_multiline_prompt_session = _LazyPromptSession(history=_history, multiline=True)


def _multiline_prompt() -> str:
    return _multiline_prompt_session.prompt(
        "",
        bottom_toolbar=HTML(
            "Submit message: <b fg='yellow' bg='black'>Esc, then Enter</b> | "
            "Navigate history: <b fg='yellow' bg='black'>Arrow Up/Down</b> | "
            "Search history: <b fg='yellow' bg='black'>Ctrl+R</b>"
        ),
    )
'''

WINDOWS_EXAMPLES = """\
    ## Useful command examples

    <important>
    You are running in Windows CMD.exe. DO NOT USE Unix commands like `ls`, `cat`, `sed`, `grep`, or `touch`.
    Instead, use Windows commands like `dir`, `type`, `findstr`, `echo`, or Python scripts.
    In python one-liners from CMD, you MUST use double quotes `"` for the outer string and single quotes `'` inside.
    </important>

    ### Create or Edit a file:
    Write a short python script to do it.
    ```bash
    python -c "content = open('filename.py').read().replace('old', 'new'); open('filename.py', 'w').write(content)"
    ```

    ### View file content:
    ```bash
    type filename.py
    ```

    ### Search inside a file:
    ```bash
    findstr /N /I "search_term" filename.py
    ```

    ### View directory contents:
    ```bash
    dir
    ```

"""

# The example block runs from its heading up to the next line that is indented
# less than the block's content (4 spaces), so the keys under `agent:` are kept.
_EXAMPLES_BLOCK = re.compile(r"^    ## Useful command examples\n.*?(?=^ {0,3}\S|\Z)", re.M | re.S)


def patch_prompt_user(text: str) -> tuple[str, bool]:
    """Return (new_text, changed) for prompt_user.py."""
    if PROMPT_USER_MARKER in text:
        return text, False
    return PROMPT_USER_SOURCE, True


def patch_mini_yaml(text: str) -> tuple[str, bool]:
    """Return (new_text, changed) for mini.yaml. Does nothing if the block is missing."""
    if YAML_MARKER in text:
        return text, False
    new_text, count = _EXAMPLES_BLOCK.subn(lambda _match: WINDOWS_EXAMPLES, text, count=1)
    return new_text, count == 1


def find_minisweagent_dir() -> Path | None:
    """Locate the installed minisweagent package in the active environment."""
    for site_dir in site.getsitepackages():
        candidate = Path(site_dir) / "minisweagent"
        if candidate.is_dir():
            return candidate
    return None


def _patch_file(path: Path, patch) -> str:
    if not path.is_file():
        return f"  [patch] {path.name} not found, skipping (mini-swe-agent layout changed?)."
    new_text, changed = patch(path.read_text(encoding="utf-8"))
    if not changed:
        return f"  [patch] {path.name} already patched."
    path.write_text(new_text, encoding="utf-8")
    for pyc in (path.parent / "__pycache__").glob(f"{path.stem}*.pyc"):
        pyc.unlink()
    return f"  [patch] Patched {path.name}."


def main() -> int:
    if sys.platform != "win32":
        print("  [patch] Skipped: these patches are for Windows only.")
        return 0

    package_dir = find_minisweagent_dir()
    if package_dir is None:
        print("  [patch] mini-swe-agent is not installed. Run: pip install -r requirements.txt")
        return 1

    results = [
        _patch_file(package_dir / "agents" / "utils" / "prompt_user.py", patch_prompt_user),
        _patch_file(package_dir / "config" / "mini.yaml", patch_mini_yaml),
    ]
    for line in results:
        print(line)
    return 0 if all("skipping" not in line for line in results) else 1


if __name__ == "__main__":
    sys.exit(main())
