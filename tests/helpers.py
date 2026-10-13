"""Shared fakes for the test suite. No test here calls a real LLM API."""
import io
import os
import tempfile
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from rich.console import Console

import main


class FakeDelta:
    def __init__(self, content):
        self.content = content


class FakeChoice:
    def __init__(self, content):
        self.delta = FakeDelta(content)


class FakeChunk:
    """Stands in for a litellm streaming chunk."""

    def __init__(self, content=None, has_choice=True):
        self.choices = [FakeChoice(content)] if has_choice else []


def fake_usage(prompt_tokens=12, completion_tokens=4):
    return SimpleNamespace(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
    )


@contextmanager
def capture():
    """Capture both print() and rich console output into one buffer."""
    buf = io.StringIO()
    test_console = Console(file=buf, width=100, color_system=None, force_terminal=False)
    with patch.object(main, "console", test_console), redirect_stdout(buf):
        yield buf


@contextmanager
def temp_dir():
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


@contextmanager
def chdir(path):
    previous = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)
