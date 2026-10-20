"""What the Desktop 3D viewer reads: a task's state, its model in chunks, its preview image.

The Desktop plugin bridge only carries JSON, so files travel as base64 chunks. Each task's
files are fetched from Tripo once and cached under ``<HERMES_HOME>/tripo/<task_id>/.viewer/``;
every request after that is served from disk. Reads are free on Tripo's side.
"""

from __future__ import annotations

import base64
import os
import re
import shutil
import sys
import threading
from pathlib import Path
from typing import Any

from .client import TERMINAL, Client, TripoError, free_stem, tripo_home
from .plans import is_task_id

CHUNK_BYTES = 2 * 1024 * 1024
VIEWABLE = {".glb", ".fbx", ".obj", ".stl"}
# Everything Tripo exports (convert outputs included); saved as-is, only VIEWABLE ones render.
DOWNLOADABLE = VIEWABLE | {".usdz", ".3mf", ".gltf", ".zip"}
IMAGE_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


# The picture that stands for a task: a render, a generated image, the front of a multiview set,
# or a UV layout, in that order.
_PREVIEW_KEYS = ("rendered_image_url", "image_url", "front_view_url", "uv_render_url")


def preview_url(output: dict) -> str | None:
    return next((output[key] for key in _PREVIEW_KEYS if isinstance(output.get(key), str)), None)


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
        "has_preview": bool(preview_url(output)),
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
            url = preview_url(output)
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


# Windows' known-folder id for Downloads, as stored under User Shell Folders.
_WINDOWS_DOWNLOADS = "{374DE290-123F-4565-9164-39C4925E467B}"
_SHELL_FOLDERS = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"


def _system_downloads() -> Path | None:
    """The Downloads folder the OS reports, which the user may have moved off the home folder."""
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _SHELL_FOLDERS) as handle:
                value, _ = winreg.QueryValueEx(handle, _WINDOWS_DOWNLOADS)
        except OSError:
            return None
        path = Path(os.path.expandvars(value)) if isinstance(value, str) and value else None
        return path if path and path.is_absolute() else None
    # Linux and other XDG desktops: XDG_DOWNLOAD_DIR in the environment, else user-dirs.dirs.
    value = os.environ.get("XDG_DOWNLOAD_DIR", "")
    if not value:
        config = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "user-dirs.dirs"
        try:
            match = re.search(
                r'^XDG_DOWNLOAD_DIR="([^"]+)"', config.read_text(encoding="utf-8"), re.MULTILINE
            )
        except OSError:
            match = None
        value = match.group(1) if match else ""
    value = value.replace("$HOME", str(Path.home()))
    return Path(value) if value and Path(value).is_absolute() else None


def downloads_dir() -> Path:
    """``<Downloads>/Tripo``, where the viewer's free 'save original' puts files. Downloads is the
    folder the OS reports (the Windows known folder, XDG on Linux), else ``~/Downloads``."""
    return (_system_downloads() or Path.home() / "Downloads") / "Tripo"


def save_original(client: Client, task_id: str, name: str = "") -> dict[str, Any]:
    """Copy the model the viewer already holds into Downloads/Tripo. Free: no Tripo call when cached.

    Never overwrites: an existing name gets " (2)", " (3)", ... appended.
    """
    source = _cached(client, task_id, "model", DOWNLOADABLE)
    stem = " ".join(_SAFE_NAME.sub(" ", name or "").split()).strip(" .")[:80] or f"tripo-{task_id[-12:]}"
    folder = downloads_dir()
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{free_stem(folder, stem, [source.suffix])}{source.suffix}"
    shutil.copyfile(source, target)
    return {
        "path": str(target),
        "folder": str(folder),
        "bytes": target.stat().st_size,
        "format": source.suffix.lstrip("."),
    }


LIBRARY_LIMIT = 60
_TYPE_LABELS = {
    "text_to_model": "Text to 3D",
    "image_to_model": "Image to 3D",
    "multiview_to_model": "Multiview to 3D",
    "text_to_image": "Concept image",
    "image_to_image": "Image edit",
    "generate_image": "Image edit",
    "generate_multiview_image": "Four views",
    "edit_multiview_image": "Edited views",
    "texture_model": "Retexture",
    "animate_prerigcheck": "Rig check",
    "rig_check": "Rig check",
    "animate_rig": "Rig",
    "rig": "Rig",
    "animate_retarget": "Animation",
    "retarget": "Animation",
    "convert_model": "Convert",
    "convert": "Convert",
    "mesh_segmentation": "Parts",
    "mesh_completion": "Part completion",
    "highpoly_to_lowpoly": "Retopology",
    "mesh_uv": "Smart UV",
}


def library(client: Client) -> dict[str, Any]:
    """Recent tasks on this Tripo account, newest first, for the Desktop library pane."""
    items = []
    for row in client.usage():
        if not isinstance(row, dict) or not is_task_id(str(row.get("task_id") or "")):
            continue
        kind = str(row.get("type") or "")
        items.append(
            {
                "task_id": row["task_id"],
                "type": kind,
                "label": _TYPE_LABELS.get(kind, kind.replace("_", " ").capitalize() or "Task"),
                "credits": row.get("credits_consumed"),
                "created_at": row.get("created_at"),
            }
        )
    items.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return {"items": items[:LIBRARY_LIMIT], "total": len(items)}
