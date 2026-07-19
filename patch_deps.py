"""
Post-install patch for mini-swe-agent.
Fixes: prompt_toolkit crashes at import time on Windows subprocess.
"""
import site
import os

def patch():
    site_packages = site.getsitepackages()[0]
    target = os.path.join(site_packages, "minisweagent", "agents", "utils", "prompt_user.py")

    if not os.path.isfile(target):
        print("  [patch] prompt_user.py not found, skipping.")
        return

    patched_code = '''\
from prompt_toolkit.formatted_text.html import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.shortcuts import PromptSession

from minisweagent import global_config_dir

_history = FileHistory(global_config_dir / "interactive_history.txt")

# Lazy init to avoid NoConsoleScreenBufferError on Windows
prompt_session = None
_multiline_prompt_session = None

def _get_prompt_session():
    global prompt_session
    if prompt_session is None:
        prompt_session = PromptSession(history=_history)
    return prompt_session

def _get_multiline_prompt_session():
    global _multiline_prompt_session
    if _multiline_prompt_session is None:
        _multiline_prompt_session = PromptSession(history=_history, multiline=True)
    return _multiline_prompt_session


def _multiline_prompt() -> str:
    return _get_multiline_prompt_session().prompt(
        "",
        bottom_toolbar=HTML(
            "Submit message: <b fg=\\'yellow\\' bg=\\'black\\'>Esc, then Enter</b> | "
            "Navigate history: <b fg=\\'yellow\\' bg=\\'black\\'>Arrow Up/Down</b> | "
            "Search history: <b fg=\\'yellow\\' bg=\\'black\\'>Ctrl+R</b>"
        ),
    )
'''

    with open(target, "w", encoding="utf-8") as f:
        f.write(patched_code)

    # Clear cached bytecode
    cache_dir = os.path.join(os.path.dirname(target), "__pycache__")
    if os.path.isdir(cache_dir):
        import shutil
        shutil.rmtree(cache_dir)

    print("  [patch] Fixed prompt_user.py")

    # Patch mini.yaml to use Windows commands instead of Unix commands
    yaml_target = os.path.join(site_packages, "minisweagent", "config", "mini.yaml")
    if os.path.isfile(yaml_target):
        with open(yaml_target, "r", encoding="utf-8") as f:
            yaml_content = f.read()

        if "You are running in Windows CMD.exe" not in yaml_content:
            windows_instructions = """    ## Useful command examples

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
    ```"""
            
            # Replace the old instructions from "## Useful command examples" to either the next top-level key or EOF
            import re
            yaml_content = re.sub(
                r"    ## Useful command examples.*?(\n(?=\w+:)|$)", 
                windows_instructions + r"\1", 
                yaml_content, 
                flags=re.DOTALL
            )
            with open(yaml_target, "w", encoding="utf-8") as f:
                f.write(yaml_content)
            print("  [patch] Fixed mini.yaml for Windows")

if __name__ == "__main__":
    patch()
