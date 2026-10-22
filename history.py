"""Where each Tripo task came from: the chat that made it, the tool, the prompt, the task it was
made from, its cost, and the files saved from it.

Kept in ``<HERMES_HOME>/tripo/history.json``, newest last, at most ``MAX_TASKS`` tasks. Only tasks
made through these tools are known; the library still lists the rest from the Tripo account.
Writes are best-effort: a failed write never fails the tool call that triggered it.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import UTC, datetime
from pathlib import Path

from .client import tripo_home
from .plans import is_task_id

logger = logging.getLogger(__name__)

MAX_TASKS = 2000
MAX_FILES = 20
MAX_CHAIN = 8
PROMPT_CHARS = 400
_lock = threading.Lock()

# What each tool makes, for the "made from" chain.
LABELS = {
    "tripo_generate_model": "3D model",
    "tripo_generate_image": "Image",
    "tripo_multiview": "Four views",
    "tripo_texture": "Retexture",
    "tripo_rig_check": "Rig check",
    "tripo_rig": "Rig",
    "tripo_animate": "Animation",
    "tripo_retopology": "Low-poly",
    "tripo_convert": "Convert",
    "tripo_parts": "Parts",
    "tripo_uv": "Smart UV",
}


def _path() -> Path:
    return tripo_home() / "history.json"


def _read() -> dict[str, dict]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    tasks = data.get("tasks") if isinstance(data, dict) else None
    return {key: value for key, value in (tasks or {}).items() if isinstance(value, dict)}


def _write(tasks: dict[str, dict]) -> None:
    while len(tasks) > MAX_TASKS:
        tasks.pop(next(iter(tasks)))
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps({"version": 1, "tasks": tasks}, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, path)


def parent_of(body: dict) -> str | None:
    """The task a request builds on (rig from a model, animation from a rig...), if it names one."""
    for key in ("input", "original_model_task_id"):
        value = body.get(key)
        if isinstance(value, str) and is_task_id(value.strip()):
            return value.strip()
    for item in body.get("inputs") or []:
        value = item.get("task_id") if isinstance(item, dict) else item
        if isinstance(value, str) and is_task_id(value.strip()):
            return value.strip()
    return None


def record_task(
    task_id: str,
    *,
    tool: str,
    session_id: str = "",
    summary: str = "",
    parent: str | None = None,
    prompt: str = "",
    credits: int = 0,
) -> None:
    record = {
        "task_id": task_id,
        "tool": tool,
        "label": LABELS.get(tool, tool.removeprefix("tripo_").replace("_", " ").capitalize()),
        "session_id": session_id or None,
        "summary": summary,
        "parent": parent,
        "prompt": (prompt or "").strip()[:PROMPT_CHARS] or None,
        "credits": credits,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "files": [],
    }
    try:
        with _lock:
            tasks = _read()
            tasks.pop(task_id, None)
            tasks[task_id] = record
            _write(tasks)
    except OSError:
        logger.warning("tripo: could not record task %s", task_id, exc_info=True)


def record_file(task_id: str, path: str | Path) -> None:
    """Note a file saved from a task, so the library can list it under 'On this machine'."""
    text = str(path)
    try:
        with _lock:
            tasks = _read()
            record = tasks.get(task_id)
            if record is None:
                record = {"task_id": task_id, "label": "Task", "files": []}
                tasks[task_id] = record
            files = [item for item in record.get("files") or [] if item != text]
            record["files"] = (files + [text])[-MAX_FILES:]
            _write(tasks)
    except OSError:
        logger.warning("tripo: could not record a file for %s", task_id, exc_info=True)


def get(task_id: str) -> dict | None:
    return _read().get(task_id)


def chain(task_id: str, tasks: dict[str, dict] | None = None) -> list[dict]:
    """The task and what it was made from, oldest first: model, then rig, then animation..."""
    tasks = _read() if tasks is None else tasks
    steps: list[dict] = []
    current = tasks.get(task_id)
    while current and len(steps) < MAX_CHAIN:
        steps.append(current)
        parent = current.get("parent")
        current = tasks.get(parent) if parent and all(step["task_id"] != parent for step in steps) else None
    return list(reversed(steps))


def origin(task_id: str, tasks: dict[str, dict] | None = None) -> dict | None:
    """The first step of the chain that knows its chat and prompt."""
    for step in chain(task_id, tasks):
        if step.get("session_id") or step.get("prompt"):
            return step
    return None


def spent(session_id: str) -> int:
    """Credits this chat has spent through these tools, as estimated when each call was made."""
    if not session_id:
        return 0
    return sum(
        int(record.get("credits") or 0)
        for record in _read().values()
        if record.get("session_id") == session_id
    )


def known() -> dict[str, bool]:
    """Every recorded task id -> whether its chain reaches a chat (so the library can say so)."""
    tasks = _read()
    return {task_id: origin(task_id, tasks) is not None for task_id in tasks}


def files() -> dict[str, str]:
    """Saved file path -> the task it came from."""
    found: dict[str, str] = {}
    for task_id, record in _read().items():
        for path in record.get("files") or []:
            found[str(path)] = task_id
    return found
