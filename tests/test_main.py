import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import config
import main
from main import Session
from tests.helpers import FakeChunk, capture, chdir, fake_usage


class SessionTestCase(unittest.TestCase):
    """Each test gets its own Session, a temp .env and restored environment variables."""

    def setUp(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.tmp = Path(tmp)
        for p in (patch.object(config, "ENV_PATH", self.tmp / ".env"), patch.dict(os.environ)):
            p.start()
            self.addCleanup(p.stop)
        for provider in config.PROVIDERS:
            for name in (provider.key_env, provider.endpoint_env, config.MODEL_ENV):
                if name:
                    os.environ.pop(name, None)
        self.s = Session(model="openai/gpt-4o")

    def stub_completion(self, *replies, usage=None, built=True, cost=0.0021):
        """Patch litellm so each call streams the given reply text."""
        def fake_completion(model, messages, stream, timeout):
            return iter([FakeChunk(text) for text in replies])

        builder_return = SimpleNamespace(usage=usage or fake_usage()) if built else None
        patches = [
            patch("main.litellm.completion", side_effect=fake_completion),
            patch("main.litellm.stream_chunk_builder", return_value=builder_return),
            patch("main.litellm.completion_cost", return_value=cost),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)


class BuildMessagesTests(SessionTestCase):
    def test_includes_system_prompt_history_and_new_message(self):
        self.s.system_prompt = "Be brief."
        self.s.history = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
        messages, dropped = main.build_messages(self.s, "c")
        self.assertEqual([m["role"] for m in messages], ["system", "user", "assistant", "user"])
        self.assertEqual(messages[-1]["content"], "c")
        self.assertEqual(dropped, 0)

    def test_drops_oldest_messages_over_budget(self):
        self.s.history = [
            {"role": "user", "content": "x" * 20},
            {"role": "assistant", "content": "y" * 20},
            {"role": "user", "content": "z" * 20},
            {"role": "assistant", "content": "w" * 20},
        ]
        with patch.object(main, "CONTEXT_BUDGET_CHARS", 48):
            messages, dropped = main.build_messages(self.s, "hi")
        self.assertEqual(dropped, 2)
        self.assertEqual([m["content"] for m in messages], ["z" * 20, "w" * 20, "hi"])

    def test_request_always_starts_with_a_user_turn(self):
        self.s.history = [
            {"role": "user", "content": "x" * 30},
            {"role": "assistant", "content": "y" * 5},
            {"role": "user", "content": "z" * 5},
            {"role": "assistant", "content": "w" * 5},
        ]
        with patch.object(main, "CONTEXT_BUDGET_CHARS", 20):
            messages, dropped = main.build_messages(self.s, "")
        self.assertEqual(messages[0]["role"], "user")
        self.assertEqual(dropped, 2)


class ChatTests(SessionTestCase):
    def test_streams_reply_records_history_and_usage(self):
        self.stub_completion("Hello ", "**world**", usage=fake_usage(12, 4), cost=0.0021)
        with capture() as out:
            ok = main.chat("hi", self.s)

        self.assertTrue(ok)
        self.assertEqual(self.s.history, [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "Hello **world**"},
        ])
        self.assertEqual(self.s.usage.prompt_tokens, 12)
        self.assertEqual(self.s.usage.completion_tokens, 4)
        self.assertAlmostEqual(self.s.usage.cost, 0.0021)
        self.assertIn("Hello world", out.getvalue())  # Markdown is rendered, not printed raw
        self.assertIn("$0.0021", out.getvalue())

    def test_estimates_tokens_when_provider_reports_no_usage(self):
        self.stub_completion("abcdefgh", built=False, cost=None)
        with patch("main.litellm.stream_chunk_builder", side_effect=RuntimeError("no usage")), capture() as out:
            self.assertTrue(main.chat("0123456789", self.s))

        self.assertGreater(self.s.usage.prompt_tokens, 0)
        self.assertEqual(self.s.usage.completion_tokens, 2)  # 8 characters, roughly 4 per token
        self.assertEqual(self.s.usage.unpriced_replies, 1)
        self.assertIn("~", out.getvalue())

    def test_error_leaves_history_unchanged(self):
        with patch("main.litellm.completion", side_effect=RuntimeError("boom")), capture() as out:
            ok = main.chat("hi", self.s)
        self.assertFalse(ok)
        self.assertEqual(self.s.history, [])
        self.assertIn("boom", out.getvalue())

    def test_interrupt_is_reported_not_raised(self):
        with patch("main.litellm.completion", side_effect=KeyboardInterrupt), capture() as out:
            self.assertFalse(main.chat("hi", self.s))
        self.assertIn("Interrupted", out.getvalue())

    def test_context_trim_is_reported(self):
        self.s.history = [{"role": "user", "content": "x" * 500}, {"role": "assistant", "content": "y" * 500}]
        self.stub_completion("ok")
        with patch.object(main, "CONTEXT_BUDGET_CHARS", 100), capture() as out:
            main.chat("hi", self.s)
        self.assertIn("left out to fit the context budget", out.getvalue())


class RetryTests(SessionTestCase):
    def test_retry_replaces_last_exchange(self):
        self.stub_completion("first")
        with capture():
            main.chat("question", self.s)

        self.stub_completion("second")
        with capture():
            main.cmd_retry("", self.s)

        self.assertEqual(len(self.s.history), 2)
        self.assertEqual(self.s.history[-1]["content"], "second")
        self.assertEqual(self.s.last_reply, "second")

    def test_retry_restores_exchange_when_request_fails(self):
        self.stub_completion("first")
        with capture():
            main.chat("question", self.s)

        with patch("main.litellm.completion", side_effect=RuntimeError("down")), capture():
            main.cmd_retry("", self.s)

        self.assertEqual(self.s.history[-1]["content"], "first")
        self.assertEqual(len(self.s.history), 2)

    def test_retry_with_nothing_to_retry(self):
        with capture() as out:
            main.cmd_retry("", self.s)
        self.assertIn("Nothing to retry", out.getvalue())


class CommandTests(SessionTestCase):
    def test_unknown_command_is_reported(self):
        with capture() as out:
            main.handle_command("/nope", self.s)
        self.assertIn("Unknown command: /nope", out.getvalue())

    def test_command_names_are_case_insensitive(self):
        with capture() as out:
            main.handle_command("/MODELS", self.s)
        self.assertIn("Providers (* = current provider)", out.getvalue())

    def test_system_prompt_set_and_clear(self):
        with capture():
            main.handle_command("/system   Answer in French", self.s)
        self.assertEqual(self.s.system_prompt, "Answer in French")
        with capture():
            main.handle_command("/system clear", self.s)
        self.assertIsNone(self.s.system_prompt)

    def test_model_change_is_saved_to_env(self):
        with capture():
            main.handle_command("/model anthropic/claude-sonnet-5-5", self.s)
        self.assertEqual(self.s.model, "anthropic/claude-sonnet-5-5")
        self.assertIn("anthropic/claude-sonnet-5-5", config.ENV_PATH.read_text(encoding="utf-8"))

    def test_models_marks_current_provider(self):
        self.s.model = next(p.default_model for p in config.PROVIDERS if p.name == "Anthropic (Claude)")
        with capture() as out:
            main.handle_command("/models", self.s)
        marked = [line for line in out.getvalue().splitlines() if line.strip().startswith("*")]
        self.assertEqual(len(marked), 1)
        self.assertIn("anthropic/", marked[0])

    def test_models_lists_every_provider_with_setup_status(self):
        os.environ["XAI_API_KEY"] = "xai-test"
        with capture() as out:
            main.handle_command("/models", self.s)
        text = out.getvalue()
        for name in ("OpenAI", "Anthropic (Claude)", "Google Gemini", "xAI (Grok)",
                     "OpenRouter", "Groq", "Mistral", "Ollama (local)"):
            self.assertIn(name, text)
        self.assertIn("xAI (Grok) (ready)", text)
        self.assertIn("OpenRouter (not set up)", text)

    def test_key_switches_model_when_provider_changes(self):
        answers = iter(["4", "xai-test-key"])  # xAI (Grok)
        with patch("builtins.input", side_effect=lambda _prompt="": next(answers)), capture():
            main.handle_command("/key", self.s)
        self.assertEqual(self.s.model, "xai/grok-4.6")
        self.assertEqual(os.environ["CLI_CODER_MODEL"], "xai/grok-4.6")

    def test_key_keeps_model_for_same_provider(self):
        self.s.model = "openai/gpt-4.1"
        answers = iter(["1", "sk-new-key"])  # OpenAI again
        with patch("builtins.input", side_effect=lambda _prompt="": next(answers)), capture() as out:
            main.handle_command("/key", self.s)
        self.assertEqual(self.s.model, "openai/gpt-4.1")
        self.assertIn("Keeping current model", out.getvalue())

    def test_info_shows_ollama_address_unmasked(self):
        os.environ["OLLAMA_API_BASE"] = "http://localhost:11434"
        with capture() as out:
            main.handle_command("/info", self.s)
        self.assertIn("http://localhost:11434", out.getvalue())

    def test_info_masks_api_keys_and_shows_usage(self):
        os.environ["OPENAI_API_KEY"] = "sk-abcdefghijklmnop"
        self.s.usage.record(100, 50, 0.01)
        with capture() as out:
            main.handle_command("/info", self.s)
        text = out.getvalue()
        self.assertIn("****mnop", text)
        self.assertNotIn("sk-abcdef", text)
        self.assertIn("100 in", text)
        self.assertIn("$0.0100", text)

    def test_help_lists_current_commands(self):
        with capture() as out:
            main.handle_command("/help", self.s)
        for name in ("/code", "/retry", "/copy", "/models", "/run"):
            self.assertIn(name, out.getvalue())

    def test_copy_without_reply(self):
        with capture() as out:
            main.handle_command("/copy", self.s)
        self.assertIn("Nothing to copy", out.getvalue())

    def test_copy_uses_clip_on_windows(self):
        with patch.object(main.os, "name", "nt"), patch("main.subprocess.run") as run:
            self.assertTrue(main.copy_to_clipboard("héllo"))
        cmd = run.call_args.args[0]
        data = run.call_args.kwargs["input"]
        self.assertEqual(cmd, ["clip"])
        self.assertEqual(data, "héllo".encode("utf-16"))


class FileCommandTests(SessionTestCase):
    def test_ls_rejects_paths_outside_current_folder(self):
        with tempfile.TemporaryDirectory() as work, chdir(work), capture() as out:
            main.handle_command("/ls ../secrets", self.s)
        self.assertIn("must be relative", out.getvalue())

    def test_read_skips_binary_and_loads_text(self):
        with tempfile.TemporaryDirectory() as work, chdir(work):
            Path("notes.txt").write_text("hello there", encoding="utf-8")
            Path("data.bin").write_bytes(b"\x00\x01binary")
            with capture() as out:
                main.handle_command("/read *", self.s)
        self.assertIn("Skipping binary file: data.bin", out.getvalue())
        self.assertIn("1 file(s) loaded", out.getvalue())
        self.assertIn("hello there", self.s.history[0]["content"])

    def test_ls_lists_files_with_sizes(self):
        with tempfile.TemporaryDirectory() as work, chdir(work):
            Path("small.txt").write_text("x" * 10, encoding="utf-8")
            with capture() as out:
                main.handle_command("/ls", self.s)
        self.assertIn("10 B  small.txt", out.getvalue())


class RunCommandTests(SessionTestCase):
    def test_run_output_goes_into_history(self):
        with tempfile.TemporaryDirectory() as work, chdir(work), capture() as out:
            main.handle_command(f'/run "{sys.executable}" -c "print(42)"', self.s)
        self.assertIn("42", out.getvalue())
        self.assertIn("42", self.s.history[0]["content"])

    def test_run_timeout_stops_long_command(self):
        # On Windows the killed process can keep its working-directory handle for a moment after taskkill returns.
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as work, chdir(work), \
                patch.object(main, "RUN_TIMEOUT_SECONDS", 1):
            started = time.monotonic()
            with capture() as out:
                main.handle_command(f'/run "{sys.executable}" -c "import time; time.sleep(60)"', self.s)
            elapsed = time.monotonic() - started
        self.assertLess(elapsed, 30)
        self.assertIn("timed out", out.getvalue())


class ChangeTrackingTests(unittest.TestCase):
    def test_diff_detects_created_modified_and_deleted(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            (root / "a.txt").write_text("a", encoding="utf-8")
            (root / "b.txt").write_text("b", encoding="utf-8")
            before, truncated = main.snapshot_files(work)
            self.assertFalse(truncated)

            later = time.time() + 100
            os.utime(root / "a.txt", (later, later))
            (root / "b.txt").unlink()
            (root / "c.txt").write_text("c", encoding="utf-8")
            after, _ = main.snapshot_files(work)

            created, modified, deleted = main.diff_snapshots(before, after, work)
        self.assertEqual(created, ["c.txt"])
        self.assertEqual(modified, ["a.txt"])
        self.assertEqual(deleted, ["b.txt"])

    def test_snapshot_reports_truncation_for_huge_trees(self):
        with tempfile.TemporaryDirectory() as work:
            for i in range(5):
                (Path(work) / f"f{i}.txt").write_text("x", encoding="utf-8")
            with patch.object(main, "MAX_SNAPSHOT_FILES", 2):
                _, truncated = main.snapshot_files(work)
        self.assertTrue(truncated)


class CodeAgentTests(SessionTestCase):
    def test_missing_agent_is_reported(self):
        with patch("main.find_mini_exe", return_value=None), capture() as out:
            main.run_code_agent("fix it", self.s)
        self.assertIn("mini-swe-agent not found", out.getvalue())

    def test_agent_runs_with_exit_immediately_and_model(self):
        with tempfile.TemporaryDirectory() as work, chdir(work), \
                patch("main.find_mini_exe", return_value="/bin/mini"), \
                patch("main.run_with_tracking") as tracked, capture():
            main.run_code_agent("fix it", self.s)
        name, cmd, task, _ = tracked.call_args.args
        self.assertEqual(name, "Coding Agent")
        self.assertIn("--exit-immediately", cmd)
        self.assertEqual(cmd[cmd.index("--model") + 1], "openai/gpt-4o")
        self.assertEqual(task, "fix it")

    def test_analysis_words_take_the_fast_path(self):
        with patch("main.cmd_analyze") as analyze, patch("main.run_code_agent") as agent, capture():
            main.handle_command("/code summarize", self.s)
        analyze.assert_called_once()
        agent.assert_not_called()


class MainEntryTests(SessionTestCase):
    def test_one_shot_prompt_prints_answer_and_returns_zero(self):
        self.stub_completion("4")
        with patch("main.ensure_setup", return_value="openai/gpt-4o"), capture() as out:
            code = main.main(["what", "is", "2+2"])
        self.assertEqual(code, 0)
        self.assertIn("4", out.getvalue())

    def test_one_shot_failure_returns_nonzero(self):
        with patch("main.ensure_setup", return_value="openai/gpt-4o"), \
                patch("main.litellm.completion", side_effect=RuntimeError("no key")), capture():
            self.assertEqual(main.main(["hi"]), 1)

    def test_version_flag_exits_cleanly(self):
        with capture() as out, self.assertRaises(SystemExit) as raised:
            main.main(["--version"])
        self.assertEqual(raised.exception.code, 0)
        self.assertIn(main.__version__, out.getvalue())

    def test_find_mini_exe_returns_none_when_not_installed(self):
        with patch("main.shutil.which", return_value=None):
            self.assertIsNone(main.find_mini_exe())


if __name__ == "__main__":
    unittest.main()
