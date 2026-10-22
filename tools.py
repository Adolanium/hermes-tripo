"""Tool schemas and handlers. Handlers return JSON strings and never raise."""

from __future__ import annotations

import json
import re
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import glb, history, plans, settings, viewer
from .client import TERMINAL, Client, TripoError, free_stem, local_path, tripo_home

TOOLSET = "tripo"
MAX_WAIT = 300
DEFAULT_WAIT = 120
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")

_INPUT = (
    "A finished Tripo task id (a UUID), a file_token, a public https URL, "
    "or a local file path (uploaded to Tripo first; relative paths resolve against the working folder)."
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
        "plus surcharges); Hermes asks the user first. Pass exactly one of prompt, image, views or multiview_task. "
        "Returns a task_id at once; follow with tripo_task to wait, then tripo_download. For multi-step "
        'jobs (rigging, game engines, printing, AR) first call skill_view(name="hermes-tripo:workflow") (exactly that name, no prefix).',
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
            "multiview_task": {
                "type": "string",
                "description": "Multiview to 3D from the four views a tripo_multiview task made (its task id).",
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
        "Make a 2D image (5 credits, 10 for chat_image/banana_pro/banana2 edits; Hermes asks first). "
        "Text only: a concept to agree on before paying for 3D. With image or images: edit them, "
        "transfer a style, or apply a template (t_pose, 3d_enhance, variants). Pass the result's "
        "task id as tripo_generate_model image.",
        {
            "prompt": {"type": "string", "description": "What to draw or change, most important first."},
            "image": {"type": "string", "description": "One reference image to edit. " + _INPUT},
            "images": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Up to 4 reference images to combine; refer to them as image[1], image[2].",
            },
            "template": {
                "type": "string",
                "enum": sorted(set(plans.IMAGE_TEMPLATES) | set(plans.EDIT_TEMPLATES)),
                "description": "t_pose for characters you will rig; figure for a full body; asset_extraction "
                "(text only) isolates a subject; 3d_enhance (edits) readies a photo for 3D; variants.",
            },
            "model": {
                "type": "string",
                "enum": sorted(set(plans.IMAGE_MODELS) | set(plans.EDIT_MODELS)),
                "description": "Default seedream_v4 for text, seedream_v5 for edits.",
            },
            "size": {"type": "string", "description": "e.g. 2048x2048 or 2K (seedream / chat_image)."},
            "aspect_ratio": {"type": "string", "description": "e.g. 1:1, 16:9 (banana models only)."},
        },
    ),
    "tripo_multiview": _schema(
        "tripo_multiview",
        "Make front, left, back and right views of one subject from a single image (10 credits), or "
        "edit an earlier set view by view (5 per edited view); Hermes asks first. Multiview to 3D "
        "gives better shapes than one image: pass the task id as tripo_generate_model views.",
        {
            "image": {"type": "string", "description": "The subject, on a clean background. " + _INPUT},
            "input": {
                "type": "string",
                "description": "For edits: the task id of an earlier tripo_multiview.",
            },
            "edits": {
                "type": "object",
                "description": "For edits: view -> instruction, e.g. {'back': 'add a logo'}.",
                "properties": {view: {"type": "string"} for view in plans.VIEWS},
            },
        },
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
            "part_names": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Texture only these parts (names from a tripo_parts segment task).",
            },
        },
        ["input"],
    ),
    "tripo_parts": _schema(
        "tripo_parts",
        "Split a model into named parts (action segment, 40 credits), then optionally rebuild the "
        "geometry hidden where parts met (action complete, 50). Hermes asks first. The part names "
        "come back in tripo_task's result; use them with tripo_texture part_names (repaint one part) "
        "or tripo_convert part_names (export some parts).",
        {
            "action": _enum(("segment", "complete"), "Default segment."),
            "input": {
                "type": "string",
                "description": "segment: the model (" + _INPUT + ") complete: the segment task id.",
            },
            "model": _enum(
                plans.SEGMENT_MODELS,
                "Segment model: v1.0 geometric (default), v2.0 semantic, labelled parts.",
            ),
            "granularity": _enum(
                ("simple", "balanced", "detailed"), "Segment v2.0 only: how finely to split."
            ),
            "part_names": {
                "type": "array",
                "items": {"type": "string"},
                "description": "complete: only these parts. Default all.",
            },
            "completion_mode": _enum(
                plans.COMPLETION_MODES,
                "complete: ai_completion (default, rebuilds shape) or quick_cap (seals holes).",
            ),
        },
        ["input"],
    ),
    "tripo_uv": _schema(
        "tripo_uv",
        "Unwrap a model's UVs again and re-bake its textures, rig and animation onto the new layout "
        "(20 credits; Hermes asks first). The result includes a UV layout image. The model must be "
        "under 80,000 triangles: run tripo_retopology first if it is heavier.",
        {
            "input": {"type": "string", "description": "The model. " + _INPUT},
            "bake": {"type": "boolean", "description": "Default true. false returns geometry with UVs only."},
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
                "Pick it from the animation you will apply: v1.0 for dances and other preset:biped:* "
                "clips (Tripo's default for bipeds); v3.0 for preset:walk/idle/run... and engine specs "
                "(no dances); v2.5 for creatures. Chosen from rig_type/spec when omitted.",
            ),
            "spec": _enum(plans.RIG_SPECS, "Bone naming for bipeds; unity/unreal/vrm/actorcore use v3.0."),
            "out_format": _enum(("glb", "fbx"), "Default glb."),
        },
        ["input"],
    ),
    "tripo_animate": _schema(
        "tripo_animate",
        "Apply preset animations to a rigged model (10 credits each, up to 5 per task; Hermes asks "
        "first). The rig model decides which presets exist: rig v1.0 bipeds use preset:biped:* "
        "(dances: preset:biped:dance_01, ...); rig v3.0 humanoids use preset:idle, walk, run, jump, climb, "
        "dive, slash, shoot, hurt, fall, turn; rig v2.5 creatures use preset:quadruped:walk, hexapod:walk, "
        "octopod:walk, serpentine:march, aquatic:march.",
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
            "part_names": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Export only these parts (names from a tripo_parts segment task).",
            },
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
                "description": "Directory to save into, normally in "
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


# session_id -> estimated credits this chat has spent through these tools (successful creates only).
_SPENT: OrderedDict[str, int] = OrderedDict()
DEFAULT_SESSION_LIMIT = 100


def record_spend(session_id: str, credits: int) -> None:
    if not session_id or credits <= 0:
        return
    _SPENT[session_id] = _SPENT.get(session_id, 0) + credits
    _SPENT.move_to_end(session_id)
    while len(_SPENT) > _PLATFORMS_MAX:
        _SPENT.popitem(last=False)


def spent(session_id: str) -> int:
    """This chat's spend: counted in memory, and in the task history so it survives a restart."""
    return max(_SPENT.get(session_id or "", 0), history.spent(session_id))


def session_limit() -> int:
    """Credits a chat may spend before every paid call asks again: the ``session_limit`` setting,
    else ``TRIPO_SESSION_LIMIT``, else 100. 0 turns it off."""
    from .client import _secret

    raw = settings.get("session_limit", None)
    if raw is None:
        try:
            raw = _secret("TRIPO_SESSION_LIMIT").strip() or None
        except TripoError:
            raw = None
    return max(0, settings.as_int(raw, DEFAULT_SESSION_LIMIT)) if raw is not None else DEFAULT_SESSION_LIMIT


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
        preview = viewer.preview_url(output)
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
        record_spend(session_id, plan.credits)
        history.record_task(
            task_id,
            tool=tool,
            session_id=session_id,
            summary=plan.summary,
            parent=history.parent_of(plan.body),
            prompt=str((args or {}).get("prompt") or ""),
            credits=plan.credits,
        )
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


def _attach_hint(saved: list[dict], task_id: str, session_id: str) -> dict:
    """How to hand the saved files to the user on this surface.

    Chat platforms get MEDIA:<path> lines, the only way a file reaches the user there. Desktop does
    not: the inline viewer already shows the model, and a MEDIA: card for a .glb only offers a
    preview button that opens the binary.
    """
    surface = _surface(session_id)
    if surface == "desktop":
        return {
            "show_in_chat": directive(task_id),
            "deliver": "Put show_in_chat alone on its own line for the 3D viewer and give the saved path "
            "as text. Do not add MEDIA: lines for these files: Desktop cannot preview a model file.",
        }
    if surface in _TEXT_ONLY or surface in _NOT_CHAT:
        return {}
    lines = [f"MEDIA:{item['path']}" for item in sorted(saved, key=lambda item: item["file"] == "model")]
    return {
        "attach": lines,
        "attach_hint": "Put any of these lines alone in your reply to send the file to the chat.",
    }


def _tail(key: str, url: str) -> str:
    """What follows the stem in a saved file's name: ``.glb`` for the model, ``_<output>.<ext>`` otherwise."""
    if key == "model_url":
        return _extension(url, ".glb")
    return f"_{key.removesuffix('_url')}{_extension(url, '.bin')}"


def tripo_download(args: dict, session_id: str = "", **_: Any) -> str:
    args = args or {}
    task_id = str(args.get("task_id") or "").strip()
    if not task_id:
        return _err("task_id is required")
    raw_dir = str(args.get("save_dir") or "").strip()
    if raw_dir:
        target_dir = local_path(raw_dir)
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
            # Never overwrite: the agent picks save_dir, so it may hold earlier files of the same name.
            ordered = sorted(urls.items(), key=lambda item: item[0] != "model_url")
            tails = {key: _tail(key, url) for key, url in ordered}
            stem = free_stem(target_dir, stem, list(tails.values()))
            for key, url in ordered:
                label = key.removesuffix("_url")
                target = target_dir / f"{stem}{tails[key]}"
                target_dir.mkdir(parents=True, exist_ok=True)
                size = client.fetch(url, target)
                item = {"file": label, "path": str(target), "bytes": size}
                if target.suffix.lower() == ".glb" and (contents := glb.summarize(target)):
                    item["contains"] = contents
                if key == "model_url":
                    history.record_file(task_id, target)
                saved.append(item)
    except TripoError as exc:
        return _err(str(exc), code=exc.code)
    except OSError as exc:
        return _err(f"Could not write the file: {exc}")
    checked = [item["contains"] for item in saved if "contains" in item]
    note = (
        {"verify": "Describe only what 'contains' shows: skins/joints mean rigged, clips mean animated."}
        if checked
        else {}
    )
    return _ok(task_id=task_id, saved=saved, **note, **_attach_hint(saved, task_id, session_id))


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
