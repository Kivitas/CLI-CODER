# Installation Guide

Welcome to CLI CODER! 

> [!NOTE]
> **A Note from the Developer:**
> You might be wondering, *"Why isn't there a convenient `cli_coder.cmd` file in this repository to just double-click and run?"* 
> 
> Well, here is the honest truth: I am simply too poor to afford a Windows Code Signing Certificate. If I included the `.cmd` file directly in the repo, Windows SmartScreen would treat it like a digital biohazard and throw up terrifying red warnings on your screen. 
> 
> So, until a wealthy client with deep pockets hires me, or kind strangers drop some donations my way, you'll have to build the launcher yourself! (It only takes 10 seconds, promise). If you do happen to have those deep pockets... hi, I'm available for work! 😉

---

## How to Build the Launcher

Since we can't distribute the `.cmd` file, you just need to create it locally on your own machine. Windows inherently trusts files that *you* create!

### Step 1: Create the file
1. Open the `CLI CODER` folder on your computer.
2. Right-click anywhere in the folder -> **New** -> **Text Document**.
3. Name it exactly `cli_coder.cmd` (make sure you delete the `.txt` extension at the end).
4. Right-click `cli_coder.cmd` and select **Edit** (or open it in Notepad).

### Step 2: Paste the code
Copy the following code block and paste it entirely into your new `cli_coder.cmd` file:

```cmd
@echo off
chcp 65001 >nul 2>&1
set PYTHONUTF8=1
setlocal

set SCRIPT_DIR=%~dp0

:: Download uv.exe if not present
if not exist "%SCRIPT_DIR%uv.exe" (
    echo Downloading uv package manager...
    powershell -Command "$ProgressPreference = 'SilentlyContinue'; Invoke-WebRequest -Uri 'https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip' -OutFile '%SCRIPT_DIR%uv.zip'; Expand-Archive -Force -Path '%SCRIPT_DIR%uv.zip' -DestinationPath '%SCRIPT_DIR%uv_extracted'; Get-ChildItem -Path '%SCRIPT_DIR%uv_extracted' -Filter 'uv.exe' -Recurse | Move-Item -Destination '%SCRIPT_DIR%' -Force; Remove-Item -Recurse -Force '%SCRIPT_DIR%uv_extracted', '%SCRIPT_DIR%uv.zip' -ErrorAction SilentlyContinue"
)

:: Create venv + install deps if needed
if not exist "%SCRIPT_DIR%.env_cli\Scripts\activate.bat" (
    echo Setting up Python environment...
    if exist "%SCRIPT_DIR%.env_cli" rmdir /s /q "%SCRIPT_DIR%.env_cli"
    "%SCRIPT_DIR%uv.exe" venv --python 3.12 "%SCRIPT_DIR%.env_cli"
    "%SCRIPT_DIR%uv.exe" pip install --python "%SCRIPT_DIR%.env_cli" -r "%SCRIPT_DIR%requirements.txt"
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to install dependencies.
        pause
        exit /b 1
    )
    :: Patch mini-swe-agent to fix Windows console bug
    "%SCRIPT_DIR%.env_cli\Scripts\python.exe" "%SCRIPT_DIR%patch_deps.py"
)

:: Run
"%SCRIPT_DIR%.env_cli\Scripts\python.exe" "%SCRIPT_DIR%main.py"

endlocal
```

### Step 3: Run it!
Save the file and close Notepad. 

Now you can simply double-click `cli_coder.cmd` anytime you want to launch the AI. It will automatically download dependencies, create an isolated Python environment, and start the CLI without any Windows Defender warnings!
