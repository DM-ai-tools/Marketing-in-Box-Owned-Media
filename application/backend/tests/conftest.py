"""Shared test setup.

The backend loads `.env` on import, so a developer's real keys are in the environment while tests
run. Manus is the one integration a test could trigger just by streaming a stage (the Pillar Page
goes to Manus whenever a key is set), and a Manus task is minutes long and costs credits. So the key
is removed for every test; a test that wants Manus sets it with `monkeypatch.setenv`.
"""
from __future__ import annotations

import pytest

from app.services import manus_client


@pytest.fixture(autouse=True)
def _no_live_manus(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in manus_client.API_KEY_ENVS:
        monkeypatch.delenv(name, raising=False)
