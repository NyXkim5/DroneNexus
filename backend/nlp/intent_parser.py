"""
Rule-based natural language command parser for tactical operations.

No ML. No model calls. Pure regex + keyword extraction.
Designed for low-latency use in denied/degraded environments.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple


class CommandType(Enum):
    DEFEND = "defend"
    ENGAGE = "engage"
    HOLD_FIRE = "hold_fire"
    PRIORITIZE = "prioritize"
    SET_ROE = "set_roe"
    DEPLOY = "deploy"
    REPORT = "report"
    CONFIGURE = "configure"
    UNKNOWN = "unknown"


@dataclass
class ParsedCommand:
    command_type: CommandType
    target: Optional[str] = None
    direction: Optional[str] = None
    distance_m: Optional[float] = None
    altitude_m: Optional[float] = None
    effector_type: Optional[str] = None
    priority: Optional[str] = None
    roe_level: Optional[str] = None
    confidence: float = 0.0
    raw_text: str = ""


# Cardinal and intercardinal directions with their canonical forms.
_DIRECTION_ALIASES: Dict[str, str] = {
    "north": "north",
    "northern": "north",
    "northward": "north",
    "south": "south",
    "southern": "south",
    "southward": "south",
    "east": "east",
    "eastern": "east",
    "eastward": "east",
    "west": "west",
    "western": "west",
    "westward": "west",
    "northeast": "northeast",
    "northeastern": "northeast",
    "northwest": "northwest",
    "northwestern": "northwest",
    "southeast": "southeast",
    "southeastern": "southeast",
    "southwest": "southwest",
    "southwestern": "southwest",
    "ne": "northeast",
    "nw": "northwest",
    "se": "southeast",
    "sw": "southwest",
}

# Known effector types.
_EFFECTOR_TYPES: List[str] = [
    "jammer",
    "interceptor",
    "net",
    "laser",
    "kinetic",
    "ew",
    "hpm",
]

# Target type keywords that signal a target description rather than an ID.
_TARGET_TYPE_KEYWORDS: List[str] = [
    "drone",
    "uav",
    "uas",
    "vehicle",
    "truck",
    "aircraft",
    "helicopter",
    "quadcopter",
    "fixed-wing",
    "swarm",
    "group",
    "fob",
    "position",
    "site",
    "installation",
    "approach",
    "sector",
    "zone",
]


class IntentParser:
    """Rule-based NL command parser for tactical operations."""

    def __init__(self) -> None:
        self._patterns = self._build_patterns()

    def parse(self, text: str) -> ParsedCommand:
        """
        Parse natural language text into a structured command.

        Steps:
        1. Normalize (lowercase, strip punctuation noise)
        2. Match against command patterns in priority order
        3. Extract entities (targets, directions, distances, altitudes)
        4. Return ParsedCommand with confidence score
        """
        normalized = self._normalize(text)

        for cmd_type, patterns in self._patterns:
            for pattern, base_confidence in patterns:
                match = pattern.search(normalized)
                if match:
                    cmd = self._build_command(
                        cmd_type=cmd_type,
                        match=match,
                        normalized=normalized,
                        base_confidence=base_confidence,
                        raw_text=text,
                    )
                    return cmd

        return ParsedCommand(
            command_type=CommandType.UNKNOWN,
            confidence=0.1,
            raw_text=text,
        )

    # ------------------------------------------------------------------
    # Pattern construction
    # ------------------------------------------------------------------

    def _build_patterns(self) -> List[Tuple[CommandType, List[Tuple[re.Pattern, float]]]]:
        """
        Build ordered list of (CommandType, [(pattern, base_confidence)]).

        Patterns are matched in order. HOLD_FIRE and SET_ROE come before
        ENGAGE to avoid "weapons free" matching an ENGAGE pattern.
        """
        return [
            (
                CommandType.SET_ROE,
                [
                    (re.compile(r"\bweapons?\s+(free|tight|hold)\b"), 0.95),
                    (re.compile(r"\broe\s+(free|tight|hold|weapons\s+free|weapons\s+tight|weapons\s+hold)\b"), 0.90),
                ],
            ),
            (
                CommandType.HOLD_FIRE,
                [
                    (re.compile(r"\b(hold\s+fire|cease\s+fire|stop\s+firing|do\s+not\s+engage|cease\s+engagement|stop\s+engagement)\b"), 0.95),
                ],
            ),
            (
                CommandType.DEFEND,
                [
                    (re.compile(r"\b(defend|protect|secure|hold)\s+(the\s+|this\s+)?(.*?)(\s+(from|against)\s+.*)?$"), 0.90),
                ],
            ),
            (
                CommandType.ENGAGE,
                [
                    (re.compile(r"\b(engage|fire\s+on|fire\s+at|shoot|attack|destroy|hit|neutralize|strike)\b(.*)"), 0.90),
                ],
            ),
            (
                CommandType.PRIORITIZE,
                [
                    (re.compile(r"\b(prioritize|focus\s+on|concentrate\s+on|focus\s+fire|concentrate\s+fire)\s+(on\s+)?(.*?)$"), 0.88),
                    (re.compile(r"\b(prioritize|focus|concentrate)\s+(.*?)$"), 0.82),
                ],
            ),
            (
                CommandType.DEPLOY,
                [
                    (re.compile(r"\b(deploy|launch|activate|use|send)\s+(.*?)$"), 0.88),
                ],
            ),
            (
                CommandType.REPORT,
                [
                    (re.compile(r"\b(status\s+report|sitrep|give\s+me\s+a\s+report|report\s+status)\b"), 0.95),
                    (re.compile(r"\b(status|report|how\s+many|what\s+is|what\'s|threat\s+count|count\s+threats?)\b"), 0.80),
                ],
            ),
            (
                CommandType.CONFIGURE,
                [
                    (re.compile(r"\b(set|change|adjust|configure|update)\s+(.*?)$"), 0.85),
                ],
            ),
        ]

    # ------------------------------------------------------------------
    # Command assembly
    # ------------------------------------------------------------------

    def _build_command(
        self,
        cmd_type: CommandType,
        match: re.Match,
        normalized: str,
        base_confidence: float,
        raw_text: str,
    ) -> ParsedCommand:
        direction = self._extract_direction(normalized)
        distance_m = self._extract_distance(normalized)
        altitude_m = self._extract_altitude(normalized)
        target = self._extract_target(normalized, cmd_type)
        effector_type = self._extract_effector(normalized)
        roe_level = self._extract_roe_level(normalized, match, cmd_type)

        # Confidence boosted for each additional entity extracted.
        entity_bonus = sum([
            0.02 if direction else 0,
            0.02 if distance_m is not None else 0,
            0.02 if altitude_m is not None else 0,
            0.02 if target else 0,
            0.02 if effector_type else 0,
        ])
        confidence = min(1.0, base_confidence + entity_bonus)

        return ParsedCommand(
            command_type=cmd_type,
            target=target,
            direction=direction,
            distance_m=distance_m,
            altitude_m=altitude_m,
            effector_type=effector_type,
            roe_level=roe_level,
            confidence=confidence,
            raw_text=raw_text,
        )

    # ------------------------------------------------------------------
    # Entity extractors
    # ------------------------------------------------------------------

    def _extract_direction(self, text: str) -> Optional[str]:
        """Extract canonical cardinal/intercardinal direction from text."""
        # Check compound directions first (northeast before north/east).
        for alias in sorted(_DIRECTION_ALIASES.keys(), key=len, reverse=True):
            pattern = re.compile(rf"\b{re.escape(alias)}\b")
            if pattern.search(text):
                return _DIRECTION_ALIASES[alias]
        return None

    def _extract_distance(self, text: str) -> Optional[float]:
        """
        Extract distance in meters.

        Handles: meters, m, km, kilometres, kilometers, clicks (1 click = 1000m).
        """
        km_pattern = re.compile(
            r"(\d+(?:\.\d+)?)\s*(km|kilomet(?:re|er)s?|clicks?)"
        )
        m_pattern = re.compile(
            r"(\d+(?:\.\d+)?)\s*(meters?|metres?|m)\b"
        )

        match = km_pattern.search(text)
        if match:
            return float(match.group(1)) * 1000.0

        match = m_pattern.search(text)
        if match:
            return float(match.group(1))

        return None

    def _extract_altitude(self, text: str) -> Optional[float]:
        """
        Extract altitude constraint in meters.

        Handles 'below/above/under/over NNN m/meters/feet/ft'.
        Feet are converted to meters (1 ft = 0.3048 m).
        """
        pattern = re.compile(
            r"\b(?:below|above|under|over|at)\s+(\d+(?:\.\d+)?)\s*(m|meters?|metres?|feet|foot|ft)\b"
        )
        match = pattern.search(text)
        if not match:
            return None

        value = float(match.group(1))
        unit = match.group(2).lower()

        if unit in ("feet", "foot", "ft"):
            return round(value * 0.3048, 2)
        return value

    def _extract_target(self, text: str, cmd_type: CommandType) -> Optional[str]:
        """
        Extract target reference.

        Priority:
        1. Explicit ID: 'target 3', 'track alpha', 'track-7'
        2. Known type keywords: 'fuel truck', 'drone'
        3. None
        """
        id_pattern = re.compile(
            r"\b(?:target|track|contact|tgt)\s*[-#]?\s*([a-z0-9]+)\b"
        )
        match = id_pattern.search(text)
        if match:
            return match.group(1)

        for keyword in _TARGET_TYPE_KEYWORDS:
            if re.search(rf"\b{re.escape(keyword)}\b", text):
                return keyword

        return None

    def _extract_effector(self, text: str) -> Optional[str]:
        """Extract effector type from text."""
        for effector in _EFFECTOR_TYPES:
            if re.search(rf"\b{re.escape(effector)}\b", text):
                return effector
        return None

    def _extract_roe_level(
        self,
        text: str,
        match: re.Match,
        cmd_type: CommandType,
    ) -> Optional[str]:
        """Extract ROE level for SET_ROE commands."""
        if cmd_type != CommandType.SET_ROE:
            return None

        roe_pattern = re.compile(r"\b(free|tight|hold)\b")
        m = roe_pattern.search(text)
        if m:
            return m.group(1)
        return None

    # ------------------------------------------------------------------
    # Text normalization
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize(text: str) -> str:
        """Lowercase, collapse whitespace, strip leading/trailing noise."""
        text = text.lower().strip()
        text = re.sub(r"[!?.]+$", "", text)
        text = re.sub(r"\s+", " ", text)
        return text
