"""Routes for the Desktop viewer: /api/plugins/hermes-tripo/{task,model,preview}/<task_id>.

Every answer is HTTP 200 JSON, ``{"ok": true, ...}`` or ``{"ok": false, "error": ...}``, so the
viewer handles failures in one place. Handlers are plain ``def``: FastAPI runs them in its
threadpool, which keeps the blocking Tripo calls off the event loop.
"""

import importlib
import importlib.util
import sys
from pathlib import Path

from fastapi import APIRouter

# Load the plugin package under a private name: the agent loader's module name is its own business.
_ROOT = Path(__file__).resolve().parents[1]
_NAME = "_hermes_tripo_http"
if _NAME not in sys.modules:
    _spec = importlib.util.spec_from_file_location(
        _NAME, _ROOT / "__init__.py", submodule_search_locations=[str(_ROOT)]
    )
    _module = importlib.util.module_from_spec(_spec)
    sys.modules[_NAME] = _module
    _spec.loader.exec_module(_module)
client = importlib.import_module(_NAME + ".client")
viewer = importlib.import_module(_NAME + ".viewer")

router = APIRouter()


def _answer(work):
    try:
        with client.Client() as tripo:
            return {"ok": True, **work(tripo)}
    except client.TripoError as exc:
        return {"ok": False, "error": str(exc), "status": exc.status}


@router.get("/task/{task_id}")
def task(task_id: str):
    return _answer(lambda tripo: viewer.task_state(tripo, task_id))


@router.get("/model/{task_id}")
def model(task_id: str, chunk: int = 0):
    return _answer(lambda tripo: viewer.model_chunk(tripo, task_id, chunk))


@router.get("/preview/{task_id}")
def preview(task_id: str):
    return _answer(lambda tripo: viewer.preview(tripo, task_id))
