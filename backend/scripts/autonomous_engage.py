"""
Autonomous engagement pipeline for OVERWATCH.

Runs the full detect-track-classify-authorize-engage loop at 5Hz. This is
the golden demo script: plug in a camera and a drone, and OVERWATCH handles
everything from detection to intercept.

Usage:
    python3 -m scripts.autonomous_engage --mode simulation
    python3 -m scripts.autonomous_engage --mode camera --model models/drone_seraphim_best.pt
    python3 -m scripts.autonomous_engage --mode full --model models/drone_seraphim_best.pt

Modes:
    simulation  - Mock detections, no hardware needed
    camera      - Live webcam + YOLOv11, simulated engagement
    full        - Live camera + real MAVLink drone for intercept
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import random
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from csontology import (
    Defender,
    DefenderKind,
    DefenderStatus,
    Detection,
    Engagement,
    Site,
    Threat,
    Track,
    TrackClass,
    Vec3,
    enu_to_latlon,
)
from decision.models import EngagementPriority
from decision.roe import ROEEngine, ROERule
from defense.allocator import GreedyAllocator, PositionResolver
from defense.effector_handoff import EffectorController, SlewCommand
from fusion.track_manager import TrackManager
from threat.classifier import assess

logger = logging.getLogger("overwatch.engage")


# ---------------------------------------------------------------------------
# Detection sources (pluggable)
# ---------------------------------------------------------------------------

class DetectionSource(ABC):
    """Base class for detection sources."""

    @abstractmethod
    async def get_detections(self) -> List[Detection]:
        raise NotImplementedError

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass


class MockDetectionSource(DetectionSource):
    """Generates fake drone detections converging on the site."""

    def __init__(self, site_pos: Vec3, count: int = 3) -> None:
        self._site = site_pos
        self._count = count
        self._tick = 0
        self._drones: List[Dict[str, Any]] = []
        self._init_drones()

    def _init_drones(self) -> None:
        for i in range(self._count):
            angle = (2 * math.pi * i) / self._count
            dist = random.uniform(600.0, 1200.0)
            x = self._site[0] + dist * math.cos(angle)
            y = self._site[1] + dist * math.sin(angle)
            z = random.uniform(30.0, 80.0)
            speed = random.uniform(8.0, 20.0)
            self._drones.append({
                "id": f"mock-{i:03d}",
                "x": x, "y": y, "z": z,
                "speed": speed, "angle": angle,
            })

    async def get_detections(self) -> List[Detection]:
        self._tick += 1
        now = time.time()
        detections: List[Detection] = []
        for drone in self._drones:
            dx = self._site[0] - drone["x"]
            dy = self._site[1] - drone["y"]
            dist = math.hypot(dx, dy)
            if dist < 5.0:
                continue
            vx = (dx / dist) * drone["speed"]
            vy = (dy / dist) * drone["speed"]
            drone["x"] += vx * 0.2
            drone["y"] += vy * 0.2
            jitter = 2.0
            detections.append(Detection(
                id=f"{drone['id']}-{self._tick}",
                timestamp=now,
                position=(
                    drone["x"] + random.gauss(0, jitter),
                    drone["y"] + random.gauss(0, jitter),
                    drone["z"] + random.gauss(0, 0.5),
                ),
                velocity=(vx, vy, 0.0),
                confidence=random.uniform(0.75, 0.98),
                sensor_id="mock-sensor",
            ))
        return detections


class CameraDetectionSource(DetectionSource):
    """Uses live webcam + YOLOv11 model for detection."""

    def __init__(self, model_path: str, site_pos: Vec3) -> None:
        self._model_path = model_path
        self._site = site_pos
        self._model: Any = None
        self._cap: Any = None
        self._frame_count = 0

    async def start(self) -> None:
        import cv2
        from ultralytics import YOLO
        logger.info("Loading YOLO model: %s", self._model_path)
        self._model = YOLO(self._model_path)
        self._cap = cv2.VideoCapture(0)
        if not self._cap.isOpened():
            raise RuntimeError("Cannot open camera")
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        logger.info("Camera opened successfully")

    async def stop(self) -> None:
        if self._cap is not None:
            self._cap.release()

    async def get_detections(self) -> List[Detection]:
        if self._model is None or self._cap is None:
            return []
        ret, frame = self._cap.read()
        if not ret:
            return []
        self._frame_count += 1
        now = time.time()
        results = self._model(frame, verbose=False, conf=0.4)
        return self._results_to_detections(results, now)

    def _results_to_detections(
        self, results: Any, now: float,
    ) -> List[Detection]:
        detections: List[Detection] = []
        for r in results:
            for box in r.boxes:
                cx = float((box.xyxy[0][0] + box.xyxy[0][2]) / 2)
                cy = float((box.xyxy[0][1] + box.xyxy[0][3]) / 2)
                conf = float(box.conf[0])
                bearing = (cx / 1280.0 - 0.5) * 60.0
                elev = (0.5 - cy / 720.0) * 40.0
                est_range = 200.0 + (1.0 - conf) * 800.0
                rad = math.radians(bearing)
                x = self._site[0] + est_range * math.sin(rad)
                y = self._site[1] + est_range * math.cos(rad)
                z = max(15.0, est_range * math.tan(math.radians(elev)))
                det_id = f"cam-{self._frame_count}-{len(detections)}"
                detections.append(Detection(
                    id=det_id,
                    timestamp=now,
                    position=(x, y, z),
                    velocity=(0.0, 0.0, 0.0),
                    confidence=conf,
                    sensor_id="webcam-0",
                ))
        return detections


# ---------------------------------------------------------------------------
# Engagement executors (pluggable)
# ---------------------------------------------------------------------------

class EngagementExecutor(ABC):
    """Base class for engagement execution backends."""

    @abstractmethod
    async def execute(self, slew: SlewCommand) -> bool:
        raise NotImplementedError


class SimulatedEngagement(EngagementExecutor):
    """Logs the engagement without sending real commands."""

    async def execute(self, slew: SlewCommand) -> bool:
        logger.info(
            "SIM ENGAGE: %s -> %s bearing=%.1f range=%.1f",
            slew.defender_id, slew.target_id,
            slew.bearing_deg, slew.range_m,
        )
        return True


class MAVLinkEngagement(EngagementExecutor):
    """Sends GOTO command to a real PX4/ArduPilot drone via MAVSDK."""

    def __init__(self, site_pos: Vec3) -> None:
        self._site = site_pos
        self._system: Any = None

    async def start(self) -> None:
        try:
            from mavsdk import System
            self._system = System()
            await self._system.connect(system_address="udp://:14540")
            logger.info("MAVLink connected")
        except Exception as exc:
            logger.error("MAVLink connection failed: %s", exc)
            self._system = None

    async def execute(self, slew: SlewCommand) -> bool:
        if self._system is None:
            logger.warning("MAVLink not connected, skipping command")
            return False
        rad = math.radians(slew.lead_bearing_deg)
        x = slew.range_m * math.sin(rad)
        y = slew.range_m * math.cos(rad)
        lat, lon, _ = enu_to_latlon(x, y, 30.0)
        try:
            await self._system.action.goto_location(lat, lon, 30.0, 0.0)
            logger.info(
                "MAVLINK GOTO: %s -> %.6f, %.6f",
                slew.defender_id, lat, lon,
            )
            return True
        except Exception as exc:
            logger.error("MAVLink GOTO failed: %s", exc)
            return False


# ---------------------------------------------------------------------------
# Tick audit record
# ---------------------------------------------------------------------------

@dataclass
class TickRecord:
    """One tick of the engagement loop for logging and broadcast."""
    tick: int
    elapsed_s: float
    detection_count: int
    track_count: int
    hostile_count: int
    threat_count: int
    authorized_count: int
    engagement_count: int
    engagements: List[Dict[str, Any]] = field(default_factory=list)
    top_threat_tti: Optional[float] = None
    status: str = "IDLE"

    def status_line(self) -> str:
        tti_str = f"{self.top_threat_tti:.1f}s" if self.top_threat_tti else "N/A"
        eng_str = "NONE"
        if self.engagements:
            e = self.engagements[0]
            eng_str = (
                f"{e['defender_id']} -> {e['target_id']} "
                f"(bearing {e['bearing_deg']:.0f} deg, range {e['range_m']:.0f}m)"
            )
        roe_str = "AUTHORIZED" if self.authorized_count > 0 else "HOLD"
        return (
            f"[T+{self.elapsed_s:06.1f}] "
            f"DETECT: {self.detection_count} objects | "
            f"TRACK: {self.track_count} active | "
            f"THREAT: {self.hostile_count} hostile (TTI {tti_str}) | "
            f"ROE: {roe_str} | "
            f"ENGAGE: {eng_str} | "
            f"STATUS: {self.status}"
        )


# ---------------------------------------------------------------------------
# Autonomous engagement loop
# ---------------------------------------------------------------------------

class AutonomousEngagementLoop:
    """Runs the full detect-track-classify-authorize-engage loop.

    Modes:
    - SIMULATION: Uses mock detections (no hardware needed)
    - CAMERA_ONLY: Uses live webcam, simulated engagement
    - FULL: Uses live camera + real MAVLink drone for intercept
    """

    def __init__(
        self,
        detector: DetectionSource,
        executor: EngagementExecutor,
        site: Site,
        defenders: List[Defender],
        corridors: List[Tuple[Vec3, float]],
        tick_rate: float = 5.0,
        classify_threshold: float = 0.7,
    ) -> None:
        self._detector = detector
        self._executor = executor
        self._site = site
        self._defenders = defenders
        self._corridors = corridors
        self._tick_rate = tick_rate
        self._classify_threshold = classify_threshold

        self._track_manager = TrackManager()
        self._roe_engine = ROEEngine()
        self._effector = EffectorController()
        self._tick_count = 0
        self._start_time = 0.0
        self._running = False

        self._defender_map: Dict[str, Defender] = {
            d.id: d for d in defenders
        }
        self._track_map: Dict[str, Track] = {}
        self._ws_clients: set = set()

        resolver: PositionResolver = self._resolve_threat_position
        self._allocator = GreedyAllocator(resolve_position=resolver)

    def _resolve_threat_position(self, threat: Threat) -> Optional[Vec3]:
        if threat.track_id and threat.track_id in self._track_map:
            return self._track_map[threat.track_id].position
        return None

    async def start(self) -> None:
        """Initialize all subsystems."""
        await self._detector.start()
        self._start_time = time.time()
        self._running = True
        logger.info("Autonomous engagement loop started")

    async def stop(self) -> None:
        """Shut down all subsystems."""
        self._running = False
        await self._detector.stop()
        logger.info("Autonomous engagement loop stopped")

    async def tick(self) -> TickRecord:
        """Run one full iteration of the kill chain."""
        self._tick_count += 1
        now = time.time()
        elapsed = now - self._start_time

        # 1. DETECT
        detections = await self._detector.get_detections()

        # 2. TRACK
        tracks = self._track_manager.update(detections, now)
        self._track_map = {t.id: t for t in tracks}

        # 3. CLASSIFY
        self._classify_tracks(tracks)

        # 4. ASSESS
        threats = assess(tracks, self._site, now)

        # 5. AUTHORIZE
        authorized = self._authorize_threats(threats, now)

        # 6. ALLOCATE
        engagements = self._allocator.allocate(
            authorized, self._defenders, now,
        )

        # 7. ENGAGE
        slew_results = await self._execute_engagements(engagements, now)

        # 8. LOG
        record = self._build_record(
            elapsed, detections, tracks, threats,
            authorized, slew_results,
        )
        await self._broadcast(record, detections, tracks, threats, slew_results)
        return record

    def _classify_tracks(self, tracks: List[Track]) -> None:
        for track in tracks:
            if track.classification != TrackClass.HOSTILE:
                if track.confidence >= self._classify_threshold:
                    self._track_manager.classify_track(
                        track.id, TrackClass.HOSTILE,
                    )

    def _authorize_threats(
        self, threats: List[Threat], now: float,
    ) -> List[Threat]:
        authorized: List[Threat] = []
        for threat in threats:
            position = self._resolve_threat_position(threat)
            if position is None:
                continue
            priority = EngagementPriority(
                target_id=threat.id,
                source="bulwark",
                normalized_score=min(1.0, threat.score),
                time_sensitivity=threat.time_to_impact_s or 300.0,
                personnel_impact=0,
                cascade_depth=0,
                recommended_effector="any",
            )
            evaluation = self._roe_engine.evaluate(
                target_id=threat.id,
                engagement_priority=priority,
                track_confidence=threat.confidence,
                target_position=position,
                personnel_at_risk=0,
                corridors=self._corridors,
                timestamp=now,
            )
            if evaluation.authorized:
                authorized.append(threat)
        return authorized

    async def _execute_engagements(
        self,
        engagements: List[Engagement],
        now: float,
    ) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        for eng in engagements:
            defender = self._defender_map.get(eng.defender_id)
            if defender is None:
                continue
            target_pos = self._resolve_threat_position_by_id(
                eng.target_threat_id,
            )
            if target_pos is None:
                continue
            track = self._find_track_for_threat(eng.target_threat_id)
            vel: Vec3 = track.velocity if track else (0.0, 0.0, 0.0)
            slew = self._effector.compute_slew(
                defender=defender,
                target_position=target_pos,
                target_velocity=vel,
                target_id=eng.target_threat_id,
                priority=1,
                timestamp=now,
            )
            await self._executor.execute(slew)
            results.append(slew.to_dict())
        return results

    def _resolve_threat_position_by_id(
        self, threat_id: str,
    ) -> Optional[Vec3]:
        track_id = threat_id.replace("threat-", "")
        track = self._track_map.get(track_id)
        return track.position if track else None

    def _find_track_for_threat(self, threat_id: str) -> Optional[Track]:
        track_id = threat_id.replace("threat-", "")
        return self._track_map.get(track_id)

    def _build_record(
        self,
        elapsed: float,
        detections: List[Detection],
        tracks: List[Track],
        threats: List[Threat],
        authorized: List[Threat],
        slew_results: List[Dict[str, Any]],
    ) -> TickRecord:
        hostile_count = sum(
            1 for t in tracks if t.classification == TrackClass.HOSTILE
        )
        top_tti = None
        if threats:
            top_tti = threats[0].time_to_impact_s
        status = "IDLE"
        if slew_results:
            status = "ENGAGING"
        elif authorized:
            status = "AUTHORIZED"
        elif threats:
            status = "TRACKING"
        elif detections:
            status = "DETECTING"
        return TickRecord(
            tick=self._tick_count,
            elapsed_s=elapsed,
            detection_count=len(detections),
            track_count=len(tracks),
            hostile_count=hostile_count,
            threat_count=len(threats),
            authorized_count=len(authorized),
            engagement_count=len(slew_results),
            engagements=slew_results,
            top_threat_tti=top_tti,
            status=status,
        )

    # --- WebSocket broadcast ---

    def register_ws_client(self, ws: Any) -> None:
        self._ws_clients.add(ws)

    def unregister_ws_client(self, ws: Any) -> None:
        self._ws_clients.discard(ws)

    async def _broadcast(
        self,
        record: TickRecord,
        detections: List[Detection],
        tracks: List[Track],
        threats: List[Threat],
        slew_results: List[Dict[str, Any]],
    ) -> None:
        if not self._ws_clients:
            return
        frame = {
            "type": "ENGAGEMENT_FRAME",
            "tick": record.tick,
            "elapsed_s": round(record.elapsed_s, 2),
            "detections": [_det_dict(d) for d in detections],
            "tracks": [_track_dict(t) for t in tracks],
            "threats": [_threat_dict(t) for t in threats],
            "engagements": slew_results,
            "roe_evaluations": [
                _roe_dict(e) for e in self._roe_engine.audit_log[-len(threats):]
            ] if threats else [],
            "status": record.status,
        }
        msg = json.dumps(frame)
        dead: set = set()
        for ws in self._ws_clients:
            try:
                await ws.send(msg)
            except Exception:
                dead.add(ws)
        self._ws_clients -= dead

    async def run(self) -> None:
        """Run the loop continuously at the configured tick rate."""
        await self.start()
        interval = 1.0 / self._tick_rate
        try:
            while self._running:
                t0 = time.time()
                record = await self.tick()
                print(record.status_line())
                dt = time.time() - t0
                sleep_time = max(0.0, interval - dt)
                await asyncio.sleep(sleep_time)
        except KeyboardInterrupt:
            logger.info("Interrupted by user")
        finally:
            await self.stop()


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def _det_dict(d: Detection) -> dict:
    return {
        "id": d.id,
        "position": list(d.position),
        "velocity": list(d.velocity),
        "confidence": round(d.confidence, 3),
        "sensor_id": d.sensor_id,
    }


def _track_dict(t: Track) -> dict:
    return {
        "id": t.id,
        "position": list(t.position),
        "velocity": list(t.velocity),
        "classification": t.classification.value,
        "confidence": round(t.confidence, 3),
    }


def _threat_dict(t: Threat) -> dict:
    return {
        "id": t.id,
        "score": round(t.score, 4),
        "time_to_impact_s": round(t.time_to_impact_s, 2) if t.time_to_impact_s else None,
        "priority_rank": t.priority_rank,
        "track_id": t.track_id,
        "range_to_site_m": round(t.range_to_site_m, 1) if t.range_to_site_m else None,
    }


def _roe_dict(e: Any) -> dict:
    return {
        "target_id": e.target_id,
        "authorized": e.authorized,
        "rule_name": e.rule_name,
        "reason": e.reason,
    }


# ---------------------------------------------------------------------------
# Default site and defenders
# ---------------------------------------------------------------------------

def build_default_site(lat: float, lon: float) -> Site:
    """Create a default defended site."""
    return Site(
        id="site-alpha",
        position=(0.0, 0.0, 0.0),
        protected_assets=["command-post", "radar-array"],
        value=500_000.0,
    )


def build_default_defenders() -> List[Defender]:
    """Create a default defender set for the demo."""
    return [
        Defender(
            id="INTERCEPTOR-001",
            position=(50.0, 50.0, 0.0),
            kind=DefenderKind.INTERCEPTOR,
            capacity=8,
            range_m=2000.0,
            reload_s=3.0,
            kill_prob=0.85,
            unit_cost=5000.0,
        ),
        Defender(
            id="JAMMER-001",
            position=(-30.0, 20.0, 0.0),
            kind=DefenderKind.JAMMER,
            capacity=100,
            range_m=1500.0,
            reload_s=0.5,
            kill_prob=0.6,
            unit_cost=50.0,
            effect_radius_m=200.0,
            max_simultaneous=10,
        ),
        Defender(
            id="INTERCEPTOR-002",
            position=(-50.0, -50.0, 0.0),
            kind=DefenderKind.INTERCEPTOR,
            capacity=8,
            range_m=2000.0,
            reload_s=3.0,
            kill_prob=0.85,
            unit_cost=5000.0,
        ),
    ]


def build_default_corridors(site_pos: Vec3) -> List[Tuple[Vec3, float]]:
    """Create a default engagement corridor around the site."""
    return [(site_pos, 3000.0)]


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

async def run_ws_server(loop: AutonomousEngagementLoop, port: int) -> None:
    """Start a WebSocket server broadcasting engagement frames."""
    import websockets

    async def handler(ws: Any) -> None:
        loop.register_ws_client(ws)
        logger.info("Engagement WS client connected")
        try:
            await ws.wait_closed()
        finally:
            loop.unregister_ws_client(ws)
            logger.info("Engagement WS client disconnected")

    server = await websockets.serve(handler, "0.0.0.0", port)
    logger.info("Engagement WebSocket server on ws://localhost:%d", port)
    await asyncio.Future()


async def main_async(args: argparse.Namespace) -> None:
    """Wire up components and run the loop."""
    site = build_default_site(args.site_lat, args.site_lon)
    defenders = build_default_defenders()
    corridors = build_default_corridors(site.position)

    if args.mode == "simulation":
        detector: DetectionSource = MockDetectionSource(site.position)
        executor: EngagementExecutor = SimulatedEngagement()
    elif args.mode == "camera":
        detector = CameraDetectionSource(args.model, site.position)
        executor = SimulatedEngagement()
    elif args.mode == "full":
        detector = CameraDetectionSource(args.model, site.position)
        mav = MAVLinkEngagement(site.position)
        await mav.start()
        executor = mav
    else:
        raise ValueError(f"Unknown mode: {args.mode}")

    loop = AutonomousEngagementLoop(
        detector=detector,
        executor=executor,
        site=site,
        defenders=defenders,
        corridors=corridors,
        tick_rate=args.tick_rate,
    )

    ws_task = asyncio.create_task(run_ws_server(loop, args.ws_port))
    engage_task = asyncio.create_task(loop.run())
    await asyncio.gather(engage_task, ws_task)


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="OVERWATCH Autonomous Engagement Pipeline",
    )
    parser.add_argument(
        "--mode", choices=["simulation", "camera", "full"],
        default="simulation", help="Operating mode",
    )
    parser.add_argument(
        "--model", default="models/drone_seraphim_best.pt",
        help="Path to YOLO model weights",
    )
    parser.add_argument(
        "--tick-rate", type=float, default=5.0,
        help="Loop iterations per second",
    )
    parser.add_argument(
        "--site-lat", type=float, default=33.6405,
        help="Defended site latitude",
    )
    parser.add_argument(
        "--site-lon", type=float, default=-117.8443,
        help="Defended site longitude",
    )
    parser.add_argument(
        "--ws-port", type=int, default=8770,
        help="WebSocket broadcast port",
    )
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        logger.info("Shutdown")


if __name__ == "__main__":
    main()
