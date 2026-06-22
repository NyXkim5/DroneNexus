"""
Tests for the OVERWATCH copilot engine and API endpoint.

Covers query classification, each handler, modification parsing, and
the FastAPI endpoint via TestClient.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.copilot import (
    CopilotEngine,
    CopilotRequest,
    CopilotResponse,
    Modification,
    _find_scenario_names,
    _format_stats,
    _parse_modification,
    classify_query,
    engine,
    router,
)


# ---------------------------------------------------------------------------
# Query classification
# ---------------------------------------------------------------------------

class TestClassifyQuery:

    def test_what_if(self) -> None:
        assert classify_query("What if we add 3 HPMs?") == "scenario_analysis"

    def test_explain(self) -> None:
        assert classify_query("Explain why track 12 leaked") == "explain_engagement"

    def test_compare(self) -> None:
        assert classify_query("Compare probe_120 vs saturation_1000") == "compare_scenarios"

    def test_status(self) -> None:
        assert classify_query("Status report") == "current_status"

    def test_recommend(self) -> None:
        assert classify_query("Recommend a response") == "recommendation"

    def test_run(self) -> None:
        assert classify_query("Run probe_120 at 2x speed") == "run_scenario"

    def test_unknown_fallback_why(self) -> None:
        assert classify_query("Why did track 5 get through?") == "explain_engagement"

    def test_unknown_fallback_vs(self) -> None:
        assert classify_query("probe_120 vs decoy_300") == "compare_scenarios"

    def test_unknown_fallback_threat(self) -> None:
        assert classify_query("How many active threats?") == "current_status"

    def test_truly_unknown(self) -> None:
        assert classify_query("Hello world") == "unknown"


# ---------------------------------------------------------------------------
# Scenario name extraction
# ---------------------------------------------------------------------------

class TestFindScenarioNames:

    def test_single(self) -> None:
        assert _find_scenario_names("run probe_120") == ["probe_120"]

    def test_multiple(self) -> None:
        names = _find_scenario_names("compare probe_120 vs saturation_1000")
        assert "probe_120" in names
        assert "saturation_1000" in names

    def test_none(self) -> None:
        assert _find_scenario_names("hello world") == []

    def test_hyphen_format(self) -> None:
        assert _find_scenario_names("probe-120") == ["probe_120"]

    def test_space_format(self) -> None:
        assert _find_scenario_names("saturation 1000") == ["saturation_1000"]


# ---------------------------------------------------------------------------
# Modification parsing
# ---------------------------------------------------------------------------

class TestParseModification:

    def test_add_jammers(self) -> None:
        mod = _parse_modification("What if we add 2 more jammers?")
        assert mod is not None
        assert mod.count == 2
        assert mod.asset_type == "JAMMER"

    def test_add_hpm(self) -> None:
        mod = _parse_modification("add 3 HPM units")
        assert mod is not None
        assert mod.count == 3
        assert mod.asset_type == "HPM"

    def test_remove_interceptors(self) -> None:
        mod = _parse_modification("remove 1 interceptor from the loadout")
        assert mod is not None
        assert mod.action == "remove"
        assert mod.count == 1
        assert mod.asset_type == "INTERCEPTOR"

    def test_no_match(self) -> None:
        assert _parse_modification("What is the weather?") is None


# ---------------------------------------------------------------------------
# Format stats utility
# ---------------------------------------------------------------------------

class TestFormatStats:

    def test_formats_means(self) -> None:
        stats = {
            "leakers": {"mean": 5.0},
            "cost_exchange_ratio": {"mean": 0.4},
            "intercept_rate": {"mean": 0.9},
            "engagements_made": {"mean": 100},
            "sim_time_s": {"mean": 60.0},
        }
        result = _format_stats(stats)
        assert result["leakers"] == 5.0
        assert result["cost_ratio"] == 0.4

    def test_empty_stats(self) -> None:
        assert _format_stats({}) == {}


# ---------------------------------------------------------------------------
# CopilotEngine handlers
# ---------------------------------------------------------------------------

class TestCopilotEngine:

    def setup_method(self) -> None:
        self.engine = CopilotEngine()
        self.engine._baseline = {
            "probe_120": {
                "leakers": {"mean": 0.0, "std": 0.0},
                "cost_exchange_ratio": {"mean": 0.015, "std": 0.008},
                "intercept_rate": {"mean": 0.53, "std": 0.1},
                "engagements_made": {"mean": 68, "std": 17},
                "sim_time_s": {"mean": 80.0, "std": 0.0},
            },
            "saturation_1000": {
                "leakers": {"mean": 543.0, "std": 26.0},
                "cost_exchange_ratio": {"mean": 0.544, "std": 0.02},
                "intercept_rate": {"mean": 1.31, "std": 0.07},
                "engagements_made": {"mean": 350, "std": 0},
                "sim_time_s": {"mean": 90.3, "std": 0.2},
            },
        }

    # -- status --

    def test_status_clear(self) -> None:
        result = self.engine.handle_status("status", {})
        assert result["threat_level"] == "CLEAR"

    def test_status_low(self) -> None:
        result = self.engine.handle_status("status", {"active_hostiles": 10, "tick": 5})
        assert result["threat_level"] == "LOW"

    def test_status_moderate(self) -> None:
        result = self.engine.handle_status("status", {"active_hostiles": 50, "tick": 5})
        assert result["threat_level"] == "MODERATE"

    def test_status_high(self) -> None:
        result = self.engine.handle_status("status", {"active_hostiles": 200, "tick": 5})
        assert result["threat_level"] == "HIGH"

    def test_status_critical(self) -> None:
        result = self.engine.handle_status("status", {"active_hostiles": 500, "tick": 5})
        assert result["threat_level"] == "CRITICAL"

    def test_status_includes_summary(self) -> None:
        result = self.engine.handle_status(
            "status",
            {"active_hostiles": 50, "leakers": 3, "tick": 10},
        )
        assert "50 active hostiles" in result["summary"]
        assert "3 leakers" in result["summary"]

    # -- explain --

    def test_explain_leaker(self) -> None:
        ctx = {"leakers": [45], "defenders_available": 2}
        result = self.engine.handle_explain("Explain why track 45 leaked", ctx)
        assert result["track_id"] == "45"
        assert result["status"] == "LEAKED"

    def test_explain_tracked(self) -> None:
        result = self.engine.handle_explain("Explain track 12", {"leakers": []})
        assert result["track_id"] == "12"
        assert result["status"] == "TRACKED"

    def test_explain_general(self) -> None:
        result = self.engine.handle_explain("Explain the situation", {})
        assert "hint" in result

    # -- compare --

    def test_compare_two(self) -> None:
        result = self.engine.handle_compare(
            "Compare probe_120 vs saturation_1000", {},
        )
        assert result["scenarios"] == ["probe_120", "saturation_1000"]
        assert "probe_120" in result
        assert "saturation_1000" in result
        assert "verdict" in result

    def test_compare_insufficient(self) -> None:
        result = self.engine.handle_compare("Compare probe_120 with nothing", {})
        assert "error" in result

    # -- what-if --

    def test_what_if_add_jammers(self) -> None:
        result = self.engine.handle_what_if(
            "What if we add 2 more jammers to saturation_1000?",
            {"scenario": "saturation_1000"},
        )
        assert "modification" in result
        assert "JAMMER" in result["modification"]
        assert result["estimated"]["leakers"] < 543.0

    def test_what_if_no_mod(self) -> None:
        result = self.engine.handle_what_if("What if the sky is blue?", {})
        assert "hint" in result

    # -- recommend --

    def test_recommend_with_leakers(self) -> None:
        ctx = {"active_hostiles": 100, "leakers": 10, "cost_exchange_ratio": 0.5}
        result = self.engine.handle_recommend("Recommend a response", ctx)
        assert result["priority"] == "HIGH"
        assert any("leakers" in r.lower() for r in result["recommendations"])

    def test_recommend_high_cost(self) -> None:
        ctx = {"active_hostiles": 50, "leakers": 0, "cost_exchange_ratio": 2.5}
        result = self.engine.handle_recommend("Recommend action", ctx)
        assert result["priority"] == "HIGH"

    def test_recommend_decoy(self) -> None:
        ctx = {"active_hostiles": 50, "leakers": 0, "swarm_intent": "DECOY"}
        result = self.engine.handle_recommend("Recommend action", ctx)
        assert any("decoy" in r.lower() for r in result["recommendations"])

    def test_recommend_all_clear(self) -> None:
        result = self.engine.handle_recommend("Recommend", {})
        assert result["priority"] == "LOW"

    # -- run --

    def test_run_with_scenario(self) -> None:
        result = self.engine.handle_run("Run probe_120 at 2x speed", {})
        assert result["scenario"] == "probe_120"
        assert result["speed_multiplier"] == 2.0

    def test_run_default_speed(self) -> None:
        result = self.engine.handle_run("Run skirmish_80", {})
        assert result["speed_multiplier"] == 1.0

    def test_run_no_scenario(self) -> None:
        result = self.engine.handle_run("Run something", {})
        assert "error" in result

    # -- unknown / fallback --

    def test_unknown_falls_back_to_intent_parser(self) -> None:
        result = self.engine.process("engage target 5")
        assert result.intent == "unknown"
        assert result.response.get("fallback") == "command"
        assert result.response.get("parsed_command") == "engage"

    def test_unknown_truly_unknown(self) -> None:
        result = self.engine.process("lorem ipsum dolor sit amet")
        assert result.intent == "unknown"
        assert result.response.get("fallback") == "none"

    # -- process routing --

    def test_process_routes_status(self) -> None:
        self.engine.set_state({"active_hostiles": 42, "tick": 1, "leakers": 0})
        result = self.engine.process("Status")
        assert result.intent == "current_status"
        assert result.response["active_hostiles"] == 42

    def test_process_routes_compare(self) -> None:
        result = self.engine.process("Compare probe_120 vs saturation_1000")
        assert result.intent == "compare_scenarios"

    def test_process_merges_context(self) -> None:
        self.engine.set_state({"active_hostiles": 10, "tick": 5})
        result = self.engine.process("Status", {"leakers": 3})
        assert result.response["active_hostiles"] == 10
        assert result.response["leakers"] == 3


# ---------------------------------------------------------------------------
# FastAPI endpoint
# ---------------------------------------------------------------------------

class TestCopilotEndpoint:

    def setup_method(self) -> None:
        self.app = FastAPI()
        self.app.include_router(router, prefix="/api/v1")
        self.client = TestClient(self.app)

    def test_status_query(self) -> None:
        resp = self.client.post(
            "/api/v1/copilot",
            json={"query": "Status", "context": {"active_hostiles": 5, "tick": 1}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["intent"] == "current_status"
        assert data["response"]["threat_level"] == "LOW"

    def test_compare_query(self) -> None:
        resp = self.client.post(
            "/api/v1/copilot",
            json={"query": "Compare probe_120 vs saturation_1000"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["intent"] == "compare_scenarios"

    def test_explain_query(self) -> None:
        resp = self.client.post(
            "/api/v1/copilot",
            json={
                "query": "Explain why track 7 leaked",
                "context": {"leakers": [7], "defenders_available": 0},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["intent"] == "explain_engagement"
        assert data["response"]["status"] == "LEAKED"

    def test_empty_query_rejected(self) -> None:
        resp = self.client.post("/api/v1/copilot", json={"query": ""})
        assert resp.status_code == 422

    def test_missing_query_rejected(self) -> None:
        resp = self.client.post("/api/v1/copilot", json={})
        assert resp.status_code == 422

    def test_recommend_query(self) -> None:
        resp = self.client.post(
            "/api/v1/copilot",
            json={
                "query": "Recommend action",
                "context": {"active_hostiles": 300, "leakers": 50},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["intent"] == "recommendation"
        assert data["response"]["priority"] == "HIGH"
