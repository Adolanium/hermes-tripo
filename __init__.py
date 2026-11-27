"""Tripo 3D for Hermes Agent.

Registration and the spend gate live here; requests are built in plans.py,
HTTP in client.py, tool schemas and handlers in tools.py.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from . import plans, settings
from .client import api_key
from .tools import EMOJI, HANDLERS, SCHEMAS, TOOLSET, remember_platform, session_limit, spent

logger = logging.getLogger(__name__)

__version__ = "0.4.0"


def _has_key() -> bool:
    try:
        return bool(api_key())
    except Exception:
        return False


def _file_names(files: list[plans.Upload]) -> str:
    names = [Path(item.path).name or item.path for item in files]
    return ", ".join(names[:3]) + (f" and {len(names) - 3} more" if len(names) > 3 else "")


# Tripo's own MCP server (Tripo Studio MCP, https://msapi.tripo3d.ai/mcp) spends the user's Studio
# credits. These are its tools that only read, or that Tripo documents as free; every other tool it
# has, or adds later, is treated as spending.
STUDIO_FREE = frozenset(
    {
        "tripo_cancel_model_task",
        "tripo_copy_model",
        "tripo_create_image_upload_grant",
        "tripo_create_model_upload_grant",
        "tripo_get_image_asset",
        "tripo_get_image_task",
        "tripo_get_image_task_by_idempotency_key",
        "tripo_get_model_asset",
        "tripo_get_model_export",
        "tripo_get_model_task",
        "tripo_get_model_task_by_idempotency_key",
        "tripo_get_model_task_progress",
        "tripo_get_motion_asset",
        "tripo_list_active_model_tasks",
        "tripo_list_image_assets",
        "tripo_list_model_assets",
        "tripo_list_model_exports",
        "tripo_list_model_tasks",
        "tripo_list_motion_assets",
        "tripo_list_motion_presets",
    }
)


def studio_tool(tool_name: str) -> str | None:
    """The Tripo Studio MCP tool behind a Hermes MCP tool name (``mcp__<server>__tripo_...``), else
    None. The server part is whatever the user called the server, so only the tool part is matched."""
    if not tool_name.startswith("mcp__"):
        return None
    tool = tool_name.rpartition("__")[2]
    return tool if tool.startswith("tripo_") else None


def _studio_gate(tool: str) -> dict | None:
    if tool in STUDIO_FREE:
        return None
    label = tool.removeprefix("tripo_").replace("_", " ")
    return {
        "action": "approve",
        "message": f"Tripo Studio {label}: spends credits from your Tripo Studio plan "
        "(Tripo's MCP server does not say how many)",
        "rule_key": f"tripo:studio:{tool}",
    }


def spend_gate(tool_name: str = "", args: dict | None = None, session_id: str = "", **_: Any) -> dict | None:
    """``pre_tool_call``: send every credit-spending Tripo call, and every local-file upload, to
    Hermes' approval prompt.

    The prompt names the request, its estimated cost, any local files it uploads, and what this chat
    has spent so far. ``rule_key`` is per tool, so "allow for this session" covers later calls to the
    same tool, until the chat's spend would pass the ``session_limit`` setting (default 100 credits): from
    then on each call gets its own key, so the user is asked again every time. A free call that would
    upload a local file (``tripo_rig_check`` with a path) asks too. Unattended runs (cron,
    ``chat -q``, a gateway nobody answers) cannot approve and the call is refused.

    Calls to Tripo's own MCP server, when the user has connected it, ask too. They spend Studio
    credits, and that server gives no price, so the prompt has no estimate and the per-chat limit
    does not count them.
    """
    studio = studio_tool(tool_name)
    if studio:
        return _studio_gate(studio)
    if tool_name not in plans.BUILDERS:
        return None
    try:
        plan = plans.BUILDERS[tool_name](args or {})
    except plans.PlanError:
        return None  # the handler refuses the call before anything is sent
    except Exception:
        logger.debug("tripo: could not price %s", tool_name, exc_info=True)
        return {
            "action": "approve",
            "message": f"Tripo {tool_name} may spend credits or upload files (could not read the request)",
            "rule_key": f"tripo:{tool_name}",
        }
    files = plans.uploads(plan.body)
    if tool_name not in plans.PAID_TOOLS:
        if not files:
            return None
        return {
            "action": "approve",
            "message": f"Tripo {plan.summary} is free, but uploads {_file_names(files)} to Tripo",
            "rule_key": f"tripo:upload:{tool_name}",
        }
    so_far = spent(session_id)
    limit = session_limit()
    message = f"Tripo {plan.summary}: costs {plan.cost_text()} of your Tripo balance"
    if files:
        message += f" and uploads {_file_names(files)} to Tripo"
    if so_far:
        message += f"; this chat has used about {so_far} so far"
    rule_key = f"tripo:{tool_name}"
    if limit and so_far + plan.credits > limit:
        message += f" (over your {limit}-credit limit per chat, so Hermes asks every time)"
        rule_key = f"tripo:over-limit:{session_id or 'unknown'}:{so_far}"
    return {"action": "approve", "message": message, "rule_key": rule_key}


def note_platform(session_id: str = "", platform: str = "", **_: Any) -> None:
    """``pre_llm_call``: remember where each session is shown, so tool results can say how to show a
    model there (a viewer line on Desktop, attachments on chat platforms). Injects nothing."""
    remember_platform(session_id, platform)


def register(ctx: Any) -> None:
    settings.bind(ctx)
    for name, handler in HANDLERS.items():
        # tripo_balance stays visible without a key so the model can tell the user what is missing.
        keyless = name == "tripo_balance"
        ctx.register_tool(
            name=name,
            toolset=TOOLSET,
            schema=SCHEMAS[name],
            handler=handler,
            check_fn=None if keyless else _has_key,
            requires_env=None if keyless else ["TRIPO_API_KEY"],
            emoji=EMOJI.get(name, "🧊"),
        )
    ctx.register_hook("pre_tool_call", spend_gate)
    ctx.register_hook("pre_llm_call", note_platform)

    skill = Path(__file__).parent / "skills" / "workflow" / "SKILL.md"
    if skill.is_file():
        try:
            ctx.register_skill(
                "workflow",
                skill,
                description="Tripo 3D workflows: generate, texture, "
                "rig, animate, convert and download models",
            )
        except Exception as exc:
            logger.warning("tripo: could not register skill: %s", exc)
