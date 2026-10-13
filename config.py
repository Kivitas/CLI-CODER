"""Provider settings, API keys and first-run setup for CLI CODER."""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv, set_key

SCRIPT_DIR = Path(__file__).resolve().parent
ENV_PATH = SCRIPT_DIR / ".env"
MODEL_ENV = "CLI_CODER_MODEL"

# LiteLLM uses a few prefixes for the same service. These count as one provider.
_PREFIX_ALIASES = {"ollama_chat": "ollama"}


def provider_of(model: str) -> str:
    """Return the provider of a LiteLLM model string: 'anthropic/x' -> 'anthropic'."""
    prefix = model.split("/", 1)[0]
    return _PREFIX_ALIASES.get(prefix, prefix)


@dataclass(frozen=True)
class Provider:
    name: str                          # shown in menus
    default_model: str                 # LiteLLM model string used when this provider is chosen
    key_env: str | None = None         # env var holding the API key (None for local servers)
    endpoint_env: str | None = None    # env var holding the server address (local servers)
    default_endpoint: str | None = None
    setup_hint: str = ""               # extra line shown after setup

    @property
    def setup_env(self) -> str:
        """The env var whose presence means this provider is set up."""
        return self.key_env or self.endpoint_env

    @property
    def family(self) -> str:
        return provider_of(self.default_model)


PROVIDERS = [
    Provider("OpenAI", "openai/gpt-5.6", key_env="OPENAI_API_KEY"),
    Provider("Anthropic (Claude)", "anthropic/claude-sonnet-5-5", key_env="ANTHROPIC_API_KEY"),
    Provider("Google Gemini", "gemini/gemini-3.5-flash", key_env="GEMINI_API_KEY"),
    Provider("xAI (Grok)", "xai/grok-4.6", key_env="XAI_API_KEY"),
    Provider("OpenRouter", "openrouter/openrouter/auto", key_env="OPENROUTER_API_KEY",
             setup_hint="OpenRouter routes to many models. 'openrouter/auto' picks one per request."),
    Provider("Groq", "groq/openai/gpt-oss-120b", key_env="GROQ_API_KEY"),
    Provider("Mistral", "mistral/mistral-large-latest", key_env="MISTRAL_API_KEY"),
    Provider("Ollama (local)", "ollama_chat/llama3.2", endpoint_env="OLLAMA_API_BASE",
             default_endpoint="http://localhost:11434",
             setup_hint="Ollama runs on this machine: start it with 'ollama serve' and get the model "
                         "with 'ollama pull llama3.2'."),
]


def mask_key(value: str) -> str:
    """Show only the last four characters of a secret."""
    return "****" + value[-4:] if len(value) > 4 else "****"


def load_config():
    """Load .env into os.environ (existing environment variables win)."""
    if ENV_PATH.exists():
        load_dotenv(ENV_PATH)


def save(key: str, value: str):
    """Save a key=value to .env and to the current process."""
    ENV_PATH.touch(exist_ok=True)
    set_key(str(ENV_PATH), key, value)
    os.environ[key] = value


def configured_providers() -> list[Provider]:
    """Providers that have a key or address set, in menu order."""
    return [p for p in PROVIDERS if os.environ.get(p.setup_env)]


def choose_provider() -> Provider:
    """Ask which provider to use, then its API key or server address. Returns the chosen provider."""
    print("\n  Choose a provider:\n")
    for num, p in enumerate(PROVIDERS, start=1):
        label = p.key_env or "no key needed"
        print(f"    {num}. {p.name}  ({label})")

    while True:
        choice = input(f"\n  Choice [1-{len(PROVIDERS)}]: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(PROVIDERS):
            break
        print(f"  Please enter a number from 1 to {len(PROVIDERS)}.")
    provider = PROVIDERS[int(choice) - 1]

    if provider.key_env:
        api_key = ""
        while not api_key:
            api_key = input(f"  Paste your {provider.key_env}: ").strip()
        save(provider.key_env, api_key)
    else:
        address = input(f"  {provider.name} address [{provider.default_endpoint}]: ").strip()
        save(provider.endpoint_env, address or provider.default_endpoint)

    print("  Saved!")
    if provider.setup_hint:
        print(f"  {provider.setup_hint}")
    print()
    return provider


def ensure_setup() -> str:
    """Make sure a provider is set up and a model is chosen. Returns the active model."""
    load_config()
    chosen = None
    if not configured_providers():
        chosen = choose_provider()

    model = os.environ.get(MODEL_ENV)
    if not model:
        provider = chosen or configured_providers()[0]
        model = provider.default_model
        save(MODEL_ENV, model)
    return model
