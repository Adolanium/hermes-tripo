"""Tripo 3D for Hermes Agent.

Registration and the spend gate live here; requests are built in plans.py,
HTTP in client.py, tool schemas and handlers in tools.py.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from . import plans
from .client import api_key
from .tools import EMOJI, HANDLERS, SCHEMAS, TOOLSET, remember_platform

logger = logging.getLogger(__name__)

__version__ = "0.2.0"


def _has_key() -> bool:
    try:
        return bool(api_key())
    except Exception:
        return False


def spend_gate(tool_name: str = "", args: dict | None = None, **_: Any) -> dict | None:
    """``pre_tool_call``: send every credit-spending Tripo call to Hermes' approval prompt.

    The prompt names the request and its estimated cost. ``rule_key`` is per
    tool, so "allow for this session" covers later calls to the same tool.
    Unattended runs (cron, ``chat -q``, a gateway nobody answers) cannot
    approve and the call is refused, which is the point.
    """
    if tool_name not in plans.PAID_TOOLS:
        return None
    try:
        plan = plans.BUILDERS[tool_name](args or {})
    except plans.PlanError:
        return None  # the handler refuses the call before anything is sent
    except Exception:
        logger.debug("tripo: could not price %s", tool_name, exc_info=True)
        return {
            "action": "approve",
            "message": f"Tripo {tool_name} spends credits (cost unknown)",
            "rule_key": f"tripo:{tool_name}",
        }
    return {
        "action": "approve",
        "message": f"Tripo {plan.summary}: costs {plan.cost_text()} of your Tripo balance",
        "rule_key": f"tripo:{tool_name}",
    }


def note_platform(session_id: str = "", platform: str = "", **_: Any) -> None:
    """``pre_llm_call``: remember where each session is shown, so tool results can say how to show a
    model there (a viewer line on Desktop, attachments on chat platforms). Injects nothing."""
    remember_platform(session_id, platform)


def register(ctx: Any) -> None:
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
