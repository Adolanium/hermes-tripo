"""Load the plugin the way Hermes does: as ``hermes_plugins.tripo`` with ``__path__`` set to the
plugin directory, so the relative imports are exercised exactly as in a real install."""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import httpx
import pytest

PLUGIN_DIR = Path(__file__).resolve().parent.parent
MODULE = "hermes_plugins.tripo"


def _load():
    if MODULE in sys.modules:
        return sys.modules[MODULE]
    if "hermes_plugins" not in sys.modules:
        parent = types.ModuleType("hermes_plugins")
        parent.__path__ = []  # type: ignore[attr-defined]
        sys.modules["hermes_plugins"] = parent
    spec = importlib.util.spec_from_file_location(
        MODULE, PLUGIN_DIR / "__init__.py", submodule_search_locations=[str(PLUGIN_DIR)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE] = module
    spec.loader.exec_module(module)
    return module


plugin = _load()


class FakeTripo:
    """An in-memory Tripo: records requests, answers from ``routes``."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.routes: dict[tuple[str, str], list] = {}

    def on(self, method: str, path: str, *responses) -> None:
        self.routes[(method, path)] = list(responses)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        key = (request.method, request.url.path)
        queue = self.routes.get(key)
        if not queue:
            return httpx.Response(404, json={"code": 404, "message": f"no route {key}"})
        answer = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(answer, httpx.Response):
            return answer
        if isinstance(answer, Exception):
            raise answer
        return httpx.Response(200, json={"code": 0, "data": answer})

    def bodies(self, path: str) -> list[dict]:
        return [
            json.loads(r.content)
            for r in self.requests
            if r.url.path == path
            and r.method == "POST"
            and r.headers.get("content-type", "").startswith("application/json")
        ]


@pytest.fixture
def tripo(monkeypatch):
    fake = FakeTripo()
    client_mod = sys.modules[f"{MODULE}.client"]
    monkeypatch.setenv("TRIPO_API_KEY", "tsk_test")
    monkeypatch.delenv("TRIPO_API_BASE", raising=False)
    original = client_mod.Client.__init__

    def patched(self, transport=None, sleep=None):
        original(self, transport=httpx.MockTransport(fake.handler), sleep=lambda _s: None)

    monkeypatch.setattr(client_mod.Client, "__init__", patched)
    return fake
