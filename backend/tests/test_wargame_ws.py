"""Integration test for the wargame websocket handler and its frame stream.

Drives api.websocket.WebSocketHandler.handle_wargame directly with a fake
websocket, so it exercises the real handler to runner to frame.to_dict path that
the HUD consumes, without standing up the full app lifespan. This verifies the
streamed payload shape end to end, not just frame.to_dict in isolation.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import WebSocketDisconnect

from api.websocket import WebSocketHandler


class _FakeApp:
    """Minimal stand-in for the OverwatchApp the handler reads. No event DB."""

    db = None


class _FakeWebSocket:
    """Captures sent frames and disconnects after a frame budget.

    handle_wargame only accepts then sends, so raising WebSocketDisconnect from
    send_text after enough frames ends the run the way a client close would.
    """

    def __init__(self, max_frames: int) -> None:
        self.sent: list = []
        self._max = max_frames

    async def accept(self) -> None:
        return None

    async def send_text(self, text: str) -> None:
        self.sent.append(text)
        if len(self.sent) >= self._max:
            raise WebSocketDisconnect()


def _drive(scenario: str, max_frames: int) -> list:
    """Run the handler against a fake socket and return the JSON frames sent."""
    handler = WebSocketHandler(_FakeApp())
    ws = _FakeWebSocket(max_frames)
    asyncio.run(handler.handle_wargame(ws, scenario))
    return [json.loads(t) for t in ws.sent]


def test_wargame_ws_streams_frames_with_new_fields() -> None:
    frames = _drive("probe_120", max_frames=4)
    assert len(frames) >= 1
    for frame in frames:
        assert frame["type"] == "WARGAME_FRAME"
        assert "tracks" in frame and "defenders" in frame and "assignments" in frame
        metrics = frame["metrics"]
        # The HUD scoreboard and cost-exchange card read these.
        for key in ("leakers", "intercepts", "cost_exchange_ratio", "cost_exchange_win"):
            assert key in metrics
        # A defined ratio is a float, an undefined one is null, never NaN text.
        assert metrics["cost_exchange_ratio"] is None or isinstance(
            metrics["cost_exchange_ratio"], float
        )
        # Tracks carry the intent the HUD colors by, when any tracks exist.
        for track in frame["tracks"]:
            assert "intent" in track and isinstance(track["intent"], str)


def test_wargame_ws_unknown_scenario_sends_error() -> None:
    frames = _drive("does_not_exist", max_frames=4)
    assert frames
    assert frames[0]["type"] == "ERROR"
