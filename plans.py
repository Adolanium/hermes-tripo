"""Turn tool arguments into Tripo v3 requests, and price them.

Everything here is pure: no network, no filesystem. The approval hook and the
tool handler both build the same ``Plan`` from the same arguments, so the cost
the user approves is computed from exactly the request that gets sent.

Prices are Tripo's published list (platform.tripo3d.ai/docs/billing, checked
2026-10-19). They are estimates: Tripo bills what the task actually consumes,
and the task result reports that as ``credits_consumed``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

GEOMETRY_MODELS = (
    "v3.1-20260211",
    "v3.0-20250812",
    "v2.5-20250123",
    "P1-20260311",
    "P2-20260801",
)
DEFAULT_GEOMETRY_MODEL = "v3.1-20260211"
FAST_TEXTURE_MODEL = "v3.5-20260815"
TEXTURE_MODELS = (FAST_TEXTURE_MODEL, "v3.0-20250812", "v2.5-20250123")
TEXTURE_QUALITIES = ("fast", "standard", "detailed", "extreme")
VIEWS = ("front", "left", "back", "right")
IMAGE_MODELS = (
    "seedream_v4",
    "seedream_v5",
    "banana",
    "banana_pro",
    "banana2",
    "chat_image_2",
    "chat_image_2.5_flare",
    "chat_image_2.5_sunburst",
)
IMAGE_TEMPLATES = ("asset_extraction", "character_completion", "t_pose", "variants", "figure")
RIG_MODELS = ("v1.0-20240301", "v2.5-20260210", "v3.0-20260909")
RIG_TYPES = ("biped", "quadruped", "hexapod", "octopod", "avian", "serpentine", "aquatic", "others")
RIG_SPECS = ("tripo", "mixamo", "actorcore", "unity", "unreal", "vrm")
CONVERT_FORMATS = ("GLTF", "FBX", "USDZ", "OBJ", "STL", "3MF")
RETOPOLOGY_MODELS = ("v1.0", "v2.0", "v3.0")
MAX_ANIMATIONS = 5

# Text / image-or-multiview, without and with texture.
_STANDARD_BASE = {"text": (10, 20), "image": (20, 30), "multiview": (20, 30)}
_P_SERIES_BASE = {"text": (30, 40), "image": (40, 50), "multiview": (40, 50)}
# On top of the textured price, which already includes standard texture.
_TEXTURE_SURCHARGE = {"fast": 0, "standard": 0, "detailed": 20, "extreme": 30}
_TEXTURE_TASK = {"fast": 10, "standard": 10, "detailed": 20, "extreme": 30}

# A string that is already a Tripo reference rather than a local path.
_REFERENCE = re.compile(
    r"^(task_|file_)\S+$|^https?://\S+$|^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


class PlanError(ValueError):
    """The arguments cannot become a valid request. Nothing was sent."""


@dataclass(frozen=True)
class Upload:
    """A local file the handler must upload; replaced by its file_token."""

    path: str
    kind: str  # "image" or "model"


@dataclass
class Plan:
    endpoint: str
    body: dict[str, Any]
    credits: int
    summary: str
    cost_note: str = ""
    notes: list[str] = field(default_factory=list)

    def cost_text(self) -> str:
        if self.credits == 0:
            return "free"
        text = f"about {self.credits} credits"
        return f"{text} ({self.cost_note})" if self.cost_note else text


def reference(value: Any, kind: str, name: str = "input") -> str | Upload:
    """A task_id, file_token or URL passes through; anything else is a local path."""
    if not isinstance(value, str) or not value.strip():
        raise PlanError(f"{name} must be a task_id, file_token, URL or local file path")
    text = value.strip()
    if _REFERENCE.match(text):
        return text
    return Upload(path=text, kind=kind)


def _choice(args: dict, key: str, options: tuple, default: Any = None) -> Any:
    value = args.get(key, default)
    if value is None or value in options:
        return value
    raise PlanError(f"{key} must be one of: {', '.join(options)}")


def _int(args: dict, key: str, low: int, high: int) -> int | None:
    value = args.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise PlanError(f"{key} must be a whole number from {low} to {high}")
    return value


def _put(body: dict, args: dict, *keys: str) -> None:
    for key in keys:
        if args.get(key) is not None:
            body[key] = args[key]


def generate_model(args: dict) -> Plan:
    prompt = (args.get("prompt") or "").strip()
    image = args.get("image")
    views = args.get("views")
    given = [name for name, value in (("prompt", prompt), ("image", image), ("views", views)) if value]
    if len(given) != 1:
        raise PlanError(
            "Pass exactly one of prompt (text to 3D), image (image to 3D) or views (multiview to 3D)"
        )

    model = _choice(args, "model", GEOMETRY_MODELS, DEFAULT_GEOMETRY_MODEL)
    p_series = model.startswith("P")
    v25 = model.startswith("v2.5")
    body: dict[str, Any] = {"model": model}
    notes: list[str] = []

    if prompt:
        mode, endpoint = "text", "/generation/text-to-model"
        if len(prompt) > 1024:
            raise PlanError("prompt is limited to 1024 characters")
        body["prompt"] = prompt
    elif image:
        mode, endpoint = "image", "/generation/image-to-model"
        body["input"] = reference(image, "image", "image")
    else:
        mode, endpoint = "multiview", "/generation/multiview-to-model"
        if not isinstance(views, dict) or set(views) - set(VIEWS):
            raise PlanError(f"views must be an object with keys from: {', '.join(VIEWS)}")
        present = [view for view in VIEWS if views.get(view)]
        if "front" not in present or len(present) < 2:
            raise PlanError("views needs the front view and at least one other view")
        body["inputs"] = [{view: reference(views[view], "image", f"views.{view}")} for view in present]

    negative = (args.get("negative_prompt") or "").strip()
    if negative:
        if mode != "text":
            raise PlanError("negative_prompt only applies to text to 3D")
        body["negative_prompt"] = negative[:255]

    parts = bool(args.get("generate_parts"))
    texture = args.get("texture", True) is not False
    # PBR defaults on and forces texture on, so "texture: false" alone must switch PBR off too,
    # or the caller is billed for a textured model they asked not to get.
    pbr = args.get("pbr", texture) is not False
    if parts:
        if p_series or v25:
            raise PlanError("generate_parts needs a v3.x model")
        # Tripo rejects parts with texture or PBR on (including the defaults).
        if args.get("texture") is True or args.get("pbr") is True:
            raise PlanError("generate_parts cannot be combined with texture or pbr")
        texture = pbr = False
        notes.append("generate_parts turns texture and PBR off; texture the parts afterwards if needed")
    textured = texture or pbr
    body["texture"] = textured
    body["pbr"] = pbr

    quality = _choice(args, "texture_quality", TEXTURE_QUALITIES)
    if quality and textured:
        body["texture_quality"] = quality
        if quality == "fast":
            body["texture_version"] = FAST_TEXTURE_MODEL

    geometry = _choice(args, "geometry_quality", ("standard", "detailed"))
    if geometry == "detailed":
        if p_series or v25:
            raise PlanError("geometry_quality=detailed needs a v3.x model")
        body["geometry_quality"] = geometry

    quad = bool(args.get("quad"))
    smart_low_poly = bool(args.get("smart_low_poly"))
    if smart_low_poly and parts:
        raise PlanError("smart_low_poly and generate_parts cannot be combined")
    if v25 and (quad or smart_low_poly or parts or args.get("auto_size")):
        raise PlanError("quad, smart_low_poly, generate_parts and auto_size need a v3.x or P model")
    if quad:
        body["quad"] = True
        notes.append("quad output is always FBX")
    if smart_low_poly:
        if p_series:
            raise PlanError("smart_low_poly is for v3.x models; P models are low-poly already")
        body["smart_low_poly"] = True
    if parts:
        body["generate_parts"] = True

    face_limit = _int(args, "face_limit", 48, 2_000_000)
    if face_limit is not None:
        body["face_limit"] = face_limit
    _put(body, args, "auto_size", "model_seed", "texture_seed")

    base = (_P_SERIES_BASE if p_series else _STANDARD_BASE)[mode][1 if textured else 0]
    credits = base
    if textured and quality:
        credits += _TEXTURE_SURCHARGE[quality]
    credits += 20 if body.get("geometry_quality") == "detailed" else 0
    credits += 5 if quad else 0
    credits += 10 if smart_low_poly else 0
    credits += 20 if parts else 0

    label = {"text": "text to 3D", "image": "image to 3D", "multiview": "multiview to 3D"}[mode]
    details = [model, "textured" if textured else "untextured"]
    if textured and quality and quality != "standard":
        details.append(f"{quality} texture")
    for flag, text in (
        (body.get("geometry_quality"), "detailed geometry"),
        (quad, "quad"),
        (smart_low_poly, "smart low-poly"),
        (parts, "parts"),
    ):
        if flag:
            details.append(text)
    return Plan(endpoint, body, credits, f"{label} ({', '.join(details)})", notes=notes)


def generate_image(args: dict) -> Plan:
    prompt = (args.get("prompt") or "").strip()
    if not prompt:
        raise PlanError("prompt is required")
    body: dict[str, Any] = {"prompt": prompt}
    model = _choice(args, "model", IMAGE_MODELS)
    template = _choice(args, "template", IMAGE_TEMPLATES)
    if model:
        body["model"] = model
    if template:
        body["template"] = template
    _put(body, args, "size", "aspect_ratio")
    note = ""
    if model and model.startswith("chat_image"):
        note = "GPT image models can cost more"
    summary = "text to image" + (f" ({template})" if template else "") + (f" with {model}" if model else "")
    return Plan("/generation/text-to-image", body, 5, summary, cost_note=note)


def texture(args: dict) -> Plan:
    body: dict[str, Any] = {"input": reference(args.get("input"), "model")}
    model = _choice(args, "model", TEXTURE_MODELS)
    quality = _choice(args, "texture_quality", TEXTURE_QUALITIES, "standard")
    if quality == "fast":
        if model not in (None, FAST_TEXTURE_MODEL):
            raise PlanError(f"texture_quality=fast needs model {FAST_TEXTURE_MODEL}")
        model = FAST_TEXTURE_MODEL
    if model:
        body["model"] = model
    body["texture_quality"] = quality

    prompt = (args.get("prompt") or "").strip()
    image = args.get("image")
    style_image = args.get("style_image")
    if prompt and image:
        raise PlanError("Pass prompt or image as texture guidance, not both")
    if style_image and not prompt:
        raise PlanError("style_image goes with a text prompt")
    guidance: dict[str, Any] = {}
    if prompt:
        guidance["text"] = prompt
    if image:
        guidance["image"] = reference(image, "image", "image")
    if style_image:
        guidance["style_image"] = reference(style_image, "image", "style_image")
    if guidance:
        body["texture_prompt"] = guidance
    _put(body, args, "pbr", "texture_seed")

    credits = _TEXTURE_TASK[quality] + (5 if style_image else 0)
    return Plan("/models/texture", body, credits, f"retexture ({quality})")


def rig_check(args: dict) -> Plan:
    body = {"input": reference(args.get("input"), "model")}
    return Plan("/animations/rig-check", body, 0, "rig check")


def rig(args: dict) -> Plan:
    body: dict[str, Any] = {"input": reference(args.get("input"), "model")}
    model = _choice(args, "model", RIG_MODELS)
    rig_type = _choice(args, "rig_type", RIG_TYPES)
    spec = _choice(args, "spec", RIG_SPECS)
    out_format = _choice(args, "out_format", ("glb", "fbx"))
    if rig_type and rig_type != "biped" and model in (None, "v1.0-20240301"):
        # v1.0, Tripo's default, rigs bipeds only.
        model = "v2.5-20260210" if rig_type != "others" else "v3.0-20260909"
    if rig_type == "others" and model != "v3.0-20260909":
        raise PlanError("rig_type=others needs rig model v3.0-20260909")
    if spec in ("actorcore", "unity", "unreal", "vrm") and model in (None, "v1.0-20240301"):
        model = "v3.0-20260909"
    for key, value in (("model", model), ("rig_type", rig_type), ("spec", spec), ("out_format", out_format)):
        if value:
            body[key] = value
    summary = f"auto-rig ({rig_type or 'biped'}, {model or 'v1.0-20240301'})"
    return Plan("/animations/rig", body, 25, summary)


def animate(args: dict) -> Plan:
    source = args.get("input")
    if not isinstance(source, str) or not source.strip().startswith("task_"):
        raise PlanError("input must be the task_id of a finished tripo_rig task")
    animations = args.get("animations")
    if isinstance(animations, str):
        animations = [animations]
    if not isinstance(animations, list) or not animations or not all(isinstance(a, str) for a in animations):
        raise PlanError("animations must be a list of preset ids such as preset:walk")
    animations = [a.strip() if a.strip().startswith("preset:") else f"preset:{a.strip()}" for a in animations]
    if len(animations) > MAX_ANIMATIONS:
        raise PlanError(f"At most {MAX_ANIMATIONS} animations per task")
    body: dict[str, Any] = {"input": source.strip()}
    if len(animations) == 1:
        body["animation"] = animations[0]
    else:
        body["animations"] = animations
    _choice(args, "out_format", ("glb", "fbx"))
    _put(body, args, "out_format", "animate_in_place", "export_with_geometry")
    count = len(animations)
    return Plan("/animations/retarget", body, 10 * count, f"animate ({', '.join(animations)})")


def retopology(args: dict) -> Plan:
    body: dict[str, Any] = {"input": reference(args.get("input"), "model")}
    model = _choice(args, "model", RETOPOLOGY_MODELS, "v3.0")
    face_limit = _int(args, "face_limit", 48, 2_000_000)
    if face_limit is None:
        raise PlanError("face_limit (the target polycount) is required")
    quad = bool(args.get("quad"))
    smart_uv = bool(args.get("smart_uv"))
    if smart_uv and model != "v3.0":
        raise PlanError("smart_uv needs model v3.0")
    body.update(model=model, face_limit=face_limit)
    if quad:
        body["quad"] = True
    if smart_uv:
        body["smart_uv"] = True
    credits = (40 if quad else 35) if model == "v3.0" else 30
    summary = f"retopology to {face_limit:,} faces ({model}{', quad' if quad else ''}{', smart UV' if smart_uv else ''})"
    return Plan("/mesh/decimate", body, credits, summary)


_CONVERT_OPTIONS = (
    "face_limit",
    "quad",
    "texture_size",
    "texture_format",
    "scale_factor",
    "pivot_to_center_bottom",
    "flatten_bottom",
    "export_orientation",
    "fbx_preset",
    "with_animation",
    "animate_in_place",
)


def convert(args: dict) -> Plan:
    body: dict[str, Any] = {"input": reference(args.get("input"), "model")}
    fmt = args.get("format")
    fmt = fmt.upper() if isinstance(fmt, str) else fmt
    if fmt not in CONVERT_FORMATS:
        raise PlanError(f"format must be one of: {', '.join(CONVERT_FORMATS)}")
    body["format"] = fmt
    _choice(args, "export_orientation", ("+x", "-x", "+y", "-y"))
    _choice(args, "fbx_preset", ("blender", "3dsmax", "mixamo", "bake_scale"))
    _put(body, args, *_CONVERT_OPTIONS)
    advanced = any(key in body for key in _CONVERT_OPTIONS)
    credits = 10 if advanced else 5
    return Plan("/models/convert", body, credits, f"convert to {fmt}{' with processing' if advanced else ''}")


BUILDERS = {
    "tripo_generate_model": generate_model,
    "tripo_generate_image": generate_image,
    "tripo_texture": texture,
    "tripo_rig_check": rig_check,
    "tripo_rig": rig,
    "tripo_animate": animate,
    "tripo_retopology": retopology,
    "tripo_convert": convert,
}

# Tools that spend credits. rig_check creates a task but is free.
PAID_TOOLS = frozenset(BUILDERS) - {"tripo_rig_check"}
