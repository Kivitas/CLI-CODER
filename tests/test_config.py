import io
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import litellm

import config
from config import PROVIDERS, provider_of


def provider_named(name: str) -> config.Provider:
    return next(p for p in PROVIDERS if p.name == name)


def all_setup_env_vars():
    names = set()
    for p in PROVIDERS:
        names.update(v for v in (p.key_env, p.endpoint_env) if v)
    return names | {config.MODEL_ENV}


class ConfigTestCase(unittest.TestCase):
    """Isolates .env and provider environment variables for each test."""

    def setUp(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.env_path = Path(tmp) / ".env"
        for p in (patch.object(config, "ENV_PATH", self.env_path), patch.dict(os.environ)):
            p.start()
            self.addCleanup(p.stop)
        for name in all_setup_env_vars():
            os.environ.pop(name, None)


class HelperTests(unittest.TestCase):
    def test_provider_of_returns_prefix(self):
        self.assertEqual(provider_of("anthropic/claude-sonnet-5-5"), "anthropic")
        self.assertEqual(provider_of("gemini-only"), "gemini-only")

    def test_ollama_chat_prefix_counts_as_ollama(self):
        self.assertEqual(provider_of("ollama_chat/llama3.2"), "ollama")
        self.assertEqual(provider_of("ollama/llama3.2"), "ollama")

    def test_mask_key_shows_only_last_four(self):
        self.assertEqual(config.mask_key("sk-abcdefghijklmnop"), "****mnop")
        self.assertEqual(config.mask_key("abc"), "****")

    def test_defaults_are_current_models(self):
        self.assertEqual(provider_named("Anthropic (Claude)").default_model, "anthropic/claude-sonnet-5-5")
        self.assertEqual(provider_named("OpenAI").default_model, "openai/gpt-5.6")
        self.assertEqual(provider_named("xAI (Grok)").default_model, "xai/grok-4.6")
        self.assertEqual(provider_named("OpenRouter").default_model, "openrouter/openrouter/auto")
        self.assertEqual(provider_named("Ollama (local)").default_model, "ollama_chat/llama3.2")

    def test_every_provider_is_listed_once(self):
        names = [p.name for p in PROVIDERS]
        self.assertEqual(len(names), len(set(names)))
        for expected in ("OpenAI", "Anthropic (Claude)", "Google Gemini", "xAI (Grok)",
                         "OpenRouter", "Groq", "Mistral", "Ollama (local)"):
            self.assertIn(expected, names)

    def test_every_default_model_routes_to_its_provider(self):
        """litellm must recognise each default model, so the setup menu never points at a dead route."""
        for p in PROVIDERS:
            with self.subTest(provider=p.name):
                resolved = litellm.get_llm_provider(model=p.default_model)[1]
                self.assertEqual(provider_of(resolved), p.family)


class SaveTests(ConfigTestCase):
    def test_save_writes_env_file_and_process_environment(self):
        config.save("GROQ_API_KEY", "gsk_test_value")
        self.assertEqual(os.environ["GROQ_API_KEY"], "gsk_test_value")
        self.assertIn("GROQ_API_KEY", self.env_path.read_text(encoding="utf-8"))


class ChooseProviderTests(ConfigTestCase):
    def run_choice(self, answers):
        iterator = iter(answers)
        with patch("builtins.input", side_effect=lambda _prompt="": next(iterator)), \
                redirect_stdout(io.StringIO()):
            return config.choose_provider()

    def test_reprompts_on_bad_choice_and_empty_key(self):
        provider = self.run_choice(["99", "abc", "2", "", "sk-test-value"])  # Anthropic
        self.assertEqual(provider.name, "Anthropic (Claude)")
        self.assertEqual(os.environ["ANTHROPIC_API_KEY"], "sk-test-value")

    def test_xai_key_is_saved_as_xai_api_key(self):
        provider = self.run_choice(["4", "xai-test-value"])
        self.assertEqual(provider.name, "xAI (Grok)")
        self.assertEqual(os.environ["XAI_API_KEY"], "xai-test-value")
        self.assertIn("XAI_API_KEY", self.env_path.read_text(encoding="utf-8"))

    def test_openrouter_key_is_saved_as_openrouter_api_key(self):
        provider = self.run_choice(["5", "sk-or-test"])
        self.assertEqual(provider.name, "OpenRouter")
        self.assertEqual(os.environ["OPENROUTER_API_KEY"], "sk-or-test")

    def test_ollama_asks_for_address_not_a_key(self):
        provider = self.run_choice(["8", ""])  # press Enter to accept the default address
        self.assertEqual(provider.name, "Ollama (local)")
        self.assertEqual(os.environ["OLLAMA_API_BASE"], "http://localhost:11434")
        self.assertIn("ollama pull llama3.2", provider.setup_hint)

    def test_ollama_accepts_a_custom_address(self):
        self.run_choice(["8", "http://192.168.1.20:11434"])
        self.assertEqual(os.environ["OLLAMA_API_BASE"], "http://192.168.1.20:11434")


class EnsureSetupTests(ConfigTestCase):
    def test_first_run_uses_default_model_of_chosen_provider(self):
        answers = iter(["3", "AIza-test"])  # Google Gemini
        with patch("builtins.input", side_effect=lambda _prompt="": next(answers)), redirect_stdout(io.StringIO()):
            model = config.ensure_setup()
        self.assertEqual(model, provider_named("Google Gemini").default_model)
        self.assertEqual(os.environ[config.MODEL_ENV], model)

    def test_ollama_counts_as_configured_without_prompting(self):
        os.environ["OLLAMA_API_BASE"] = "http://localhost:11434"
        with patch("builtins.input", side_effect=AssertionError("should not prompt")):
            self.assertEqual(config.ensure_setup(), "ollama_chat/llama3.2")

    def test_keeps_saved_model_without_prompting(self):
        os.environ["OPENAI_API_KEY"] = "sk-openai-test"
        os.environ[config.MODEL_ENV] = "openai/gpt-4.1"
        with patch("builtins.input", side_effect=AssertionError("should not prompt")):
            self.assertEqual(config.ensure_setup(), "openai/gpt-4.1")

    def test_picks_default_for_existing_key(self):
        os.environ["GROQ_API_KEY"] = "gsk_existing"
        with patch("builtins.input", side_effect=AssertionError("should not prompt")):
            self.assertEqual(config.ensure_setup(), provider_named("Groq").default_model)

    def test_configured_providers_lists_in_menu_order(self):
        os.environ["XAI_API_KEY"] = "x"
        os.environ["OPENAI_API_KEY"] = "o"
        names = [p.name for p in config.configured_providers()]
        self.assertEqual(names, ["OpenAI", "xAI (Grok)"])


if __name__ == "__main__":
    unittest.main()
