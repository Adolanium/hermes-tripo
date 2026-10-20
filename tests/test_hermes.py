"""Contracts against a real Hermes checkout: discovery, registration and the spend gate.

Skipped when Hermes is not importable (the plain unit-test job). scripts/test.sh runs them
inside Hermes's own test harness.
"""

import json
import shutil
from pathlib import Path

import pytest

pytest.importorskip("hermes_cli.plugins")

ROOT = Path(__file__).resolve().parents[1]
FILES = ("plugin.yaml", "__init__.py", "client.py", "plans.py", "tools.py", "viewer.py")


@pytest.fixture
def hermes(tmp_path, monkeypatch):
    """Install this checkout as a user plugin in a throwaway profile and discover it."""
    home = tmp_path / "profile"
    plugin_dir = home / "plugins" / "hermes-tripo"
    plugin_dir.mkdir(parents=True)
    for name in FILES:
        shutil.copy2(ROOT / name, plugin_dir / name)
    shutil.copytree(ROOT / "skills", plugin_dir / "skills")
    # JSON is valid YAML.
    (home / "config.yaml").write_text(
        json.dumps({"plugins": {"enabled": ["hermes-tripo"]}}), encoding="utf-8"
    )
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_BUNDLED_PLUGINS", str(tmp_path / "empty-bundled"))
    monkeypatch.setenv("TRIPO_API_KEY", "tsk_contract_test")
    for name in ("HERMES_INTERACTIVE", "HERMES_GATEWAY_SESSION", "HERMES_YOLO_MODE"):
        monkeypatch.delenv(name, raising=False)

    from hermes_cli.plugins import _ensure_plugins_discovered

    _ensure_plugins_discovered(force=True)
    import model_tools

    return model_tools


def test_tools_register_through_discovery(hermes):
    from tools.registry import registry

    for name in ("tripo_generate_model", "tripo_rig", "tripo_task", "tripo_download", "tripo_balance"):
        assert registry.get_entry(name) is not None, name


def test_paid_call_is_refused_without_an_approver_and_names_the_cost(hermes, monkeypatch):
    import httpx

    sent = []
    monkeypatch.setattr(httpx.Client, "send", lambda self, request, **kw: sent.append(request))
    out = hermes.handle_function_call(
        "tripo_generate_model",
        {"prompt": "a wooden stool", "texture_quality": "detailed"},
        task_id="contract",
    )
    assert "BLOCKED" in out and "about 40 credits" in out
    assert sent == []


def test_desktop_sessions_get_the_viewer_line(hermes, monkeypatch):
    import httpx
    from hermes_cli.lifecycle import invoke_hook

    done = {
        "code": 0,
        "data": {
            "task_id": "task_1",
            "status": "success",
            "progress": 100,
            "output": {"model_url": "https://cdn.example/m.glb"},
        },
    }
    monkeypatch.setattr(
        httpx.Client, "send", lambda self, request, **kw: httpx.Response(200, json=done, request=request)
    )
    invoke_hook(
        "pre_llm_call", session_id="desk-1", platform="desktop", user_message="", conversation_history=[]
    )
    invoke_hook("pre_llm_call", session_id="cli-1", platform="cli", user_message="", conversation_history=[])
    desk = hermes.handle_function_call(
        "tripo_task", {"task_id": "task_1", "wait": False}, session_id="desk-1"
    )
    cli = hermes.handle_function_call("tripo_task", {"task_id": "task_1", "wait": False}, session_id="cli-1")
    assert '::tripo-model{task=\\"task_1\\"}' in desk
    assert "tripo-model" not in cli
