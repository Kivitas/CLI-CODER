import importlib.util
import pathlib
import sys
import types
import unittest
from unittest.mock import patch

import patch_deps

# A trimmed copy of mini-swe-agent's mini.yaml layout: the Unix examples sit inside an
# instance_template block scalar, followed by the agent settings at 2-space indent.
FIXTURE_YAML = """\
agent:
  system_template: |
    You are a helpful assistant.
  instance_template: |
    Solve the issue.

    <system_information>
    {{system}}
    </system_information>

    ## Useful command examples

    ### Create a new file:

    ```bash
    cat <<'EOF' > newfile.py
    EOF
    ```
  step_limit: 0
  cost_limit: 3.
  mode: confirm
environment:
  env:
    PAGER: cat
model:
  model_name: test-model
"""


def _yaml_module():
    try:
        import yaml
    except ImportError:
        return None
    return yaml


class PromptUserPatchTests(unittest.TestCase):
    def test_patch_is_idempotent(self):
        once, changed = patch_deps.patch_prompt_user("from upstream import x\n")
        self.assertTrue(changed)
        twice, changed_again = patch_deps.patch_prompt_user(once)
        self.assertFalse(changed_again)
        self.assertEqual(twice, once)

    def test_sessions_are_created_lazily_and_forwarded(self):
        created = []

        class FakePromptSession:
            def __init__(self, **kwargs):
                created.append(kwargs)

            def prompt(self, text, **kwargs):
                return f"answer to {text!r}"

        fake_modules = {
            "prompt_toolkit": types.ModuleType("prompt_toolkit"),
            "prompt_toolkit.formatted_text": types.ModuleType("prompt_toolkit.formatted_text"),
            "prompt_toolkit.formatted_text.html": types.ModuleType("prompt_toolkit.formatted_text.html"),
            "prompt_toolkit.history": types.ModuleType("prompt_toolkit.history"),
            "prompt_toolkit.shortcuts": types.ModuleType("prompt_toolkit.shortcuts"),
            "minisweagent": types.ModuleType("minisweagent"),
        }
        fake_modules["prompt_toolkit.formatted_text.html"].HTML = lambda text: text
        fake_modules["prompt_toolkit.history"].FileHistory = lambda path: ("history", str(path))
        fake_modules["prompt_toolkit.shortcuts"].PromptSession = FakePromptSession
        fake_modules["minisweagent"].global_config_dir = pathlib.Path("unused-config-dir")

        source, _ = patch_deps.patch_prompt_user("")
        namespace = {}
        with patch.dict(sys.modules, fake_modules):
            exec(compile(source, "prompt_user.py", "exec"), namespace)
            self.assertEqual(created, [], "no PromptSession may be created at import time")

            result = namespace["prompt_session"].prompt("")
            namespace["_multiline_prompt_session"].prompt("")

        self.assertEqual(result, "answer to ''")
        self.assertEqual(len(created), 2)
        self.assertNotIn("multiline", created[0])
        self.assertTrue(created[1]["multiline"])


class MiniYamlPatchTests(unittest.TestCase):
    def test_example_block_is_replaced(self):
        patched, changed = patch_deps.patch_mini_yaml(FIXTURE_YAML)
        self.assertTrue(changed)
        self.assertIn("You are running in Windows CMD.exe", patched)
        self.assertNotIn("cat <<'EOF'", patched)

    def test_agent_settings_after_the_block_are_kept(self):
        yaml = _yaml_module()
        if yaml is None:
            self.skipTest("PyYAML is not installed")
        patched, _ = patch_deps.patch_mini_yaml(FIXTURE_YAML)
        before = yaml.safe_load(FIXTURE_YAML)
        after = yaml.safe_load(patched)
        self.assertEqual(after["agent"]["step_limit"], 0)
        self.assertEqual(after["agent"]["cost_limit"], 3.0)
        self.assertEqual(after["agent"]["mode"], "confirm")
        self.assertEqual(set(after["agent"]), set(before["agent"]))
        self.assertEqual(after["environment"], before["environment"])
        self.assertEqual(after["model"], before["model"])

    def test_patch_is_idempotent(self):
        once, _ = patch_deps.patch_mini_yaml(FIXTURE_YAML)
        twice, changed = patch_deps.patch_mini_yaml(once)
        self.assertFalse(changed)
        self.assertEqual(twice, once)

    def test_missing_block_leaves_text_alone(self):
        text = "agent:\n  step_limit: 0\n"
        self.assertEqual(patch_deps.patch_mini_yaml(text), (text, False))


class InstalledPackageTests(unittest.TestCase):
    """Runs against the real mini-swe-agent files when the package is installed (for example in CI)."""

    def test_installed_mini_yaml_still_parses_after_patch(self):
        yaml = _yaml_module()
        spec = importlib.util.find_spec("minisweagent")
        if yaml is None or spec is None or spec.origin is None:
            self.skipTest("mini-swe-agent or PyYAML is not installed")
        path = pathlib.Path(spec.origin).parent / "config" / "mini.yaml"
        text = path.read_text(encoding="utf-8")

        if patch_deps.YAML_MARKER in text:
            # Already patched (for example a dev venv that ran patch_deps.py): patching again must be a no-op.
            again, changed = patch_deps.patch_mini_yaml(text)
            self.assertFalse(changed)
            self.assertEqual(again, text)
            self.assertIn("agent", yaml.safe_load(text))
            return

        patched, changed = patch_deps.patch_mini_yaml(text)
        self.assertTrue(changed)
        before = yaml.safe_load(text)["agent"]
        after = yaml.safe_load(patched)["agent"]
        self.assertEqual(set(after), set(before))
        self.assertEqual(after["cost_limit"], before["cost_limit"])
        self.assertEqual(after["step_limit"], before["step_limit"])


class MainFunctionTests(unittest.TestCase):
    def test_skips_on_non_windows_without_touching_the_package(self):
        with patch.object(sys, "platform", "linux"), \
                patch.object(patch_deps, "find_minisweagent_dir") as finder, \
                patch("builtins.print") as printed:
            self.assertEqual(patch_deps.main(), 0)
        finder.assert_not_called()
        self.assertIn("Windows only", printed.call_args.args[0])

    def test_reports_missing_package(self):
        # Pin the platform so this checks the missing-package path on every OS, including the Ubuntu runner.
        with patch.object(sys, "platform", "win32"), \
                patch.object(patch_deps, "find_minisweagent_dir", return_value=None), \
                patch("builtins.print") as printed:
            self.assertEqual(patch_deps.main(), 1)
        self.assertIn("not installed", printed.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
