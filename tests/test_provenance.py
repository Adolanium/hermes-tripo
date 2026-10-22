import base64
import json
import re
import struct
from pathlib import Path

import httpx
import pytest

from hermes_plugins.tripo import history, settings, tools, viewer
from hermes_plugins.tripo.client import Client, TripoError

MODEL = "6f1c2a9e-3b4d-4e5f-8a7b-1c2d3e4f5a6b"
RIG = "7a2d3b0f-4c5e-4f60-9b8c-2d3e4f5a6b7c"
DANCE = "8b3e4c1a-5d6f-4a71-8c9d-3e4f5a6b7c8d"
PLUGIN_DIR = Path(__file__).resolve().parent.parent
REAL_STORED = settings._stored
RIGGED = {
    "meshes": [{}],
    "skins": [{"joints": list(range(41))}],
    "animations": [{"name": "dance_01", "channels": [{}]}],
}


def make_glb(gltf: dict) -> bytes:
    body = json.dumps(gltf).encode()
    body += b" " * (-len(body) % 4)
    return struct.pack("<4sIII4s", b"glTF", 2, 20 + len(body), len(body), b"JSON") + body


@pytest.fixture(autouse=True)
def fresh_memory(monkeypatch):
    monkeypatch.setattr(tools, "_SPENT", type(tools._SPENT)())
    monkeypatch.setattr(settings, "_stored", lambda key: None)


def _make(tripo, tool, args, task_id, session="chat-1"):
    endpoint = {
        "tripo_generate_model": "/v3/generation/text-to-model",
        "tripo_rig": "/v3/animations/rig",
        "tripo_animate": "/v3/animations/retarget",
    }[tool]
    tripo.on("POST", endpoint, {"task_id": task_id})
    return json.loads(tools.HANDLERS[tool](args, session_id=session))


# -- provenance ------------------------------------------------------------------------------------


def test_each_task_remembers_its_chat_prompt_and_parent(tripo):
    assert _make(tripo, "tripo_generate_model", {"prompt": "a chunky knight"}, MODEL)["ok"]
    assert _make(tripo, "tripo_rig", {"input": MODEL}, RIG)["ok"]
    assert _make(tripo, "tripo_animate", {"input": RIG, "animations": ["preset:biped:dance_01"]}, DANCE)["ok"]

    made = viewer.provenance(DANCE)
    assert made["known"] and made["session_id"] == "chat-1" and made["prompt"] == "a chunky knight"
    assert [step["label"] for step in made["chain"]] == ["3D model", "Rig", "Animation"]
    assert [step["task_id"] for step in made["chain"]] == [MODEL, RIG, DANCE]
    assert viewer.provenance(MODEL)["chain"][0]["tool"] == "tripo_generate_model"
    assert viewer.provenance(DANCE)["parent"] == RIG and viewer.provenance(MODEL)["parent"] is None


def test_unknown_tasks_say_so_and_bad_ids_are_refused():
    assert viewer.provenance(MODEL) == {
        "known": False,
        "task_id": MODEL,
        "tool": None,
        "parent": None,
        "session_id": None,
        "prompt": None,
        "made_at": None,
        "chain": [],
        "files": [],
    }
    with pytest.raises(TripoError):
        viewer.provenance("../../etc/passwd")


def test_chat_spend_survives_a_restart(tripo, monkeypatch):
    _make(tripo, "tripo_generate_model", {"prompt": "a chest"}, MODEL)
    _make(tripo, "tripo_rig", {"input": MODEL}, RIG)
    monkeypatch.setattr(tools, "_SPENT", type(tools._SPENT)())  # a new process forgets in-memory counts
    assert tools.spent("chat-1") == 45 and tools.spent("other-chat") == 0
    assert viewer.spend("chat-1") == {"session_spent": 45, "session_limit": 100}


def test_history_is_bounded_and_a_broken_file_is_ignored(monkeypatch):
    monkeypatch.setattr(history, "MAX_TASKS", 3)
    for index in range(5):
        history.record_task(f"task_{index}", tool="tripo_generate_model", session_id="s")
    assert list(history._read()) == ["task_2", "task_3", "task_4"]
    history._path().write_text("{not json", encoding="utf-8")
    assert history.get("task_4") is None and history.spent("s") == 0
    history.record_task("task_9", tool="tripo_rig")  # and recovers on the next write
    assert history.get("task_9")["label"] == "Rig"


def test_library_marks_tasks_made_in_a_chat(tripo):
    _make(tripo, "tripo_generate_model", {"prompt": "a potion"}, MODEL)
    tripo.on(
        "GET",
        "/v3/account/usage",
        [
            {
                "task_id": MODEL,
                "type": "text_to_model",
                "credits_consumed": 20,
                "created_at": "2026-10-22T10:00:00Z",
            },
            {
                "task_id": RIG,
                "type": "animate_rig",
                "credits_consumed": 25,
                "created_at": "2026-10-21T10:00:00Z",
            },
        ],
    )
    with Client() as client:
        items = viewer.library(client)["items"]
    assert items[0]["from_chat"] is True and "from_chat" not in items[1]


# -- local models ----------------------------------------------------------------------------------


@pytest.fixture
def home_dir(tmp_path, monkeypatch):
    home = tmp_path / "user"
    home.mkdir()
    monkeypatch.setattr(viewer.Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(viewer, "downloads_dir", lambda: home / "Downloads" / "Tripo")
    return home


def test_downloads_are_listed_on_this_machine_with_what_they_contain(tripo, home_dir):
    tripo.on(
        "GET",
        f"/v3/tasks/{DANCE}",
        {"task_id": DANCE, "status": "success", "output": {"model_url": "https://cdn.example/out/model.glb"}},
    )
    tripo.on("GET", "/out/model.glb", httpx.Response(200, content=make_glb(RIGGED)))
    saved = json.loads(
        tools.HANDLERS["tripo_download"](
            {"task_id": DANCE, "save_dir": str(home_dir / "game"), "name": "knight"}
        )
    )
    path = saved["saved"][0]["path"]
    loose = home_dir / "Downloads" / "Tripo" / "chair.stl"
    loose.parent.mkdir(parents=True)
    loose.write_bytes(b"solid chair")

    items = viewer.local_models()["items"]
    assert {item["name"] for item in items} == {"knight.glb", "chair.stl"}
    assert next(item for item in items if item["name"] == "knight.glb")["task_id"] == DANCE
    assert viewer.provenance(DANCE)["files"] == [path]

    first = viewer.local_chunk(path, 0)
    assert first["format"] == "glb" and first["chunks"] == 1 and first["path"] == str(Path(path).resolve())
    assert first["contains"]["joints"] == 41 and first["contains"]["clips"][0]["name"] == "dance_01"
    assert base64.b64decode(first["data"]) == make_glb(RIGGED)


def test_local_files_outside_home_or_not_models_are_refused(home_dir, tmp_path):
    outside = tmp_path / "elsewhere.glb"
    outside.write_bytes(make_glb(RIGGED))
    notes = home_dir / "notes.txt"
    notes.write_text("hi")
    with pytest.raises(TripoError, match="home folder"):
        viewer.local_chunk(str(outside), 0)
    with pytest.raises(TripoError, match="glb"):
        viewer.local_chunk(str(notes), 0)
    with pytest.raises(TripoError, match="No such file"):
        viewer.local_chunk(str(home_dir / "missing.glb"), 0)


def test_the_viewer_cache_is_not_listed_as_a_local_model(home_dir, hermes_home):
    cached = hermes_home / "tripo" / MODEL / ".viewer" / "model.glb"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"glTF")
    kept = hermes_home / "tripo" / MODEL / "chest.glb"
    kept.write_bytes(b"glTF")
    assert [item["name"] for item in viewer.local_models()["items"]] == ["chest.glb"]


# -- settings --------------------------------------------------------------------------------------


def test_settings_win_over_env_and_defaults(monkeypatch, tmp_path):
    stored = {"session_limit": "250", "downloads_folder": str(tmp_path / "models"), "auto_spin": False}
    monkeypatch.setattr(settings, "_stored", lambda key: stored.get(key))
    monkeypatch.setenv("TRIPO_SESSION_LIMIT", "30")
    assert tools.session_limit() == 250
    assert viewer.downloads_dir() == tmp_path / "models"
    assert viewer.preferences() == {"auto_spin": False, "downloads_dir": str(tmp_path / "models")}

    stored.clear()
    assert tools.session_limit() == 30  # the env var still works when the setting is unset
    monkeypatch.delenv("TRIPO_SESSION_LIMIT")
    assert tools.session_limit() == tools.DEFAULT_SESSION_LIMIT
    assert viewer.preferences()["auto_spin"] is True


def test_settings_read_the_plugin_context_when_hermes_loads_it(monkeypatch):
    monkeypatch.setattr(settings, "_stored", REAL_STORED)

    class Ctx:
        def get_config(self, key, default=None):
            return {"session_limit": 0}.get(key, default)

    settings.bind(Ctx())
    try:
        assert tools.session_limit() == 0  # 0 turns the limit off
        assert settings.get("auto_spin", True) is True
    finally:
        settings.bind(None)


def test_manifest_declares_the_settings_hermes_renders():
    # No YAML library here: read the two-space-indented config_schema block by hand.
    text = (PLUGIN_DIR / "plugin.yaml").read_text(encoding="utf-8")
    block = text.split("\nconfig_schema:\n", 1)[1]
    keys = re.findall(r"^  ([a-z_]+):$", block, re.MULTILINE)
    assert keys == ["session_limit", "downloads_folder", "auto_spin"]
    assert re.findall(r"^    type: (\w+)$", block, re.MULTILINE) == ["int", "str", "bool"]
    assert len(re.findall(r"^    label: ", block, re.MULTILINE)) == 3
    assert len(re.findall(r"^    description: ", block, re.MULTILINE)) == 3
