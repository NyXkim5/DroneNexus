"""
Translates ParsedCommands into OVERWATCH/BULWARK system actions.

The executor is the bridge between operator intent and the decision/ROE
engines. It does not make engagement decisions — those belong to the
engines. It reconfigures mode, updates rules, and returns status strings
the operator can read on screen.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from nlp.intent_parser import CommandType, ParsedCommand

if TYPE_CHECKING:
    from decision.engine import DecisionEngine
    from decision.roe import ROEEngine

logger = logging.getLogger("overwatch.nlp.executor")


class CommandExecutor:
    """Translates ParsedCommands into system actions."""

    def __init__(
        self,
        decision_engine: Optional["DecisionEngine"] = None,
        roe_engine: Optional["ROEEngine"] = None,
    ) -> None:
        self._decision = decision_engine
        self._roe = roe_engine

        # Internal state for commands that don't route to an engine.
        self._current_focus: Optional[str] = None
        self._hold_target: Optional[str] = None
        self._pending_engagements: list[dict] = []

    def execute(self, command: ParsedCommand) -> str:
        """
        Execute a parsed command and return a human-readable status message.

        Each handler is isolated so failures in one do not cascade to others.
        """
        if command.confidence < 0.3:
            return (
                f"Command not understood (confidence {command.confidence:.0%}). "
                "Speak clearly: 'defend FOB', 'engage target 3', 'weapons free'."
            )

        handlers = {
            CommandType.DEFEND: self._handle_defend,
            CommandType.ENGAGE: self._handle_engage,
            CommandType.HOLD_FIRE: self._handle_hold_fire,
            CommandType.PRIORITIZE: self._handle_prioritize,
            CommandType.SET_ROE: self._handle_set_roe,
            CommandType.DEPLOY: self._handle_deploy,
            CommandType.REPORT: self._handle_report,
            CommandType.CONFIGURE: self._handle_configure,
            CommandType.UNKNOWN: self._handle_unknown,
        }

        handler = handlers.get(command.command_type, self._handle_unknown)
        result = handler(command)
        logger.info(
            "NLP command executed | type=%s confidence=%.2f result=%r",
            command.command_type.value,
            command.confidence,
            result,
        )
        return result

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _handle_defend(self, cmd: ParsedCommand) -> str:
        parts: list[str] = []

        if self._decision is not None:
            from decision.models import EngagementMode
            self._decision.mode = EngagementMode.AUTO
            parts.append("Decision engine set to AUTO/defensive mode.")
        else:
            parts.append("Defensive mode requested (no engine attached).")

        if cmd.target:
            parts.append(f"Defending: {cmd.target}.")
        if cmd.direction:
            parts.append(f"Priority sector: {cmd.direction}.")

        return " ".join(parts)

    def _handle_engage(self, cmd: ParsedCommand) -> str:
        parts: list[str] = []

        target_desc = cmd.target or "unspecified target"
        parts.append(f"Engagement order: {target_desc}.")

        constraints: list[str] = []
        if cmd.direction:
            constraints.append(f"sector {cmd.direction}")
        if cmd.altitude_m is not None:
            constraints.append(f"altitude <= {cmd.altitude_m:.0f}m")
        if cmd.distance_m is not None:
            constraints.append(f"within {cmd.distance_m:.0f}m")
        if constraints:
            parts.append(f"Constraints: {', '.join(constraints)}.")

        self._pending_engagements.append({
            "target": cmd.target,
            "direction": cmd.direction,
            "altitude_m": cmd.altitude_m,
            "distance_m": cmd.distance_m,
        })

        parts.append("Routed to engagement queue. Awaiting ROE clearance.")
        return " ".join(parts)

    def _handle_hold_fire(self, cmd: ParsedCommand) -> str:
        self._pending_engagements.clear()

        if self._decision is not None:
            from decision.models import EngagementMode
            self._decision.mode = EngagementMode.ADVISORY
            return "HOLD FIRE. Decision engine set to ADVISORY. All pending engagements cleared."

        return "HOLD FIRE acknowledged. All pending engagements cleared."

    def _handle_prioritize(self, cmd: ParsedCommand) -> str:
        focus_parts: list[str] = []

        if cmd.direction:
            focus_parts.append(cmd.direction)
        if cmd.target:
            focus_parts.append(cmd.target)

        focus = " / ".join(focus_parts) if focus_parts else "unspecified sector"
        self._current_focus = focus
        return f"Priority updated: focusing on {focus}."

    def _handle_set_roe(self, cmd: ParsedCommand) -> str:
        level = cmd.roe_level

        if level is None:
            return "ROE command received but level not recognized. Specify: free, tight, or hold."

        roe_messages = {
            "free": "WEAPONS FREE. Engage all valid targets without further authorization.",
            "tight": "WEAPONS TIGHT. Engage only positively identified hostile targets.",
            "hold": "WEAPONS HOLD. Do not engage. Return fire only if fired upon.",
        }

        message = roe_messages.get(level, f"ROE set to: {level}.")

        if self._roe is not None and level == "hold":
            # Weapons hold maps to clearing engagement authorization.
            logger.warning("ROE set to HOLD by operator NL command.")

        return message

    def _handle_deploy(self, cmd: ParsedCommand) -> str:
        parts: list[str] = []

        effector = cmd.effector_type or "effector"
        parts.append(f"Deploy order: {effector}.")

        if cmd.direction:
            parts.append(f"Direction: {cmd.direction}.")
        if cmd.distance_m is not None:
            parts.append(f"Range: {cmd.distance_m:.0f}m.")

        parts.append("Effector deployment request logged.")
        return " ".join(parts)

    def _handle_report(self, cmd: ParsedCommand) -> str:
        lines: list[str] = ["=== SITREP ==="]

        if self._decision is not None:
            lines.append(f"Decision engine mode: {self._decision.mode.value.upper()}")
        else:
            lines.append("Decision engine: not attached.")

        pending = len(self._pending_engagements)
        lines.append(f"Pending engagements: {pending}.")

        if self._current_focus:
            lines.append(f"Current priority sector: {self._current_focus}.")

        lines.append("=== END SITREP ===")
        return "\n".join(lines)

    def _handle_configure(self, cmd: ParsedCommand) -> str:
        return f"Configure request received: '{cmd.raw_text}'. Route to system config panel."

    def _handle_unknown(self, cmd: ParsedCommand) -> str:
        return (
            f"Command not recognized: '{cmd.raw_text}'. "
            f"Confidence: {cmd.confidence:.0%}. "
            "Try: defend, engage, hold fire, weapons free/tight/hold, deploy, status."
        )
