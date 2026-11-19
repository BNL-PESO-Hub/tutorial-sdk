"""Shared pytest fixtures."""

import pytest


@pytest.fixture(autouse=True)
def _isolate_backend_env(monkeypatch):
    """Keep a developer's ``TUTORIAL_SDK_BACKEND`` out of tests.

    Backend selection reads this variable, so a value set in the
    shell would otherwise change which backend tests exercise.
    Tests that need it set it explicitly.
    """
    monkeypatch.delenv("TUTORIAL_SDK_BACKEND", raising=False)
