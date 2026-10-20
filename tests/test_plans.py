import pytest

from hermes_plugins.tripo import plans
from hermes_plugins.tripo.plans import PlanError, Upload


def test_text_to_model_defaults_to_v31_textured():
    plan = plans.generate_model({"prompt": "a treasure chest"})
    assert plan.endpoint == "/generation/text-to-model"
    assert plan.body == {"model": "v3.1-20260211", "prompt": "a treasure chest", "texture": True, "pbr": True}
    assert plan.credits == 20


@pytest.mark.parametrize(
    ("args", "credits"),
    [
        ({"prompt": "x", "texture": False, "pbr": False}, 10),
        ({"image": "https://e.x/a.png"}, 30),
        ({"image": "https://e.x/a.png", "texture_quality": "detailed"}, 50),
        ({"image": "https://e.x/a.png", "texture_quality": "extreme", "quad": True}, 65),
        ({"prompt": "x", "geometry_quality": "detailed"}, 40),
        ({"prompt": "x", "model": "P1-20260311"}, 40),
        ({"image": "task_1", "model": "P1-20260311", "texture": False, "pbr": False}, 40),
        ({"prompt": "x", "smart_low_poly": True}, 30),
        ({"prompt": "x", "generate_parts": True}, 30),
    ],
)
def test_generation_prices_follow_tripo_list(args, credits):
    assert plans.generate_model(args).credits == credits


def test_texture_quality_ignored_when_untextured():
    plan = plans.generate_model({"prompt": "x", "texture": False, "pbr": False, "texture_quality": "extreme"})
    assert "texture_quality" not in plan.body
    assert plan.credits == 10


def test_fast_texture_pins_the_v35_texture_model():
    plan = plans.generate_model({"prompt": "x", "texture_quality": "fast"})
    assert plan.body["texture_version"] == "v3.5-20260815"
    assert plan.credits == 20


def test_parts_turn_texture_off_and_refuse_explicit_texture():
    plan = plans.generate_model({"prompt": "x", "generate_parts": True})
    assert plan.body["texture"] is False and plan.body["pbr"] is False
    with pytest.raises(PlanError):
        plans.generate_model({"prompt": "x", "generate_parts": True, "texture": True})


def test_exactly_one_source():
    with pytest.raises(PlanError):
        plans.generate_model({})
    with pytest.raises(PlanError):
        plans.generate_model({"prompt": "x", "image": "task_1"})


def test_local_paths_become_uploads_and_references_pass_through():
    assert plans.reference("task_abc", "model") == "task_abc"
    assert plans.reference("file_abc", "image") == "file_abc"
    assert plans.reference("https://e.x/a.png", "image") == "https://e.x/a.png"
    assert plans.reference("3154b4b0-4647-4032-aa82-827441ae92ef", "image").startswith("3154")
    assert plans.reference("/home/me/a.png", "image") == Upload("/home/me/a.png", "image")


def test_multiview_needs_front_and_orders_views():
    plan = plans.generate_model({"views": {"back": "task_b", "front": "task_f"}})
    assert plan.body["inputs"] == [{"front": "task_f"}, {"back": "task_b"}]
    with pytest.raises(PlanError):
        plans.generate_model({"views": {"back": "task_b", "left": "task_l"}})
    with pytest.raises(PlanError):
        plans.generate_model({"views": {"front": "task_f", "top": "task_t"}})


def test_v25_rejects_v3_only_options():
    for extra in ({"quad": True}, {"geometry_quality": "detailed"}, {"auto_size": True}):
        with pytest.raises(PlanError):
            plans.generate_model({"prompt": "x", "model": "v2.5-20250123", **extra})


def test_rig_picks_a_model_that_supports_the_rig_type():
    assert plans.rig({"input": "task_1"}).body == {"input": "task_1"}
    assert plans.rig({"input": "task_1", "rig_type": "quadruped"}).body["model"] == "v2.5-20260210"
    assert plans.rig({"input": "task_1", "spec": "unreal"}).body["model"] == "v3.0-20260909"
    assert plans.rig({"input": "task_1"}).credits == 25


def test_animate_prices_per_animation_and_normalizes_ids():
    plan = plans.animate({"input": "task_r", "animations": ["walk", "preset:run"]})
    assert plan.body["animations"] == ["preset:walk", "preset:run"]
    assert plan.credits == 20
    single = plans.animate({"input": "task_r", "animations": ["preset:idle"]})
    assert single.body["animation"] == "preset:idle" and "animations" not in single.body
    with pytest.raises(PlanError):
        plans.animate({"input": "task_r", "animations": ["walk"] * 6})
    with pytest.raises(PlanError):
        plans.animate({"input": "/tmp/model.glb", "animations": ["walk"]})


def test_convert_basic_versus_advanced():
    assert plans.convert({"input": "task_1", "format": "fbx"}).credits == 5
    plan = plans.convert({"input": "task_1", "format": "STL", "flatten_bottom": True})
    assert plan.credits == 10 and plan.body["format"] == "STL"
    with pytest.raises(PlanError):
        plans.convert({"input": "task_1", "format": "blend"})


def test_texture_and_retopology_prices():
    assert plans.texture({"input": "task_1"}).credits == 10
    assert (
        plans.texture(
            {
                "input": "task_1",
                "texture_quality": "extreme",
                "prompt": "x",
                "style_image": "https://e.x/s.png",
            }
        ).credits
        == 35
    )
    assert plans.texture({"input": "task_1", "texture_quality": "fast"}).body["model"] == "v3.5-20260815"
    assert plans.retopology({"input": "task_1", "face_limit": 5000}).credits == 35
    assert plans.retopology({"input": "task_1", "face_limit": 5000, "quad": True}).credits == 40
    assert plans.retopology({"input": "task_1", "face_limit": 5000, "model": "v2.0"}).credits == 30
    with pytest.raises(PlanError):
        plans.retopology({"input": "task_1", "face_limit": 5000, "model": "v2.0", "smart_uv": True})


def test_rig_check_is_free_and_not_gated():
    assert plans.rig_check({"input": "task_1"}).credits == 0
    assert "tripo_rig_check" not in plans.PAID_TOOLS
    assert plans.PAID_TOOLS >= {"tripo_generate_model", "tripo_rig", "tripo_animate", "tripo_convert"}


def test_texture_false_alone_means_untextured():
    plan = plans.generate_model({"prompt": "a small clay teapot", "texture": False})
    assert plan.body["texture"] is False and plan.body["pbr"] is False
    assert plan.credits == 10 and "untextured" in plan.summary
    # An explicit pbr=true still wins, as Tripo documents.
    assert plans.generate_model({"prompt": "x", "texture": False, "pbr": True}).body["texture"] is True
