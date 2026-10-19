import json
import re
from pathlib import Path

import httpx
import pytest

from hermes_plugins import tripo as plugin
from hermes_plugins.tripo import tools

PLUGIN_DIR = Path(__file__).resolve().parent.parent


def call(tool, **args):
    return json.loads(tools.HANDLERS[tool](args))


# -- spend gate -----------------------------------------------------------------------------------


def test_gate_asks_for_paid_calls_with_the_estimated_cost():
    directive = plugin.spend_gate(
        tool_name="tripo_generate_model", args={"image": "https://e.x/a.png", "texture_quality": "detailed"}
    )
    assert directive["action"] == "approve"
    assert "about 50 credits" in directive["message"]
    assert directive["rule_key"] == "tripo:tripo_generate_model"


def test_gate_ignores_free_calls_and_other_tools():
    for name in ("tripo_task", "tripo_download", "tripo_balance", "tripo_rig_check", "terminal"):
        assert plugin.spend_gate(tool_name=name, args={"task_id": "task_1", "input": "task_1"}) is None


def test_gate_leaves_invalid_calls_to_the_handler(tripo):
    # Nothing is priced, and the handler refuses before any request goes out.
    assert plugin.spend_gate(tool_name="tripo_generate_model", args={}) is None
    result = call("tripo_generate_model")
    assert result["ok"] is False and "no credits were spent" in result["error"]
    assert tripo.requests == []


# -- creates --------------------------------------------------------------------------------------


def test_generate_sends_bearer_key_and_returns_task(tripo):
    tripo.on("POST", "/v3/generation/text-to-model", {"task_id": "task_abc"})
    result = call("tripo_generate_model", prompt="a chest")
    assert result["ok"] and result["task_id"] == "task_abc"
    assert result["estimated_cost"] == "about 20 credits"
    request = tripo.requests[0]
    assert request.headers["authorization"] == "Bearer tsk_test"
    assert tripo.bodies("/v3/generation/text-to-model")[0]["prompt"] == "a chest"


def test_local_image_is_uploaded_then_referenced(tripo, tmp_path):
    photo = tmp_path / "mug.png"
    photo.write_bytes(b"\x89PNG fake")
    tripo.on("POST", "/v3/files", {"file_token": "file_123"})
    tripo.on("POST", "/v3/generation/image-to-model", {"task_id": "task_img"})
    result = call("tripo_generate_model", image=str(photo))
    assert result["task_id"] == "task_img"
    assert b'name="file"' in tripo.requests[0].content
    assert tripo.bodies("/v3/generation/image-to-model")[0]["input"] == "file_123"


def test_relative_and_missing_paths_are_refused(tripo, tmp_path):
    assert "absolute" in call("tripo_generate_model", image="photos/mug.png")["error"]
    assert "not found" in call("tripo_generate_model", image=str(tmp_path / "nope.png"))["error"]
    assert tripo.requests == []


def test_api_error_surfaces_code_and_hint(tripo):
    tripo.on(
        "POST",
        "/v3/models/convert",
        httpx.Response(403, json={"code": 2010, "message": "Insufficient credits"}),
    )
    result = call("tripo_convert", input="task_1", format="FBX")
    assert result["ok"] is False and result["code"] == 2010
    assert "out of credits" in result["error"]


def test_creates_are_never_retried(tripo):
    tripo.on("POST", "/v3/animations/rig", httpx.ConnectError("boom"))
    result = call("tripo_rig", input="task_1")
    assert "may have been created anyway" in result["error"]
    assert len(tripo.requests) == 1


def test_missing_key_is_explained(monkeypatch):
    monkeypatch.delenv("TRIPO_API_KEY", raising=False)
    result = call("tripo_balance")
    assert result["ok"] is False and "TRIPO_API_KEY" in result["error"]


# -- tasks, balance, downloads -------------------------------------------------------------------


def test_task_waits_until_success_and_hides_long_urls(tripo):
    running = {"task_id": "task_1", "status": "running", "progress": 40}
    done = {
        "task_id": "task_1",
        "type": "text_to_model",
        "status": "success",
        "progress": 100,
        "credits_consumed": 20.0,
        "output": {
            "model_url": "https://cdn.example/m.glb?sig=x",
            "rendered_image_url": "https://cdn.example/p.webp?sig=y",
        },
    }
    tripo.on("GET", "/v3/tasks/task_1", running, running, done)
    result = call("tripo_task", task_id="task_1", wait_seconds=60)
    assert result["status"] == "success" and result["credits_consumed"] == 20.0
    assert result["output_files"] == ["model_url", "rendered_image_url"]
    assert result["preview_url"].startswith("https://cdn.example/p.webp")
    assert "model_url" not in json.dumps(result.get("result", {}))


def test_task_get_retries_on_rate_limit(tripo):
    tripo.on(
        "GET",
        "/v3/tasks/task_1",
        httpx.Response(429, json={"code": 2000, "message": "slow down"}),
        {"task_id": "task_1", "status": "queued", "progress": 0},
    )
    result = call("tripo_task", task_id="task_1", wait=False)
    assert result["status"] == "queued" and "Do not create" in result["next"]


def test_rig_check_result_is_passed_through(tripo):
    tripo.on(
        "GET",
        "/v3/tasks/task_rc",
        {"task_id": "task_rc", "status": "success", "output": {"riggable": True, "rig_type": "biped"}},
    )
    result = call("tripo_task", task_id="task_rc", wait=False)
    assert result["result"] == {"riggable": True, "rig_type": "biped"}


def test_download_saves_model_first_without_sending_the_key(tripo, tmp_path):
    tripo.on(
        "GET",
        "/v3/tasks/task_1",
        {
            "task_id": "task_1",
            "status": "success",
            "output": {
                "rendered_image_url": "https://cdn.example/p.webp?sig=y",
                "model_url": "https://cdn.example/out/model.glb?sig=x",
            },
        },
    )
    tripo.on("GET", "/out/model.glb", httpx.Response(200, content=b"glTF" + b"\0" * 96))
    tripo.on("GET", "/p.webp", httpx.Response(200, content=b"RIFF"))
    result = call("tripo_download", task_id="task_1", save_dir=str(tmp_path), name="chest")
    assert [item["path"] for item in result["saved"]] == [
        str(tmp_path / "chest.glb"),
        str(tmp_path / "chest_rendered_image.webp"),
    ]
    assert (tmp_path / "chest.glb").stat().st_size == 100
    cdn = [r for r in tripo.requests if r.url.host == "cdn.example"]
    assert cdn and all("authorization" not in r.headers for r in cdn)
    assert not list(tmp_path.glob("*.part"))


def test_download_refuses_unfinished_tasks_and_relative_dirs(tripo):
    tripo.on("GET", "/v3/tasks/task_1", {"task_id": "task_1", "status": "running"})
    assert "not success" in call("tripo_download", task_id="task_1")["error"]
    assert "absolute" in call("tripo_download", task_id="task_1", save_dir="models")["error"]


def test_balance_and_usage(tripo):
    tripo.on("GET", "/v3/account/balance", {"balance": 1980.0, "frozen": 20.0})
    tripo.on("GET", "/v3/account/usage", [{"task_id": "task_1", "credits_consumed": 20.0}])
    result = call("tripo_balance", usage=True)
    assert result["available"] == 1980.0 and result["frozen"] == 20.0
    assert result["recent_usage"][0]["task_id"] == "task_1"


# -- registration ---------------------------------------------------------------------------------


class FakeContext:
    def __init__(self):
        self.tools, self.hooks, self.skills = {}, {}, {}

    def register_tool(self, name, toolset, schema, handler, **kwargs):
        assert schema["name"] == name and toolset == "tripo"
        self.tools[name] = kwargs

    def register_hook(self, name, fn):
        self.hooks[name] = fn

    def register_skill(self, name, path, description=""):
        self.skills[name] = Path(path)


def test_registration_matches_the_manifest():
    ctx = FakeContext()
    plugin.register(ctx)
    manifest = (PLUGIN_DIR / "plugin.yaml").read_text(encoding="utf-8")
    listed = re.findall(r"^  - (\w+)$", manifest, re.MULTILINE)
    assert sorted(listed) == sorted([*ctx.tools, *ctx.hooks])
    assert ctx.skills["workflow"].is_file()
    assert ctx.tools["tripo_balance"]["check_fn"] is None
    assert f'version: "{plugin.__version__}"' in manifest


@pytest.mark.parametrize("name", sorted(tools.SCHEMAS))
def test_schemas_are_well_formed(name):
    schema = tools.SCHEMAS[name]
    properties = schema["parameters"]["properties"]
    assert set(schema["parameters"]["required"]) <= set(properties)
    assert len(schema["description"]) < 700
