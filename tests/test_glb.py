import json
import struct

import httpx

from hermes_plugins.tripo import glb, tools


def make_glb(gltf: dict) -> bytes:
    body = json.dumps(gltf).encode()
    body += b" " * (-len(body) % 4)
    return struct.pack("<4sIII4s", b"glTF", 2, 20 + len(body), len(body), b"JSON") + body


RIGGED = {
    "meshes": [{}],
    "nodes": [{}] * 43,
    "images": [{}, {}, {}],
    "skins": [{"joints": list(range(41))}],
    "animations": [{"name": "dance_01", "channels": [{}] * 123}],
}


def test_rigged_and_animated_file(tmp_path):
    path = tmp_path / "robot.glb"
    path.write_bytes(make_glb(RIGGED))
    assert glb.summarize(path) == {
        "gltf_version": 2,
        "meshes": 1,
        "nodes": 43,
        "textures": 3,
        "skins": 1,
        "joints": 41,
        "clips": [{"name": "dance_01", "channels": 123}],
    }


def test_still_model_and_garbage(tmp_path):
    still = tmp_path / "still.glb"
    still.write_bytes(make_glb({"meshes": [{}], "nodes": [{}]}))
    summary = glb.summarize(still)
    assert summary["skins"] == 0 and summary["clips"] == []
    junk = tmp_path / "junk.glb"
    junk.write_bytes(b"not a glb at all, just text")
    assert glb.summarize(junk) is None
    assert glb.summarize(tmp_path / "missing.glb") is None


def test_download_reports_what_the_file_contains(tripo, tmp_path):
    tripo.on(
        "GET",
        "/v3/tasks/task_1",
        {
            "task_id": "task_1",
            "status": "success",
            "output": {"model_url": "https://cdn.example/out/model.glb?sig=x"},
        },
    )
    tripo.on("GET", "/out/model.glb", httpx.Response(200, content=make_glb(RIGGED)))
    result = json.loads(tools.HANDLERS["tripo_download"]({"task_id": "task_1", "save_dir": str(tmp_path)}))
    contains = result["saved"][0]["contains"]
    assert contains["joints"] == 41 and contains["clips"][0]["name"] == "dance_01"
    assert "verify" in result
