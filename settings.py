"""The plugin's settings: Settings → Plugins in Hermes (``config_schema`` in plugin.yaml), stored as
``plugins.entries.hermes-tripo.settings.<key>``. Unset keys fall back to an env var where one
existed before settings did, then to the default."""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

PLUGIN_ID = "hermes-tripo"
_ctx: Any = None


def bind(ctx: Any) -> None:
    """Remember the agent's PluginContext; the Desktop backend has none and reads config.yaml."""
    global _ctx
    _ctx = ctx


def _stored(key: str) -> Any:
    if _ctx is not None and hasattr(_ctx, "get_config"):
        try:
            return _ctx.get_config(key, None)
        except Exception:
            logger.debug("tripo: get_config(%s) failed", key, exc_info=True)
    try:
        from hermes_cli.config import load_config_readonly
    except ImportError:  # outside Hermes (tests, scripts)
        return None
    try:
        config = load_config_readonly() or {}
    except Exception:
        return None
    entry = ((config.get("plugins") or {}).get("entries") or {}).get(PLUGIN_ID) or {}
    values = entry.get("settings") if isinstance(entry, dict) else None
    return values.get(key) if isinstance(values, dict) else None


def get(key: str, default: Any, *, env: str = "") -> Any:
    value = _stored(key)
    if value is None and env:
        value = os.environ.get(env) or None
    return default if value is None else value


def as_int(value: Any, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default
