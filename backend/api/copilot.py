"""
LLM-free mission planning copilot for OVERWATCH.

Answers tactical questions about wargame scenarios, defender allocations, and
engagement outcomes using template-based responses and the existing IntentParser
for command execution fallback. No external LLM API required.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger("overwatch.copilot")

router = APIRouter(tags=["copilot"])

# Maps a leading keyword in the query to an intent category.
QUERY_PATTERNS: Dict[str, str] = {
    "what if": "scenario_analysis",
    "explain": "explain_engagement",
    "compare": "compare_scenarios",
    "status": "current_status",
    "recommend": "recommendation",
    "run": "run_scenario",
}

# Human-readable names for defender kinds.
_DEFENDER_LABELS: Dict[str, str] = {
    "jammer": "JAMMER",
    "jammers": "JAMMER",
    "interceptor": "INTERCEPTOR",
    "interceptors": "INTERCEPTOR",
    "hpm": "HPM",
    "ew": "EW",
    "laser": "LASER",
    "net": "NET",
}


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class CopilotRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000)
    context: Optional[Dict[str, Any]] = None


class CopilotResponse(BaseModel):
    query: str
    intent: str
    response: Dict[str, Any]


# ---------------------------------------------------------------------------
# Baseline loader
# ---------------------------------------------------------------------------

_BASELINE_PATH = Path(__file__).parent.parent / "models" / "benchmark_baseline.json"


def _load_baseline() -> Dict[str, Dict[str, Any]]:
    """Load benchmark baseline keyed by scenario name."""
    if not _BASELINE_PATH.exists():
        return {}
    raw = json.loads(_BASELINE_PATH.read_text())
    result: Dict[str, Dict[str, Any]] = {}
    for entry in raw.get("scenarios", []):
        name = entry.get("scenario_name", "")
        result[name] = entry.get("stats", {})
    return result


# ---------------------------------------------------------------------------
# Query classifier
# ---------------------------------------------------------------------------

def classify_query(query: str) -> str:
    """Return the intent category for a natural language query."""
    lower = query.lower().strip()
    for keyword, intent in QUERY_PATTERNS.items():
        if lower.startswith(keyword):
            return intent
    # Fallback heuristics for queries that do not start with a keyword.
    if "vs" in lower or "versus" in lower:
        return "compare_scenarios"
    if any(w in lower for w in ("threat", "leaker", "active")):
        return "current_status"
    if "?" in query and any(w in lower for w in ("why", "how")):
        return "explain_engagement"
    return "unknown"


# ---------------------------------------------------------------------------
# Scenario name extractor
# ---------------------------------------------------------------------------

_KNOWN_SCENARIOS = [
    "saturation_1000",
    "probe_120",
    "decoy_300",
    "contested_500",
    "skirmish_80",
    "combined_saturation_strike",
]


def _find_scenario_names(text: str) -> List[str]:
    """Extract known scenario names from free text, preserving query order."""
    lower = text.lower().replace("-", "_").replace(" ", "_")
    hits: List[Tuple[int, str]] = []
    for name in _KNOWN_SCENARIOS:
        idx = lower.find(name)
        if idx >= 0:
            hits.append((idx, name))
    hits.sort(key=lambda pair: pair[0])
    return [name for _, name in hits]


# ---------------------------------------------------------------------------
# Modification parser for what-if queries
# ---------------------------------------------------------------------------

_MOD_PATTERN = re.compile(
    r"(?:add|remove|increase|decrease)\s+(\d+)\s+(?:more\s+)?(\w+)",
    re.IGNORECASE,
)


@dataclass
class Modification:
    action: str  # add, remove, increase, decrease
    count: int
    asset_type: str  # normalized defender kind


def _parse_modification(query: str) -> Optional[Modification]:
    """Extract a single defender modification from a what-if query."""
    match = _MOD_PATTERN.search(query)
    if not match:
        return None
    action_word = query.lower().split()[0]
    if action_word not in ("add", "remove", "increase", "decrease"):
        # Find the actual verb in the match context.
        for verb in ("add", "remove", "increase", "decrease"):
            if verb in query.lower():
                action_word = verb
                break
    count = int(match.group(1))
    raw_type = match.group(2).lower()
    kind = _DEFENDER_LABELS.get(raw_type, raw_type.upper())
    return Modification(action=action_word, count=count, asset_type=kind)


# ---------------------------------------------------------------------------
# CopilotEngine
# ---------------------------------------------------------------------------

class CopilotEngine:
    """Template-based tactical question answering engine for OVERWATCH."""

    def __init__(self) -> None:
        self._baseline = _load_baseline()
        self._intent_parser: Any = None
        self._current_state: Dict[str, Any] = {}

    def set_state(self, state: Dict[str, Any]) -> None:
        """Update the live wargame state snapshot used by status queries."""
        self._current_state = state

    def process(
        self,
        query: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> CopilotResponse:
        """Route a natural language query to the appropriate handler."""
        intent = classify_query(query)
        ctx = context or {}
        merged = {**self._current_state, **ctx}

        handler = self._get_handler(intent)
        result = handler(query, merged)
        return CopilotResponse(query=query, intent=intent, response=result)

    # ------------------------------------------------------------------
    # Handler dispatch
    # ------------------------------------------------------------------

    def _get_handler(self, intent: str) -> Any:
        handlers = {
            "scenario_analysis": self.handle_what_if,
            "explain_engagement": self.handle_explain,
            "compare_scenarios": self.handle_compare,
            "current_status": self.handle_status,
            "recommendation": self.handle_recommend,
            "run_scenario": self.handle_run,
            "unknown": self._handle_unknown,
        }
        return handlers.get(intent, self._handle_unknown)

    # ------------------------------------------------------------------
    # What-if analysis
    # ------------------------------------------------------------------

    def handle_what_if(
        self,
        query: str,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Estimate the impact of adding or removing defenders."""
        mod = _parse_modification(query)
        scenarios = _find_scenario_names(query)
        scenario_name = scenarios[0] if scenarios else context.get("scenario", "")
        baseline = self._baseline.get(str(scenario_name), {})

        if mod is None:
            return {
                "analysis": "Could not parse a modification from the query.",
                "hint": "Try: 'What if we add 2 more jammers?'",
            }

        estimate = self._estimate_impact(mod, baseline)
        return {
            "modification": f"{mod.action} {mod.count} {mod.asset_type}",
            "scenario": scenario_name or "current",
            "baseline": _format_stats(baseline),
            "estimated": estimate,
            "analysis": self._what_if_summary(mod, baseline, estimate),
            "recommendation": self._what_if_verdict(baseline, estimate),
        }

    def _estimate_impact(
        self,
        mod: Modification,
        baseline: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Rough heuristic estimate. Not a full simulation."""
        bl_leakers = baseline.get("leakers", {}).get("mean", 0)
        bl_cost = baseline.get("cost_exchange_ratio", {}).get("mean", 0)
        is_area = mod.asset_type in ("HPM", "EW", "JAMMER")

        if mod.action in ("add", "increase"):
            factor = 0.15 if is_area else 0.08
            reduction = mod.count * factor
            est_leakers = max(0, bl_leakers * (1.0 - reduction))
            est_cost = bl_cost * (1.0 - reduction * 0.6)
        else:
            factor = 0.20 if is_area else 0.10
            increase = mod.count * factor
            est_leakers = bl_leakers * (1.0 + increase)
            est_cost = bl_cost * (1.0 + increase * 0.5)

        return {
            "leakers": round(est_leakers, 1),
            "cost_ratio": round(est_cost, 4),
        }

    @staticmethod
    def _what_if_summary(
        mod: Modification,
        baseline: Dict[str, Any],
        estimate: Dict[str, Any],
    ) -> str:
        bl_l = baseline.get("leakers", {}).get("mean", 0)
        bl_c = baseline.get("cost_exchange_ratio", {}).get("mean", 0)
        el = estimate["leakers"]
        ec = estimate["cost_ratio"]
        verb = "Adding" if mod.action in ("add", "increase") else "Removing"
        return (
            f"{verb} {mod.count} {mod.asset_type} changes leakers "
            f"from {bl_l} to {el} and cost ratio from {bl_c} to {ec}"
        )

    @staticmethod
    def _what_if_verdict(
        baseline: Dict[str, Any],
        estimate: Dict[str, Any],
    ) -> str:
        bl_l = baseline.get("leakers", {}).get("mean", 0)
        el = estimate["leakers"]
        if el < bl_l:
            return "RECOMMENDED: Modification reduces leaker count"
        if el > bl_l:
            return "NOT RECOMMENDED: Modification increases leaker count"
        return "NEUTRAL: No significant change in leaker count"

    # ------------------------------------------------------------------
    # Explain engagement
    # ------------------------------------------------------------------

    def handle_explain(
        self,
        query: str,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Explain why a track leaked or an engagement occurred."""
        track_id = self._extract_track_id(query)
        leakers = context.get("leakers", [])
        engagements = context.get("engagements", [])

        if track_id and track_id in [str(l) for l in leakers]:
            return self._explain_leaker(track_id, context)
        if track_id:
            return self._explain_track(track_id, engagements)
        return self._explain_general(context)

    @staticmethod
    def _extract_track_id(query: str) -> Optional[str]:
        match = re.search(r"track\s*[-#]?\s*(\w+)", query, re.IGNORECASE)
        return match.group(1) if match else None

    @staticmethod
    def _explain_leaker(
        track_id: str,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        defenders = context.get("defenders_available", 0)
        return {
            "track_id": track_id,
            "status": "LEAKED",
            "reasons": [
                "Track reached the defended site before engagement could complete",
                f"Defenders available at time of breach: {defenders}",
                "Possible causes: capacity exhaustion, range gap, or reload window",
            ],
        }

    @staticmethod
    def _explain_track(
        track_id: str,
        engagements: List[Any],
    ) -> Dict[str, Any]:
        return {
            "track_id": track_id,
            "status": "TRACKED",
            "engagements_against": len(engagements),
            "detail": f"Track {track_id} is under observation or engagement",
        }

    @staticmethod
    def _explain_general(context: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "summary": "No specific track identified in query",
            "hint": "Try: 'Explain why track 45 leaked'",
            "active_threats": context.get("active_hostiles", "unknown"),
        }

    # ------------------------------------------------------------------
    # Compare scenarios
    # ------------------------------------------------------------------

    def handle_compare(
        self,
        query: str,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Compare two scenario baselines side by side."""
        names = _find_scenario_names(query)
        if len(names) < 2:
            return {
                "error": "Need two scenario names to compare",
                "available": _KNOWN_SCENARIOS,
                "hint": "Try: 'Compare probe_120 vs saturation_1000'",
            }

        a_name, b_name = names[0], names[1]
        a_stats = self._baseline.get(a_name, {})
        b_stats = self._baseline.get(b_name, {})

        return {
            "scenarios": [a_name, b_name],
            a_name: _format_stats(a_stats),
            b_name: _format_stats(b_stats),
            "verdict": self._compare_verdict(a_name, a_stats, b_name, b_stats),
        }

    @staticmethod
    def _compare_verdict(
        a_name: str,
        a_stats: Dict[str, Any],
        b_name: str,
        b_stats: Dict[str, Any],
    ) -> str:
        a_l = a_stats.get("leakers", {}).get("mean", 0)
        b_l = b_stats.get("leakers", {}).get("mean", 0)
        a_c = a_stats.get("cost_exchange_ratio", {}).get("mean", 0)
        b_c = b_stats.get("cost_exchange_ratio", {}).get("mean", 0)
        parts: List[str] = []
        parts.append(f"{a_name} has {a_l} mean leakers vs {b_name} with {b_l}")
        parts.append(f"Cost ratio: {a_name}={a_c}, {b_name}={b_c}")
        harder = a_name if a_l > b_l else b_name
        parts.append(f"{harder} is the harder scenario")
        return ". ".join(parts)

    # ------------------------------------------------------------------
    # Current status
    # ------------------------------------------------------------------

    def handle_status(
        self,
        query: str,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Return the current operational status from live state."""
        active = context.get("active_hostiles", 0)
        leakers = context.get("leakers", 0)
        cost_ratio = context.get("cost_exchange_ratio")
        intercept_rate = context.get("intercept_rate")
        tick = context.get("tick", 0)

        if active == 0 and tick == 0:
            threat_level = "CLEAR"
        elif active <= 20:
            threat_level = "LOW"
        elif active <= 100:
            threat_level = "MODERATE"
        elif active <= 300:
            threat_level = "HIGH"
        else:
            threat_level = "CRITICAL"

        return {
            "threat_level": threat_level,
            "active_hostiles": active,
            "leakers": leakers,
            "cost_exchange_ratio": cost_ratio,
            "intercept_rate": intercept_rate,
            "tick": tick,
            "summary": (
                f"Threat level {threat_level}. "
                f"{active} active hostiles, {leakers} leakers."
            ),
        }

    # ------------------------------------------------------------------
    # Recommendation
    # ------------------------------------------------------------------

    def handle_recommend(
        self,
        query: str,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Provide tactical recommendations based on current state."""
        active = context.get("active_hostiles", 0)
        leakers = context.get("leakers", 0)
        cost_ratio = context.get("cost_exchange_ratio")
        intent = context.get("swarm_intent", "UNKNOWN")

        recommendations: List[str] = []
        priority = "LOW"

        if leakers > 0:
            recommendations.append(
                f"{leakers} leakers detected. Consider adding area effectors (HPM/EW)."
            )
            priority = "HIGH"

        if cost_ratio is not None and cost_ratio > 1.0:
            recommendations.append(
                f"Cost ratio {cost_ratio:.2f} exceeds 1.0. "
                "Shift to non-kinetic effectors to reduce engagement cost."
            )
            priority = "HIGH"

        if intent == "DECOY":
            recommendations.append(
                "Decoy swarm detected. Avoid kinetic interceptors on low-value targets."
            )

        if active > 200:
            recommendations.append(
                "High threat density. Maximize HPM coverage for area effect kills."
            )

        if not recommendations:
            recommendations.append("Defenses are performing within acceptable parameters.")

        return {
            "priority": priority,
            "recommendations": recommendations,
            "context": {
                "active_hostiles": active,
                "leakers": leakers,
                "cost_ratio": cost_ratio,
                "swarm_intent": intent,
            },
        }

    # ------------------------------------------------------------------
    # Run scenario
    # ------------------------------------------------------------------

    def handle_run(
        self,
        query: str,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Return instructions to start a scenario (actual run is async)."""
        names = _find_scenario_names(query)
        speed_match = re.search(r"(\d+(?:\.\d+)?)x\s*speed", query, re.IGNORECASE)
        speed = float(speed_match.group(1)) if speed_match else 1.0

        if not names:
            return {
                "error": "No recognized scenario name in query",
                "available": _KNOWN_SCENARIOS,
                "hint": "Try: 'Run probe_120 at 2x speed'",
            }

        return {
            "action": "start_scenario",
            "scenario": names[0],
            "speed_multiplier": speed,
            "baseline": _format_stats(self._baseline.get(names[0], {})),
            "message": f"Starting {names[0]} at {speed}x speed",
        }

    # ------------------------------------------------------------------
    # Unknown / fallback
    # ------------------------------------------------------------------

    def _handle_unknown(
        self,
        query: str,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Fall back to the IntentParser for direct commands."""
        parsed = self._get_intent_parser().parse(query)
        if parsed.command_type.value != "unknown":
            return {
                "fallback": "command",
                "parsed_command": parsed.command_type.value,
                "target": parsed.target,
                "confidence": parsed.confidence,
                "message": f"Interpreted as {parsed.command_type.value} command",
            }
        return {
            "fallback": "none",
            "message": "Could not understand the query. Try one of these forms:",
            "examples": [
                "What if we add 2 more jammers?",
                "Explain why track 45 leaked",
                "Compare probe_120 vs saturation_1000",
                "Status",
                "Recommend a response to the decoy swarm",
                "Run probe_120 at 2x speed",
            ],
        }

    def _get_intent_parser(self) -> Any:
        """Lazy-load the IntentParser to avoid circular imports."""
        if self._intent_parser is None:
            from nlp.intent_parser import IntentParser
            self._intent_parser = IntentParser()
        return self._intent_parser


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _format_stats(stats: Dict[str, Any]) -> Dict[str, Any]:
    """Extract mean values from benchmark stats into a flat dict."""
    if not stats:
        return {}
    return {
        "leakers": stats.get("leakers", {}).get("mean"),
        "cost_ratio": stats.get("cost_exchange_ratio", {}).get("mean"),
        "intercept_rate": stats.get("intercept_rate", {}).get("mean"),
        "engagements": stats.get("engagements_made", {}).get("mean"),
        "sim_time_s": stats.get("sim_time_s", {}).get("mean"),
    }


# ---------------------------------------------------------------------------
# Singleton engine + FastAPI route
# ---------------------------------------------------------------------------

engine = CopilotEngine()


@router.post("/copilot", response_model=CopilotResponse)
async def copilot_query(request: CopilotRequest) -> CopilotResponse:
    """Process a natural language tactical query."""
    try:
        return engine.process(request.query, request.context)
    except Exception as exc:
        logger.exception("Copilot query failed: %s", request.query)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
