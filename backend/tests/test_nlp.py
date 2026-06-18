"""
Tests for the NLP mission command parser and executor.

All tests are rule-based — no ML, no external calls.
Run: cd /Users/jay/DroneNexus/backend && python3 -m pytest tests/test_nlp.py -v
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from nlp.intent_parser import CommandType, IntentParser, ParsedCommand
from nlp.executor import CommandExecutor


@pytest.fixture
def parser() -> IntentParser:
    return IntentParser()


@pytest.fixture
def executor() -> CommandExecutor:
    return CommandExecutor()


# ------------------------------------------------------------------
# CommandType classification
# ------------------------------------------------------------------

def test_parse_defend(parser: IntentParser) -> None:
    cmd = parser.parse("defend the FOB")
    assert cmd.command_type == CommandType.DEFEND
    assert cmd.confidence >= 0.8


def test_parse_engage_target(parser: IntentParser) -> None:
    cmd = parser.parse("engage target 3")
    assert cmd.command_type == CommandType.ENGAGE
    assert cmd.target == "3"


def test_parse_hold_fire(parser: IntentParser) -> None:
    cmd = parser.parse("hold fire")
    assert cmd.command_type == CommandType.HOLD_FIRE
    assert cmd.confidence >= 0.9


def test_parse_weapons_free(parser: IntentParser) -> None:
    cmd = parser.parse("weapons free")
    assert cmd.command_type == CommandType.SET_ROE
    assert cmd.roe_level == "free"


def test_parse_weapons_tight(parser: IntentParser) -> None:
    cmd = parser.parse("weapons tight")
    assert cmd.command_type == CommandType.SET_ROE
    assert cmd.roe_level == "tight"


def test_parse_weapons_hold(parser: IntentParser) -> None:
    cmd = parser.parse("weapons hold")
    assert cmd.command_type == CommandType.SET_ROE
    assert cmd.roe_level == "hold"


def test_parse_prioritize_direction(parser: IntentParser) -> None:
    cmd = parser.parse("prioritize northern approach")
    assert cmd.command_type == CommandType.PRIORITIZE
    assert cmd.direction == "north"


def test_parse_deploy_effector(parser: IntentParser) -> None:
    cmd = parser.parse("deploy jammer to the north")
    assert cmd.command_type == CommandType.DEPLOY
    assert cmd.effector_type == "jammer"
    assert cmd.direction == "north"


def test_parse_status_report(parser: IntentParser) -> None:
    cmd = parser.parse("status report")
    assert cmd.command_type == CommandType.REPORT


def test_parse_sitrep(parser: IntentParser) -> None:
    cmd = parser.parse("sitrep")
    assert cmd.command_type == CommandType.REPORT


# ------------------------------------------------------------------
# Entity extraction
# ------------------------------------------------------------------

def test_extract_distance_meters(parser: IntentParser) -> None:
    cmd = parser.parse("engage all targets within 500 meters")
    assert cmd.distance_m == 500.0


def test_extract_distance_km(parser: IntentParser) -> None:
    cmd = parser.parse("engage targets within 2 km")
    assert cmd.distance_m == 2000.0


def test_extract_distance_clicks(parser: IntentParser) -> None:
    cmd = parser.parse("hold position 3 clicks out")
    assert cmd.distance_m == 3000.0


def test_extract_altitude_below(parser: IntentParser) -> None:
    cmd = parser.parse("engage all drones below 200m")
    assert cmd.altitude_m == 200.0


def test_extract_altitude_feet(parser: IntentParser) -> None:
    cmd = parser.parse("engage targets above 500 feet")
    # 500 ft * 0.3048 = 152.4 m
    assert cmd.altitude_m == pytest.approx(152.4, abs=0.1)


def test_extract_direction_cardinal(parser: IntentParser) -> None:
    # "south" extracted from a parseable REPORT command.
    assert parser.parse("report threats from the south").direction == "south"
    # "east" extracted from a parseable DEPLOY command.
    assert parser.parse("deploy east").direction == "east"


def test_extract_direction_intercardinal(parser: IntentParser) -> None:
    cmd = parser.parse("deploy interceptor to the northeast")
    assert cmd.direction == "northeast"


def test_extract_direction_adjective_form(parser: IntentParser) -> None:
    # "southern" -> "south"
    cmd = parser.parse("engage targets on the southern perimeter")
    assert cmd.direction == "south"


def test_extract_target_id_numeric(parser: IntentParser) -> None:
    cmd = parser.parse("engage target 7")
    assert cmd.target == "7"


def test_extract_target_id_alpha(parser: IntentParser) -> None:
    cmd = parser.parse("engage track alpha")
    assert cmd.target == "alpha"


def test_extract_effector_interceptor(parser: IntentParser) -> None:
    cmd = parser.parse("deploy interceptor north")
    assert cmd.effector_type == "interceptor"


# ------------------------------------------------------------------
# Complex / multi-entity commands
# ------------------------------------------------------------------

def test_parse_complex_engage_with_altitude_and_direction(parser: IntentParser) -> None:
    cmd = parser.parse("engage all targets below 200m on northern approach")
    assert cmd.command_type == CommandType.ENGAGE
    assert cmd.altitude_m == 200.0
    assert cmd.direction == "north"


def test_parse_complex_defend_with_direction(parser: IntentParser) -> None:
    cmd = parser.parse("defend the FOB, prioritize northern approach, engage anything under 200m")
    # First verb wins: defend
    assert cmd.command_type == CommandType.DEFEND
    assert cmd.direction == "north"


def test_parse_complex_deploy_with_distance(parser: IntentParser) -> None:
    cmd = parser.parse("deploy jammer 500 meters to the northeast")
    assert cmd.command_type == CommandType.DEPLOY
    assert cmd.effector_type == "jammer"
    assert cmd.distance_m == 500.0
    assert cmd.direction == "northeast"


# ------------------------------------------------------------------
# Edge cases
# ------------------------------------------------------------------

def test_unknown_command_low_confidence(parser: IntentParser) -> None:
    cmd = parser.parse("xyzzy florp banana seven")
    assert cmd.confidence < 0.3


def test_case_insensitive(parser: IntentParser) -> None:
    upper = parser.parse("ENGAGE TARGET 5")
    lower = parser.parse("engage target 5")
    assert upper.command_type == lower.command_type
    assert upper.target == lower.target


def test_cease_fire_variant(parser: IntentParser) -> None:
    cmd = parser.parse("cease fire")
    assert cmd.command_type == CommandType.HOLD_FIRE


def test_stop_firing_variant(parser: IntentParser) -> None:
    cmd = parser.parse("stop firing")
    assert cmd.command_type == CommandType.HOLD_FIRE


def test_protect_variant(parser: IntentParser) -> None:
    cmd = parser.parse("protect this position")
    assert cmd.command_type == CommandType.DEFEND


def test_neutralize_variant(parser: IntentParser) -> None:
    cmd = parser.parse("neutralize the fuel truck")
    assert cmd.command_type == CommandType.ENGAGE


def test_configure_command(parser: IntentParser) -> None:
    cmd = parser.parse("set scan rate to 10hz")
    assert cmd.command_type == CommandType.CONFIGURE


def test_raw_text_preserved(parser: IntentParser) -> None:
    original = "Engage Target 3"
    cmd = parser.parse(original)
    assert cmd.raw_text == original


# ------------------------------------------------------------------
# Executor
# ------------------------------------------------------------------

def test_executor_hold_fire_clears_queue(executor: CommandExecutor) -> None:
    # Add something to the queue first.
    engage_cmd = ParsedCommand(
        command_type=CommandType.ENGAGE,
        target="3",
        confidence=0.92,
        raw_text="engage target 3",
    )
    executor.execute(engage_cmd)
    assert len(executor._pending_engagements) == 1

    hold_cmd = ParsedCommand(
        command_type=CommandType.HOLD_FIRE,
        confidence=0.95,
        raw_text="hold fire",
    )
    result = executor.execute(hold_cmd)
    assert "HOLD FIRE" in result
    assert len(executor._pending_engagements) == 0


def test_executor_low_confidence_rejected(executor: CommandExecutor) -> None:
    cmd = ParsedCommand(
        command_type=CommandType.ENGAGE,
        confidence=0.2,
        raw_text="xyzzy",
    )
    result = executor.execute(cmd)
    assert "not understood" in result.lower()


def test_executor_report_returns_sitrep(executor: CommandExecutor) -> None:
    cmd = ParsedCommand(
        command_type=CommandType.REPORT,
        confidence=0.95,
        raw_text="status report",
    )
    result = executor.execute(cmd)
    assert "SITREP" in result


def test_executor_roe_weapons_free(executor: CommandExecutor) -> None:
    cmd = ParsedCommand(
        command_type=CommandType.SET_ROE,
        roe_level="free",
        confidence=0.95,
        raw_text="weapons free",
    )
    result = executor.execute(cmd)
    assert "WEAPONS FREE" in result


def test_executor_deploy_with_direction(executor: CommandExecutor) -> None:
    cmd = ParsedCommand(
        command_type=CommandType.DEPLOY,
        effector_type="jammer",
        direction="north",
        confidence=0.92,
        raw_text="deploy jammer north",
    )
    result = executor.execute(cmd)
    assert "jammer" in result.lower()
    assert "north" in result.lower()


def test_executor_defend_sets_focus(executor: CommandExecutor) -> None:
    cmd = ParsedCommand(
        command_type=CommandType.DEFEND,
        target="fob",
        direction="north",
        confidence=0.92,
        raw_text="defend the FOB from the north",
    )
    result = executor.execute(cmd)
    assert "defensive" in result.lower() or "north" in result.lower()


def test_executor_prioritize_updates_focus(executor: CommandExecutor) -> None:
    cmd = ParsedCommand(
        command_type=CommandType.PRIORITIZE,
        direction="northeast",
        confidence=0.90,
        raw_text="prioritize northeastern sector",
    )
    result = executor.execute(cmd)
    assert executor._current_focus is not None
    assert "northeast" in executor._current_focus


def test_executor_unknown_command(executor: CommandExecutor) -> None:
    cmd = ParsedCommand(
        command_type=CommandType.UNKNOWN,
        confidence=0.1,
        raw_text="xyzzy florp",
    )
    result = executor.execute(cmd)
    # Low confidence -> "not understood" path
    assert "not understood" in result.lower() or "not recognized" in result.lower()
