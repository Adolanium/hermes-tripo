"""Tool schemas and handlers. Handlers return JSON strings and never raise."""

from __future__ import annotations

import json
import os
import re
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import plans
from .client import TERMINAL, Client, TripoError, tripo_home

TOOLSET = "tripo"
MAX_WAIT = 300
DEFAULT_WAIT = 120
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")

_INPUT = (
    "A finished Tripo task_id (task_...), a file_token, a public https URL, "
    "or an ABSOLUTE local file path (uploaded to Tripo first)."
)


def _schema(name: str, description: str, properties: dict, required: list[str] | None = None) -> dict:
    return {
        "name": name,
        "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required or []},
    }


def _enum(options: tuple, description: str) -> dict:
    return {"type": "string", "enum": list(options), "description": description}


_BOOL = {"type": "boolean"}

SCHEMAS = {
    "tripo_generate_model": _schema(
        "tripo_generate_model",
        "Start a Tripo 3D generation task. SPENDS CREDITS (text 10-20, image 20-30, P series 30-50, "
        "plus surcharges); Hermes asks the user first. Pass exactly one of prompt, image or views. "
        "Returns a task_id at once; follow with tripo_task to wait, then tripo_download. For multi-step "
        "jobs (rigging, game engines, printing, AR) load the skill hermes-tripo:workflow first.",
        {
            "prompt": {
                "type": "string",
                "description": "Text to 3D: shape, material, style (max 1024 chars).",
            },
            "image": {
                "type": "string",
                "description": "Image to 3D (PNG/JPEG/WebP). "
                + _INPUT
                + " A tripo_generate_image task_id also works.",
            },
            "views": {
                "type": "object",
                "description": "Multiview to 3D: front plus at least one of left/back/right, same object "
                "and lighting. Each value: " + _INPUT,
                "properties": {view: {"type": "string"} for view in plans.VIEWS},
            },
            "model": _enum(
                plans.GEOMETRY_MODELS,
                "v3.1-20260211 (default, best detail); P1/P2 for clean "
                "low-poly game assets (48-20k faces, pricier).",
            ),
            "texture": {"type": "boolean", "description": "Default true. false = bare geometry, cheaper."},
            "pbr": {"type": "boolean", "description": "PBR maps; default true (forces texture on)."},
            "texture_quality": _enum(
                plans.TEXTURE_QUALITIES, "standard (default), fast, detailed (+20), extreme 8K (+30)."
            ),
            "geometry_quality": _enum(("standard", "detailed"), "detailed = Ultra geometry, +20, v3.x only."),
            "face_limit": {"type": "integer", "description": "Max faces. Games 10k-100k; omit for adaptive."},
            "quad": {"type": "boolean", "description": "Quad mesh, +5, output becomes FBX."},
            "smart_low_poly": {
                "type": "boolean",
                "description": "Hand-made-style low-poly topology, +10, v3.x.",
            },
            "generate_parts": {"type": "boolean", "description": "Editable separate parts, +20, untextured."},
            "auto_size": {"type": "boolean", "description": "Scale to real-world meters (AR/VR, engines)."},
            "negative_prompt": {"type": "string", "description": "Text mode only: what to avoid."},
            "model_seed": {"type": "integer"},
            "texture_seed": {"type": "integer"},
        },
    ),
    "tripo_generate_image": _schema(
        "tripo_generate_image",
        "Generate a 2D concept image (about 5 credits; Hermes asks first). Use it to agree on a look "
        "before paying for 3D, then pass its task_id as tripo_generate_model image.",
        {
            "prompt": {"type": "string", "description": "What to draw, most important first."},
            "template": _enum(
                plans.IMAGE_TEMPLATES,
                "t_pose for characters you will rig; figure for a full "
                "body; asset_extraction to isolate a subject.",
            ),
            "model": _enum(plans.IMAGE_MODELS, "Default seedream_v4."),
            "size": {"type": "string", "description": "e.g. 2048x2048 or 2K (seedream / chat_image)."},
            "aspect_ratio": {"type": "string", "description": "e.g. 1:1, 16:9 (banana models only)."},
        },
        ["prompt"],
    ),
    "tripo_texture": _schema(
        "tripo_texture",
        "Re-texture an existing model (10/20/30 credits for standard/detailed/extreme, +5 with "
        "style_image; Hermes asks first). Guide it with a text prompt or a reference image.",
        {
            "input": {"type": "string", "description": "The model. " + _INPUT},
            "prompt": {"type": "string", "description": "Texture description, e.g. 'worn leather'."},
            "image": {"type": "string", "description": "Reference image instead of a prompt. " + _INPUT},
            "style_image": {"type": "string", "description": "Style reference that goes with prompt."},
            "texture_quality": _enum(plans.TEXTURE_QUALITIES, "Default standard."),
            "model": _enum(
                plans.TEXTURE_MODELS, "Texture model; v3.0 default for v3.x geometry, v2.5 for v2.5 geometry."
            ),
            "pbr": _BOOL,
            "texture_seed": {"type": "integer"},
        },
        ["input"],
    ),
    "tripo_rig_check": _schema(
        "tripo_rig_check",
        "Free. Check whether a model can be rigged and which rig_type fits. Returns a task_id; "
        "tripo_task shows riggable and rig_type when it finishes.",
        {"input": {"type": "string", "description": "The model. " + _INPUT}},
        ["input"],
    ),
    "tripo_rig": _schema(
        "tripo_rig",
        "Add a skeleton to a model (25 credits; Hermes asks first). Run tripo_rig_check first and "
        "pass its rig_type.",
        {
            "input": {"type": "string", "description": "The model. " + _INPUT},
            "rig_type": _enum(plans.RIG_TYPES, "From tripo_rig_check. Default biped."),
            "model": _enum(
                plans.RIG_MODELS,
                "v1.0 bipeds only (Tripo default, 90+ biped presets); "
                "v2.5 creatures; v3.0 best humanoids and engine specs. "
                "Chosen automatically from rig_type/spec when omitted.",
            ),
            "spec": _enum(plans.RIG_SPECS, "Bone naming for bipeds; unity/unreal/vrm/actorcore use v3.0."),
            "out_format": _enum(("glb", "fbx"), "Default glb."),
        },
        ["input"],
    ),
    "tripo_animate": _schema(
        "tripo_animate",
        "Apply preset animations to a rigged model (10 credits each, up to 5 per task; Hermes asks "
        "first). Presets for rig v2.5/v3.0: preset:idle, walk, run, jump, climb, dive, slash, shoot, "
        "hurt, fall, turn; preset:quadruped:walk, hexapod:walk, octopod:walk, serpentine:march, "
        "aquatic:march. Rig v1.0 bipeds use preset:biped:* (e.g. preset:biped:dance_01).",
        {
            "input": {"type": "string", "description": "task_id of a finished tripo_rig task."},
            "animations": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Preset ids, e.g. ['preset:idle', 'preset:walk'].",
            },
            "out_format": _enum(("glb", "fbx"), "Default glb."),
            "animate_in_place": {"type": "boolean", "description": "No root motion (good for engines)."},
            "export_with_geometry": {"type": "boolean", "description": "Default true."},
        },
        ["input", "animations"],
    ),
    "tripo_retopology": _schema(
        "tripo_retopology",
        "Rebuild a model at a lower polycount with clean topology (v3.0: 35 credits, 40 with quad; "
        "v1.0/v2.0: 30; Hermes asks first).",
        {
            "input": {"type": "string", "description": "The model. " + _INPUT},
            "face_limit": {"type": "integer", "description": "Target face count."},
            "quad": _BOOL,
            "smart_uv": {"type": "boolean", "description": "Also unwrap UVs (v3.0 only, same price)."},
            "model": _enum(plans.RETOPOLOGY_MODELS, "Default v3.0 (newest)."),
        },
        ["input", "face_limit"],
    ),
    "tripo_convert": _schema(
        "tripo_convert",
        "Convert a model's format (5 credits; 10 when any processing option is set; Hermes asks "
        "first). FBX for Unity/Unreal, USDZ for Apple AR, STL/3MF/OBJ for printing, GLTF for web.",
        {
            "input": {"type": "string", "description": "The model. " + _INPUT},
            "format": _enum(plans.CONVERT_FORMATS, "Target format."),
            "face_limit": {"type": "integer"},
            "quad": _BOOL,
            "texture_size": {"type": "integer", "description": "Texture pixels, default 4096."},
            "texture_format": {"type": "string", "description": "JPEG (default), PNG, WEBP, ..."},
            "scale_factor": {"type": "number"},
            "pivot_to_center_bottom": _BOOL,
            "flatten_bottom": {"type": "boolean", "description": "Flat base, useful for printing."},
            "export_orientation": _enum(("+x", "-x", "+y", "-y"), "Forward axis."),
            "fbx_preset": _enum(("blender", "3dsmax", "mixamo", "bake_scale"), "FBX target."),
            "with_animation": _BOOL,
            "animate_in_place": _BOOL,
        },
        ["input", "format"],
    ),
    "tripo_task": _schema(
        "tripo_task",
        "Free. Get a Tripo task's status, progress, credits consumed and outputs. With wait=true "
        "(default) it polls until the task finishes or wait_seconds pass; a timeout leaves the task "
        "running, so call again with the same task_id rather than creating a new task.",
        {
            "task_id": {"type": "string"},
            "wait": {"type": "boolean", "description": "Default true."},
            "wait_seconds": {"type": "integer", "description": f"Default {DEFAULT_WAIT}, max {MAX_WAIT}."},
        },
        ["task_id"],
    ),
    "tripo_download": _schema(
        "tripo_download",
        "Free. Save a finished task's outputs (model file, preview render, and any other files) to "
        "disk and return the paths. Output URLs expire, so download soon after a task succeeds.",
        {
            "task_id": {"type": "string"},
            "save_dir": {
                "type": "string",
                "description": "ABSOLUTE directory to save into, normally in "
                "the user's project. Default: "
                "<HERMES_HOME>/tripo/<task_id>/.",
            },
            "name": {
                "type": "string",
                "description": "File name stem, e.g. 'treasure_chest'. Default: the task_id.",
            },
            "model_only": {"type": "boolean", "description": "Skip previews and other extras."},
        },
        ["task_id"],
    ),
    "tripo_balance": _schema(
        "tripo_balance",
        "Free. Tripo credit balance (available and frozen in running tasks), optionally with "
        "recent per-task usage.",
        {"usage": {"type": "boolean", "description": "Also list recent tasks and their cost."}},
    ),
}

EMOJI = {"tripo_balance": "💳", "tripo_task": "⏳", "tripo_download": "📥"}


def _ok(**data: Any) -> str:
    return json.dumps({"ok": True, **data}, ensure_ascii=False)


def _err(message: str, **data: Any) -> str:
    return json.dumps({"ok": False, "error": message, **data}, ensure_ascii=False)


# Surfaces that render nothing beyond text, and those that are not chat channels.
_TEXT_ONLY = frozenset({"cli", "tui"})
_NOT_CHAT = frozenset({"", "local", "cron", "api_server", "acp", "webhook"})


# session_id -> platform, filled by the pre_llm_call hook (see __init__.py). Bounded: a long-lived
# gateway sees many sessions, and only the live ones matter.
_PLATFORMS: OrderedDict[str, str] = OrderedDict()
_PLATFORMS_MAX = 512


def remember_platform(session_id: str, platform: str) -> None:
    if not session_id:
        return
    _PLATFORMS[session_id] = (platform or "").strip().lower()
    _PLATFORMS.move_to_end(session_id)
    while len(_PLATFORMS) > _PLATFORMS_MAX:
        _PLATFORMS.popitem(last=False)


def _surface(session_id: str = "") -> str:
    """Where this turn is shown: 'desktop', a chat platform such as 'telegram', 'cli', or '' if unknown."""
    return _PLATFORMS.get(session_id or "", "")


def directive(task_id: str) -> str:
    return f'::tripo-model{{task="{task_id}"}}'


def _show_hint(task_id: str, session_id: str, *, finished: bool) -> dict:
    """How to put this task in front of the user on the current surface."""
    if _surface(session_id) != "desktop":
        return {}
    what = "an interactive 3D viewer" if finished else "live progress, then an interactive 3D viewer"
    return {
        "show_in_chat": directive(task_id),
        "show_hint": f"Put show_in_chat alone on its own line in your reply: Hermes Desktop renders it as {what}.",
    }


def _task_view(data: dict, session_id: str = "") -> dict:
    """The parts of a task the model needs, without long signed URLs."""
    view = {key: data.get(key) for key in ("task_id", "type", "status", "progress") if key in data}
    if data.get("credits_consumed") is not None:
        view["credits_consumed"] = data["credits_consumed"]
    output = data.get("output") or {}
    if isinstance(output, dict) and output:
        files = sorted(
            key for key, value in output.items() if isinstance(value, str) and value.startswith("http")
        )
        if files:
            view["output_files"] = files
        preview = output.get("rendered_image_url") or output.get("image_url")
        if preview:
            view["preview_url"] = preview
        facts = {key: value for key, value in output.items() if key not in files}
        if facts:
            view["result"] = facts
    for key in ("error_code", "error_message"):
        if data.get(key) is not None:
            view[key] = data[key]
    status = data.get("status")
    if status == "success":
        view["next"] = "tripo_download to save the files; reuse this task_id for texture, rig, convert."
        if data.get("task_id") and isinstance(output, dict) and (output.get("model_url") or preview):
            view.update(_show_hint(data["task_id"], session_id, finished=True))
    elif status not in TERMINAL:
        view["next"] = "Still running: call tripo_task again with this task_id. Do not create a new task."
    else:
        view["next"] = "The task did not succeed; Tripo refunds frozen credits for failed tasks."
    return view


def _create_handler(tool: str) -> Callable[..., str]:
    build = plans.BUILDERS[tool]

    def handler(args: dict, session_id: str = "", **_: Any) -> str:
        try:
            plan = build(args or {})
        except plans.PlanError as exc:
            return _err(f"{exc}. Nothing was sent and no credits were spent.")
        try:
            with Client() as client:
                body = client.resolve(plan.body)
                task_id = client.create(plan.endpoint, body)
        except TripoError as exc:
            return _err(str(exc), code=exc.code)
        shown = _show_hint(task_id, session_id, finished=False) if tool != "tripo_rig_check" else {}
        return _ok(
            task_id=task_id,
            request=plan.summary,
            estimated_cost=plan.cost_text(),
            notes=plan.notes,
            next="Call tripo_task with this task_id to wait for the result.",
            **shown,
        )

    return handler


def tripo_task(args: dict, session_id: str = "", **_: Any) -> str:
    task_id = str((args or {}).get("task_id") or "").strip()
    if not task_id:
        return _err("task_id is required")
    wait = args.get("wait", True) is not False
    seconds = args.get("wait_seconds", DEFAULT_WAIT)
    seconds = max(0, min(int(seconds) if isinstance(seconds, (int, float)) else DEFAULT_WAIT, MAX_WAIT))
    try:
        with Client() as client:
            data = client.wait(task_id, seconds) if wait else client.task(task_id)
    except TripoError as exc:
        return _err(str(exc), code=exc.code)
    return _ok(**_task_view(data, session_id))


def _default_dir(task_id: str) -> Path:
    return tripo_home() / _SAFE_NAME.sub("_", task_id)


def _extension(url: str, fallback: str) -> str:
    suffix = Path(url.split("?", 1)[0]).suffix.lower()
    return suffix if re.fullmatch(r"\.[a-z0-9]{1,5}", suffix) else fallback


def _attach_hint(saved: list[dict], session_id: str) -> dict:
    """On surfaces with an attachment channel, MEDIA:<path> sends a saved file to the user."""
    surface = _surface(session_id)
    if surface in _TEXT_ONLY or surface in _NOT_CHAT:
        return {}
    preview = next((item["path"] for item in saved if item["file"] != "model"), None)
    model = next((item["path"] for item in saved if item["file"] == "model"), None)
    lines = []
    if preview and surface != "desktop":
        lines.append(f"MEDIA:{preview}")
    if model:
        lines.append(f"MEDIA:{model}")
    if not lines:
        return {}
    where = "as a download card" if surface == "desktop" else "to the chat"
    return {
        "attach": lines,
        "attach_hint": f"Put any of these lines alone in your reply to send the file {where}.",
    }


def tripo_download(args: dict, session_id: str = "", **_: Any) -> str:
    args = args or {}
    task_id = str(args.get("task_id") or "").strip()
    if not task_id:
        return _err("task_id is required")
    raw_dir = str(args.get("save_dir") or "").strip()
    if raw_dir:
        target_dir = Path(os.path.expanduser(raw_dir))
        if not target_dir.is_absolute():
            return _err("save_dir must be an absolute path")
    else:
        target_dir = _default_dir(task_id)
    stem = _SAFE_NAME.sub("_", str(args.get("name") or task_id)).strip("._") or "tripo_model"
    try:
        with Client() as client:
            data = client.task(task_id)
            if data.get("status") != "success":
                return _err(
                    f"Task is {data.get('status')}, not success; nothing to download yet.",
                    task=_task_view(data),
                )
            output = data.get("output") or {}
            urls = {
                key: value
                for key, value in output.items()
                if isinstance(value, str) and value.startswith(("https://", "http://"))
            }
            if args.get("model_only"):
                urls = {key: value for key, value in urls.items() if key == "model_url"}
            if not urls:
                return _err("The task has no downloadable files.", task=_task_view(data))
            saved = []
            for key, url in sorted(urls.items(), key=lambda item: item[0] != "model_url"):
                label = key.removesuffix("_url")
                fallback = ".glb" if key == "model_url" else ".bin"
                filename = (
                    f"{stem}{_extension(url, fallback)}"
                    if key == "model_url"
                    else f"{stem}_{label}{_extension(url, fallback)}"
                )
                target = target_dir / filename
                target_dir.mkdir(parents=True, exist_ok=True)
                size = client.fetch(url, target)
                saved.append({"file": label, "path": str(target), "bytes": size})
    except TripoError as exc:
        return _err(str(exc), code=exc.code)
    except OSError as exc:
        return _err(f"Could not write the file: {exc}")
    return _ok(task_id=task_id, saved=saved, **_attach_hint(saved, session_id))


def tripo_balance(args: dict, **_: Any) -> str:
    try:
        with Client() as client:
            balance = client.balance()
            result: dict[str, Any] = {"available": balance.get("balance"), "frozen": balance.get("frozen")}
            if (args or {}).get("usage"):
                result["recent_usage"] = client.usage()[:20]
    except TripoError as exc:
        return _err(str(exc), code=exc.code)
    return _ok(**result)


HANDLERS: dict[str, Callable[..., str]] = {
    **{tool: _create_handler(tool) for tool in plans.BUILDERS},
    "tripo_task": tripo_task,
    "tripo_download": tripo_download,
    "tripo_balance": tripo_balance,
}
