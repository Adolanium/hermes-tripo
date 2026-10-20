import base64
import json

import httpx
import pytest

from hermes_plugins.tripo import tools, viewer
from hermes_plugins.tripo.client import Client, TripoError

DONE = {
    "task_id": "task_1",
    "type": "text_to_model",
    "status": "success",
    "progress": 100,
    "credits_consumed": 20.0,
    "output": {
        "model_url": "https://cdn.example/out/model.glb?sig=x",
        "rendered_image_url": "https://cdn.example/p.webp?sig=y",
    },
}


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return tmp_path


def test_state_reports_progress_and_what_can_be_shown(tripo, home):
    tripo.on("GET", "/v3/tasks/task_1", {"task_id": "task_1", "status": "running", "progress": 40}, DONE)
    with Client() as client:
        running = viewer.task_state(client, "task_1")
        done = viewer.task_state(client, "task_1")
    assert running["done"] is False and running["progress"] == 40
    assert done["done"] and done["viewable"] and done["model_format"] == "glb" and done["has_preview"]


def test_model_is_fetched_once_and_served_in_chunks(tripo, home, monkeypatch):
    monkeypatch.setattr(viewer, "CHUNK_BYTES", 4)
    tripo.on("GET", "/v3/tasks/task_1", DONE)
    tripo.on("GET", "/out/model.glb", httpx.Response(200, content=b"glTF0123ab"))
    with Client() as client:
        parts = [viewer.model_chunk(client, "task_1", index) for index in range(3)]
        again = viewer.model_chunk(client, "task_1", 0)
    assert [p["chunks"] for p in parts] == [3, 3, 3] and parts[0]["format"] == "glb"
    assert b"".join(base64.b64decode(p["data"]) for p in parts) == b"glTF0123ab"
    assert again["data"] == parts[0]["data"]
    cdn = [r for r in tripo.requests if r.url.host == "cdn.example"]
    assert len(cdn) == 1 and "authorization" not in cdn[0].headers
    assert (home / "tripo" / "task_1" / ".viewer" / "model.glb").is_file()
    with Client() as client, pytest.raises(TripoError):
        viewer.model_chunk(client, "task_1", 3)


def test_preview_is_a_data_url(tripo, home):
    tripo.on("GET", "/v3/tasks/task_1", DONE)
    tripo.on("GET", "/p.webp", httpx.Response(200, content=b"RIFF"))
    with Client() as client:
        answer = viewer.preview(client, "task_1")
    assert answer["data_url"] == "data:image/webp;base64," + base64.b64encode(b"RIFF").decode()


def test_unviewable_or_unfinished_tasks_are_refused(tripo, home):
    usdz = {**DONE, "output": {"model_url": "https://cdn.example/out/model.usdz"}}
    tripo.on("GET", "/v3/tasks/task_u", usdz)
    tripo.on("GET", "/v3/tasks/task_r", {"task_id": "task_r", "status": "running"})
    with Client() as client:
        assert viewer.task_state(client, "task_u")["viewable"] is False
        with pytest.raises(TripoError, match="usdz"):
            viewer.model_chunk(client, "task_u", 0)
        with pytest.raises(TripoError, match="running"):
            viewer.model_chunk(client, "task_r", 0)


@pytest.mark.parametrize("bad", ["../etc/passwd", "task_../x", "", "task_a/b", "x" * 10])
def test_task_ids_are_validated_before_anything_runs(tripo, home, bad):
    with Client() as client, pytest.raises(TripoError):
        viewer.task_state(client, bad)
    assert tripo.requests == []


# -- per-surface hints in tool results ----------------------------------------------------------


def call(tool, **args):
    return json.loads(tools.HANDLERS[tool](args))


def test_desktop_gets_the_viewer_directive(tripo, monkeypatch):
    from hermes_plugins import tripo as plugin

    plugin.note_platform(session_id="s-desk", platform="desktop")
    tripo.on("POST", "/v3/generation/text-to-model", {"task_id": "task_9"})
    tripo.on("GET", "/v3/tasks/task_9", {**DONE, "task_id": "task_9"})
    created = json.loads(tools.HANDLERS["tripo_generate_model"]({"prompt": "a chest"}, session_id="s-desk"))
    finished = json.loads(
        tools.HANDLERS["tripo_task"]({"task_id": "task_9", "wait": False}, session_id="s-desk")
    )
    assert (
        created["show_in_chat"] == '::tripo-model{task="task_9"}' and "live progress" in created["show_hint"]
    )
    assert finished["show_in_chat"] == '::tripo-model{task="task_9"}'


@pytest.mark.parametrize("surface", ["cli", "telegram", ""])
def test_other_surfaces_get_no_directive(tripo, monkeypatch, surface):
    tools.remember_platform("s-other", surface)
    tripo.on("GET", "/v3/tasks/task_9", {**DONE, "task_id": "task_9"})
    result = json.loads(
        tools.HANDLERS["tripo_task"]({"task_id": "task_9", "wait": False}, session_id="s-other")
    )
    assert "show_in_chat" not in result
    # A session nobody told us about gets no directive either.
    assert "show_in_chat" not in call("tripo_task", task_id="task_9", wait=False)


def test_chat_platforms_are_told_how_to_attach_downloads(tripo, monkeypatch, tmp_path):
    tripo.on("GET", "/v3/tasks/task_1", DONE)
    tripo.on("GET", "/out/model.glb", httpx.Response(200, content=b"glTF"))
    tripo.on("GET", "/p.webp", httpx.Response(200, content=b"RIFF"))
    tools.remember_platform("s-tg", "telegram")
    args = {"task_id": "task_1", "save_dir": str(tmp_path), "name": "chest"}
    result = json.loads(tools.HANDLERS["tripo_download"](args, session_id="s-tg"))
    assert result["attach"] == [
        f"MEDIA:{tmp_path / 'chest_rendered_image.webp'}",
        f"MEDIA:{tmp_path / 'chest.glb'}",
    ]
    tools.remember_platform("s-cli", "cli")
    assert "attach" not in json.loads(tools.HANDLERS["tripo_download"](args, session_id="s-cli"))


def test_desktop_downloads_point_at_the_viewer_not_a_file_card(tripo, tmp_path):
    tripo.on("GET", "/v3/tasks/task_1", DONE)
    tripo.on("GET", "/out/model.glb", httpx.Response(200, content=b"glTF"))
    tripo.on("GET", "/p.webp", httpx.Response(200, content=b"RIFF"))
    tools.remember_platform("s-desk2", "desktop")
    args = {"task_id": "task_1", "save_dir": str(tmp_path)}
    result = json.loads(tools.HANDLERS["tripo_download"](args, session_id="s-desk2"))
    assert "attach" not in result
    assert result["show_in_chat"] == '::tripo-model{task="task_1"}' and "MEDIA" in result["deliver"]


def test_platform_memory_is_bounded():
    for index in range(tools._PLATFORMS_MAX + 50):
        tools.remember_platform(f"s{index}", "desktop")
    assert len(tools._PLATFORMS) == tools._PLATFORMS_MAX and "s0" not in tools._PLATFORMS


def test_plain_http_outputs_are_refused_from_the_real_api(tripo, home):
    http = {**DONE, "output": {"model_url": "http://cdn.example/out/model.glb"}}
    tripo.on("GET", "/v3/tasks/task_1", http)
    with Client() as client, pytest.raises(TripoError, match="non-https"):
        viewer.model_chunk(client, "task_1", 0)
    assert not [r for r in tripo.requests if r.url.host == "cdn.example"]
