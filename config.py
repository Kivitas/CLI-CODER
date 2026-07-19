import os
from pathlib import Path
from dotenv import load_dotenv, set_key

SCRIPT_DIR = Path(__file__).parent
ENV_PATH = SCRIPT_DIR / ".env"

PROVIDERS = {
    "1": ("OpenAI",    "OPENAI_API_KEY"),
    "2": ("Anthropic", "ANTHROPIC_API_KEY"),
    "3": ("Gemini",    "GEMINI_API_KEY"),
    "4": ("Groq",      "GROQ_API_KEY"),
    "5": ("Mistral",   "MISTRAL_API_KEY"),
}

DEFAULT_MODELS = {
    "GEMINI_API_KEY":    "gemini/gemini-3.5-flash",
    "OPENAI_API_KEY":    "openai/gpt-4o",
    "ANTHROPIC_API_KEY": "anthropic/claude-sonnet-4-20250514",
    "GROQ_API_KEY":      "groq/llama3-70b-8192",
    "MISTRAL_API_KEY":   "mistral/mistral-large-latest",
}

SUPPORTED_KEYS = list(DEFAULT_MODELS.keys())


def load_config():
    """Load .env into os.environ."""
    if ENV_PATH.exists():
        load_dotenv(ENV_PATH)


def save(key: str, value: str):
    """Save a key=value to .env and current process."""
    if not ENV_PATH.exists():
        ENV_PATH.touch()
    set_key(str(ENV_PATH), key, value)
    os.environ[key] = value


def setup_api_key():
    """Interactive prompt to pick a provider and paste an API key."""
    print("\n  No API key found. Choose a provider:\n")
    for num, (name, env_var) in PROVIDERS.items():
        print(f"    {num}. {name}  ({env_var})")

    choice = ""
    while choice not in PROVIDERS:
        choice = input("\n  Choice [1-5]: ").strip()

    _, key_name = PROVIDERS[choice]
    api_key = input(f"  Paste your {key_name}: ").strip()
    save(key_name, api_key)
    print(f"  Saved!\n")


def ensure_setup():
    """Make sure API key + model are configured."""
    load_config()
    if not any(os.environ.get(k) for k in SUPPORTED_KEYS):
        setup_api_key()
    if not os.environ.get("CLI_CODER_MODEL"):
        # Auto-select default model based on provider
        for key_name, model in DEFAULT_MODELS.items():
            if os.environ.get(key_name):
                save("CLI_CODER_MODEL", model)
                break

