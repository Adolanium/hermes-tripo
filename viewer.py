"""What the Desktop 3D viewer reads: a task's state, its model in chunks, its preview image.

The Desktop plugin bridge only carries JSON, so files travel as base64 chunks. Each task's
files are fetched from Tripo once and cached under ``<HERMES_HOME>/tripo/<task_id>/.viewer/``;
every request after that is served from disk. Reads are free on Tripo's side.
"""

from __future__ import annotations

import base64
import re
import shutil
import threading
from pathlib import Path
from typing import Any

from .client import TERMINAL, Client, TripoError, tripo_home
from .plans import is_task_id

CHUNK_BYTES = 2 * 1024 * 1024
VIEWABLE = {".glb", ".fbx", ".obj", ".stl"}
# Everything Tripo exports (convert outputs included); saved as-is, only VIEWABLE ones render.
DOWNLOADABLE = VIEWABLE | {".usdz", ".3mf", ".gltf", ".zip"}
IMAGE_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def check_task_id(task_id: str) -> str:
    if not is_task_id(task_id):
        raise TripoError("Not a Tripo task id", status=400)
    return task_id


def _lock(task_id: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(task_id, threading.Lock())


def _suffix(url: str) -> str:
    return Path(url.split("?", 1)[0]).suffix.lower()


def _cache_dir(task_id: str) -> Path:
    return tripo_home() / task_id / ".viewer"


def task_state(client: Client, task_id: str) -> dict[str, Any]:
    """Status for the card: progress while running, what can be shown once done."""
    data = client.task(check_task_id(task_id))
    output = data.get("output") if isinstance(data.get("output"), dict) else {}
    model_url = output.get("model_url") or ""
    state: dict[str, Any] = {
        "task_id": task_id,
        "type": data.get("type"),
        "status": data.get("status"),
        "progress": data.get("progress"),
        "done": data.get("status") in TERMINAL,
        "credits_consumed": data.get("credits_consumed"),
        "model_format": _suffix(model_url).lstrip(".") or None,
        "viewable": _suffix(model_url) in VIEWABLE,
        "has_preview": bool(output.get("rendered_image_url") or output.get("image_url")),
        "downloads_dir": str(downloads_dir()),
    }
    for key in ("riggable", "rig_type"):
        if key in output:
            state[key] = output[key]
    if data.get("error_message"):
        state["error_message"] = data["error_message"]
    return state


def _cached(client: Client, task_id: str, key: str, allowed: set[str] | dict[str, str]) -> Path:
    """The task's output ``key``, downloaded once. Raises when the task has no such file."""
    check_task_id(task_id)
    folder = _cache_dir(task_id)
    with _lock(task_id):
        for existing in folder.glob(f"{key}.*") if folder.is_dir() else ():
            if existing.suffix.lower() in allowed and existing.stat().st_size > 0:
                return existing
        data = client.task(task_id)
        if data.get("status") != "success":
            raise TripoError(f"Task is {data.get('status')}", status=409)
        output = data.get("output") if isinstance(data.get("output"), dict) else {}
        if key == "model":
            url = output.get("model_url")
        else:
            url = output.get("rendered_image_url") or output.get("image_url")
        if not isinstance(url, str) or not url.startswith(("https://", "http://")):
            raise TripoError(f"Task has no {key} file", status=404)
        suffix = _suffix(url)
        if suffix not in allowed:
            raise TripoError(f"Cannot show a {suffix or 'file without extension'} in the viewer", status=415)
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{key}{suffix}"
        client.fetch(url, target)
        return target


def model_chunk(client: Client, task_id: str, index: int) -> dict[str, Any]:
    path = _cached(client, task_id, "model", VIEWABLE)
    size = path.stat().st_size
    chunks = max(1, -(-size // CHUNK_BYTES))
    if not 0 <= index < chunks:
        raise TripoError("Chunk out of range", status=416)
    with path.open("rb") as handle:
        handle.seek(index * CHUNK_BYTES)
        data = handle.read(CHUNK_BYTES)
    return {
        "format": path.suffix.lstrip("."),
        "size": size,
        "chunks": chunks,
        "index": index,
        "data": base64.b64encode(data).decode("ascii"),
    }


def preview(client: Client, task_id: str) -> dict[str, Any]:
    path = _cached(client, task_id, "preview", IMAGE_MIME)
    mime = IMAGE_MIME[path.suffix.lower()]
    return {"data_url": f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"}


_SAFE_NAME = re.compile(r"[^A-Za-z0-9._ -]+")


def downloads_dir() -> Path:
    """``<home>/Downloads/Tripo``: where the viewer's free 'save original' puts files."""
    return Path.home() / "Downloads" / "Tripo"


def save_original(client: Client, task_id: str, name: str = "") -> dict[str, Any]:
    """Copy the model the viewer already holds into Downloads/Tripo. Free: no Tripo call when cached.

    Never overwrites: an existing name gets " (2)", " (3)", ... appended.
    """
    source = _cached(client, task_id, "model", DOWNLOADABLE)
    stem = " ".join(_SAFE_NAME.sub(" ", name or "").split()).strip(" .")[:80] or f"tripo-{task_id[-12:]}"
    folder = downloads_dir()
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{stem}{source.suffix}"
    count = 2
    while target.exists():
        target = folder / f"{stem} ({count}){source.suffix}"
        count += 1
    shutil.copyfile(source, target)
    return {
        "path": str(target),
        "folder": str(folder),
        "bytes": target.stat().st_size,
        "format": source.suffix.lstrip("."),
    }
