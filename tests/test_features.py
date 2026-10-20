import json

import httpx
import pytest

from hermes_plugins import tripo as plugin
from hermes_plugins.tripo import plans, tools, viewer
from hermes_plugins.tripo.client import Client
from hermes_plugins.tripo.plans import PlanError

UUID = "6f1c2a9e-3b4d-4e5f-8a7b-1c2d3e4f5a6b"


# -- image edits and multiview -------------------------------------------------------------------


def test_image_edit_goes_to_image_to_image_and_prices_by_model():
    plan = plans.generate_image({"image": "task_concept", "prompt": "red armor"})
    assert plan.endpoint == "/generation/image-to-image"
    assert plan.body == {"input": "task_concept", "prompt": "red armor"} and plan.credits == 5
    premium = plans.generate_image(
        {"images": ["task_a", "https://e.x/b.png"], "prompt": "fuse", "model": "banana_pro"}
    )
    assert premium.body["inputs"] == ["task_a", "https://e.x/b.png"] and premium.credits == 10
    assert (
        plans.generate_image({"image": "task_a", "template": "3d_enhance"}).body["template"] == "3d_enhance"
    )
    with pytest.raises(PlanError):
        plans.generate_image({"image": "task_a"})  # neither prompt nor template
    with pytest.raises(PlanError):
        plans.generate_image({"images": ["task_a"] * 5, "prompt": "x"})
    assert plans.generate_image({"prompt": "a cat"}).endpoint == "/generation/text-to-image"


def test_multiview_generate_and_edit():
    made = plans.multiview({"image": "https://e.x/robot.png"})
    assert made.endpoint == "/generation/image-to-multiview" and made.credits == 10
    edited = plans.multiview({"input": UUID, "edits": {"back": "add a logo", "front": "red visor"}})
    assert edited.endpoint == "/generation/edit-multiview"
    assert edited.body["prompts"] == [
        {"view": "front", "prompt": "red visor"},
        {"view": "back", "prompt": "add a logo"},
    ]
    assert edited.credits == 10
    with pytest.raises(PlanError):
        plans.multiview({"input": UUID, "edits": {"top": "x"}})
    with pytest.raises(PlanError):
        plans.multiview({})


def test_model_from_a_multiview_task():
    plan = plans.generate_model({"multiview_task": UUID})
    assert plan.endpoint == "/generation/multiview-to-model" and plan.body["inputs"] == [{"task_id": UUID}]
    assert plan.credits == 30


# -- parts and UVs -------------------------------------------------------------------------------


def test_parts_segment_and_complete():
    seg = plans.parts({"input": UUID})
    assert seg.endpoint == "/mesh/segment" and seg.body == {"input": UUID} and seg.credits == 40
    semantic = plans.parts({"input": UUID, "granularity": "detailed"})
    assert (
        semantic.body["model"] == "v2.0-20260430" and semantic.body["segmentation_granularity"] == "detailed"
    )
    with pytest.raises(PlanError):
        plans.parts({"input": UUID, "granularity": "simple", "model": "v1.0-20250506"})
    done = plans.parts(
        {"action": "complete", "input": UUID, "part_names": ["helmet"], "completion_mode": "quick_cap"}
    )
    assert done.endpoint == "/mesh/complete" and done.credits == 50
    assert done.body == {"input": UUID, "part_names": ["helmet"], "completion_mode": "quick_cap"}
    with pytest.raises(PlanError):
        plans.parts({"action": "complete", "input": "/tmp/model.glb"})


def test_part_names_reach_texture_and_convert():
    assert plans.texture({"input": UUID, "part_names": ["helmet"]}).body["part_names"] == ["helmet"]
    plan = plans.convert({"input": UUID, "format": "GLTF", "part_names": ["sword"]})
    assert plan.body["part_names"] == ["sword"] and plan.credits == 10
    with pytest.raises(PlanError):
        plans.texture({"input": UUID, "part_names": []})


def test_smart_uv():
    plan = plans.smart_uv({"input": UUID})
    assert plan.endpoint == "/mesh/uv" and plan.body == {"input": UUID} and plan.credits == 20
    assert plans.smart_uv({"input": UUID, "bake": False}).body["bake"] is False


def test_new_tools_are_gated_and_registered():
    for name in ("tripo_parts", "tripo_uv", "tripo_multiview"):
        assert name in plans.PAID_TOOLS and name in tools.SCHEMAS and name in tools.HANDLERS


# -- spending limit ------------------------------------------------------------------------------


def test_spend_limit_forces_a_fresh_prompt(tripo, monkeypatch):
    monkeypatch.setenv("TRIPO_SESSION_LIMIT", "50")
    session = "limit-chat"
    args = {"prompt": "a chest"}  # 20 credits
    first = plugin.spend_gate(tool_name="tripo_generate_model", args=args, session_id=session)
    assert first["rule_key"] == "tripo:tripo_generate_model" and "so far" not in first["message"]
    tripo.on("POST", "/v3/generation/text-to-model", {"task_id": "task_a"})
    for _ in range(2):
        json.loads(tools.HANDLERS["tripo_generate_model"](args, session_id=session))
    assert tools.spent(session) == 40
    over = plugin.spend_gate(tool_name="tripo_generate_model", args=args, session_id=session)
    assert "used about 40 so far" in over["message"] and "50-credit limit" in over["message"]
    assert over["rule_key"].startswith("tripo:over-limit:") and over["rule_key"] != first["rule_key"]


def test_spend_limit_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("TRIPO_SESSION_LIMIT", "0")
    tools.record_spend("big-spender", 500)
    directive = plugin.spend_gate(tool_name="tripo_rig", args={"input": UUID}, session_id="big-spender")
    assert directive["rule_key"] == "tripo:tripo_rig" and "used about 500" in directive["message"]
    monkeypatch.setenv("TRIPO_SESSION_LIMIT", "nonsense")
    assert tools.session_limit() == tools.DEFAULT_SESSION_LIMIT


# -- library, previews, videos -------------------------------------------------------------------


def test_library_is_newest_first_with_labels(tripo):
    tripo.on(
        "GET",
        "/v3/account/usage",
        [
            {
                "task_id": UUID,
                "type": "text_to_model",
                "credits_consumed": 20,
                "created_at": "2026-10-20T08:00:00Z",
            },
            {"task_id": "not a task", "type": "x"},
            {
                "task_id": "task_b",
                "type": "mesh_uv",
                "credits_consumed": 20,
                "created_at": "2026-10-20T09:00:00Z",
            },
            {"task_id": "task_c", "type": "something_new", "created_at": "2026-10-19T09:00:00Z"},
        ],
    )
    with Client() as client:
        result = viewer.library(client)
    assert [item["task_id"] for item in result["items"]] == ["task_b", UUID, "task_c"]
    assert [item["label"] for item in result["items"]] == ["Smart UV", "Text to 3D", "Something new"]


def test_preview_falls_back_to_views_and_uv_layouts():
    assert viewer.preview_url({"front_view_url": "https://e.x/f.png"}) == "https://e.x/f.png"
    assert (
        viewer.preview_url({"uv_render_url": "https://e.x/uv.webp", "model_url": "x"})
        == "https://e.x/uv.webp"
    )
    assert (
        viewer.preview_url({"rendered_image_url": "https://e.x/r.webp", "front_view_url": "f"})
        == "https://e.x/r.webp"
    )
    assert viewer.preview_url({}) is None


def test_turntable_video_is_saved_and_attached_on_chat_platforms(tripo, tmp_path):
    tripo.on(
        "GET",
        "/v3/tasks/task_v",
        {
            "task_id": "task_v",
            "status": "success",
            "output": {
                "model_url": "https://cdn.example/out/model.glb",
                "rendered_video_url": "https://cdn.example/out/spin.mp4?sig=1",
            },
        },
    )
    tripo.on("GET", "/out/model.glb", httpx.Response(200, content=b"glTF"))
    tripo.on("GET", "/out/spin.mp4", httpx.Response(200, content=b"mp4"))
    tools.remember_platform("tg-video", "telegram")
    args = {"task_id": "task_v", "save_dir": str(tmp_path), "name": "robot"}
    result = json.loads(tools.HANDLERS["tripo_download"](args, session_id="tg-video"))
    assert (tmp_path / "robot_rendered_video.mp4").read_bytes() == b"mp4"
    assert f"MEDIA:{tmp_path / 'robot_rendered_video.mp4'}" in result["attach"]
