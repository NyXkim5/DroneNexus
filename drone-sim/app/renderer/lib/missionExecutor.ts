/**
 * Mission execution engine — pure functions, no side effects.
 *
 * Computes desired velocity, heading, and waypoint advancement each tick.
 * The caller (sim loop) is responsible for reading/writing store state.
 */
import type { MissionData, MissionWaypointENU } from '../stores/simStore';

// ---------------------------------------------------------------------------
// Public interfaces
// ---------------------------------------------------------------------------

export interface MissionExecutorInput {
  mission: MissionData;
  currentWaypointIndex: number;
  dronePosition: { x: number; y: number; z: number }; // ENU meters
  waypointHoldRemaining: number; // seconds remaining at current WP
  elapsed: number; // seconds since last tick
}

export interface MissionExecutorOutput {
  desiredVelocity: { x: number; y: number; z: number }; // m/s ENU
  desiredHeading: number; // degrees, 0 = north, CW positive
  nextWaypointIndex: number; // same or incremented
  holdRemaining: number; // seconds
  missionComplete: boolean;
  triggerLanding: boolean; // true when land waypoint reached
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const RAD_TO_DEG = 180 / Math.PI;

/** Horizontal (XY) distance between two points. */
function horizontalDistance(
  a: { x: number; y: number },
  b: { x: number; y: number },
): number {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  return Math.sqrt(dx * dx + dy * dy);
}

/** 3D Euclidean distance. */
function distance3D(
  a: { x: number; y: number; z: number },
  b: { x: number; y: number; z: number },
): number {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const dz = b.z - a.z;
  return Math.sqrt(dx * dx + dy * dy + dz * dz);
}

/** Normalize a 3D vector; returns zero vector when magnitude is near-zero. */
function normalize(v: { x: number; y: number; z: number }): { x: number; y: number; z: number } {
  const mag = Math.sqrt(v.x * v.x + v.y * v.y + v.z * v.z);
  if (mag < 1e-9) return { x: 0, y: 0, z: 0 };
  return { x: v.x / mag, y: v.y / mag, z: v.z / mag };
}

/**
 * Compute compass heading from an ENU velocity vector.
 * Convention: atan2(east, north) gives 0 = north, CW positive.
 */
function headingFromVelocity(vel: { x: number; y: number }): number {
  const deg = Math.atan2(vel.x, vel.y) * RAD_TO_DEG;
  return ((deg % 360) + 360) % 360; // ensure [0, 360)
}

/** Resolve effective acceptance radius for a waypoint. */
function resolveAcceptanceRadius(wp: MissionWaypointENU, defaults: MissionData['defaults']): number {
  return wp.acceptance_radius_m > 0 ? wp.acceptance_radius_m : defaults.acceptance_radius_m;
}

/** Resolve effective speed for a waypoint. */
function resolveSpeed(wp: MissionWaypointENU, defaults: MissionData['defaults']): number {
  return wp.speed_ms > 0 ? wp.speed_ms : defaults.speed_ms;
}

// ---------------------------------------------------------------------------
// Core executor
// ---------------------------------------------------------------------------

/**
 * Compute one tick of mission execution.
 *
 * This is a pure function: no store access, no side effects.  All state
 * transitions are communicated through the returned output object.
 */
export function executeMission(input: MissionExecutorInput): MissionExecutorOutput {
  const { mission, currentWaypointIndex, dronePosition, waypointHoldRemaining, elapsed } = input;
  const { waypoints, defaults } = mission;

  // Default output — safe idle state
  const output: MissionExecutorOutput = {
    desiredVelocity: { x: 0, y: 0, z: 0 },
    desiredHeading: 0,
    nextWaypointIndex: currentWaypointIndex,
    holdRemaining: waypointHoldRemaining,
    missionComplete: false,
    triggerLanding: false,
  };

  // Mission complete guard
  if (currentWaypointIndex >= waypoints.length) {
    output.missionComplete = true;
    return output;
  }

  const wp = waypoints[currentWaypointIndex];
  const wpPos = { x: wp.position.x_m, y: wp.position.y_m, z: wp.position.z_m };
  const speed = resolveSpeed(wp, defaults);
  const acceptRadius = resolveAcceptanceRadius(wp, defaults);

  switch (wp.type) {
    case 'takeoff':
      handleTakeoff(output, wp, wpPos, dronePosition, speed, elapsed, waypointHoldRemaining, waypoints.length);
      break;

    case 'waypoint':
    case 'orbit': // treat orbit like waypoint for now
    case 'survey_start':
    case 'survey_end':
      handleWaypoint(output, wp, wpPos, dronePosition, speed, acceptRadius, elapsed, waypointHoldRemaining, waypoints.length);
      break;

    case 'land':
      handleLand(output, wpPos, dronePosition, speed);
      break;

    default:
      // Unknown type — hold position
      break;
  }

  // --- Heading ---
  if (wp.heading_deg !== null) {
    output.desiredHeading = wp.heading_deg;
  } else {
    const v = output.desiredVelocity;
    if (v.x * v.x + v.y * v.y > 1e-6) {
      output.desiredHeading = headingFromVelocity(v);
    }
    // If velocity is ~zero and no explicit heading, heading stays at 0 (default).
  }

  return output;
}

// ---------------------------------------------------------------------------
// Waypoint type handlers
// ---------------------------------------------------------------------------

function handleTakeoff(
  out: MissionExecutorOutput,
  wp: MissionWaypointENU,
  wpPos: { x: number; y: number; z: number },
  pos: { x: number; y: number; z: number },
  climbRate: number,
  elapsed: number,
  holdRemaining: number,
  totalWaypoints: number,
): void {
  const targetAlt = wpPos.z;

  if (pos.z < targetAlt - 0.1) {
    // Still climbing — vertical only
    out.desiredVelocity = { x: 0, y: 0, z: climbRate };
    out.holdRemaining = wp.hold_time_s; // reset hold until we reach altitude
  } else {
    // At target altitude — run hold timer
    out.desiredVelocity = { x: 0, y: 0, z: 0 };

    if (holdRemaining > 0) {
      out.holdRemaining = Math.max(0, holdRemaining - elapsed);
    } else if (wp.hold_time_s > 0 && holdRemaining <= 0 && holdRemaining === wp.hold_time_s) {
      // First frame at altitude with a hold configured — this branch won't
      // trigger since we keep resetting holdRemaining above.  Instead we
      // rely on the check in the climbing branch: holdRemaining is seeded
      // with hold_time_s while climbing, so once climbing stops it simply
      // counts down.
      out.holdRemaining = Math.max(0, holdRemaining - elapsed);
    } else {
      // Hold already counting or zero
      out.holdRemaining = Math.max(0, holdRemaining - elapsed);
    }

    if (out.holdRemaining <= 0) {
      // Advance
      const nextIdx = out.nextWaypointIndex + 1;
      if (nextIdx >= totalWaypoints) {
        out.missionComplete = true;
      } else {
        out.nextWaypointIndex = nextIdx;
        out.holdRemaining = 0;
      }
    }
  }
}

function handleWaypoint(
  out: MissionExecutorOutput,
  wp: MissionWaypointENU,
  wpPos: { x: number; y: number; z: number },
  pos: { x: number; y: number; z: number },
  speed: number,
  acceptRadius: number,
  elapsed: number,
  holdRemaining: number,
  totalWaypoints: number,
): void {
  const hDist = horizontalDistance(pos, wpPos);
  const dir = { x: wpPos.x - pos.x, y: wpPos.y - pos.y, z: wpPos.z - pos.z };
  const norm = normalize(dir);

  if (hDist < acceptRadius) {
    // ---------- Inside acceptance radius ----------
    out.desiredVelocity = { x: 0, y: 0, z: 0 }; // hold position

    if (wp.hold_time_s <= 0) {
      // No hold — advance immediately
      const nextIdx = out.nextWaypointIndex + 1;
      if (nextIdx >= totalWaypoints) {
        out.missionComplete = true;
      } else {
        out.nextWaypointIndex = nextIdx;
        out.holdRemaining = 0;
      }
    } else {
      // Hold required
      if (holdRemaining <= 0) {
        // Hold not yet started — initialize it
        out.holdRemaining = wp.hold_time_s;
      } else {
        out.holdRemaining = Math.max(0, holdRemaining - elapsed);

        if (out.holdRemaining <= 0) {
          // Hold complete — advance
          const nextIdx = out.nextWaypointIndex + 1;
          if (nextIdx >= totalWaypoints) {
            out.missionComplete = true;
          } else {
            out.nextWaypointIndex = nextIdx;
            out.holdRemaining = 0;
          }
        }
      }
    }
  } else {
    // ---------- Steering toward waypoint ----------
    let effectiveSpeed = speed;

    // Approach slowdown: linearly scale speed down when within 3x acceptance radius
    const slowdownThreshold = acceptRadius * 3;
    if (hDist < slowdownThreshold) {
      const fraction = hDist / slowdownThreshold; // 0 at wp, 1 at threshold
      const minApproachSpeed = 0.5;
      effectiveSpeed = minApproachSpeed + (speed - minApproachSpeed) * fraction;
    }

    out.desiredVelocity = {
      x: norm.x * effectiveSpeed,
      y: norm.y * effectiveSpeed,
      z: norm.z * effectiveSpeed,
    };

    // Not at waypoint yet — carry over hold state untouched
    out.holdRemaining = holdRemaining;
  }
}

function handleLand(
  out: MissionExecutorOutput,
  wpPos: { x: number; y: number; z: number },
  pos: { x: number; y: number; z: number },
  speed: number,
): void {
  const hDist = horizontalDistance(pos, wpPos);

  if (hDist < 2.0) {
    // Close enough horizontally — trigger landing
    out.desiredVelocity = { x: 0, y: 0, z: 0 };
    out.triggerLanding = true;
    out.missionComplete = true;
  } else {
    // Steer toward land position
    const dir = { x: wpPos.x - pos.x, y: wpPos.y - pos.y, z: wpPos.z - pos.z };
    const norm = normalize(dir);
    out.desiredVelocity = {
      x: norm.x * speed,
      y: norm.y * speed,
      z: norm.z * speed,
    };
  }
}

// ---------------------------------------------------------------------------
// YAML parser
// ---------------------------------------------------------------------------

/**
 * Convert a raw (already JSON-parsed) YAML mission object into MissionData.
 *
 * The `raw` parameter is the JavaScript object produced by js-yaml (or
 * equivalent) in the main process IPC handler.  Its shape matches the
 * mission YAML schema (see simple-square.yaml).
 */
export function parseMissionYAML(raw: any): MissionData {
  const params = raw.parameters ?? {};
  const defs = params.defaults ?? {};
  const safety = params.safety ?? {};

  const defaultSpeed = defs.speed_ms ?? 5.0;
  const defaultAcceptance = defs.acceptance_radius_m ?? 2.0;
  const defaultAltitude = defs.altitude_m ?? 20.0;

  const waypoints: MissionWaypointENU[] = (raw.waypoints ?? []).map((wp: any) => ({
    id: wp.id,
    name: wp.name ?? `WP-${wp.id}`,
    type: wp.type ?? 'waypoint',
    position: {
      x_m: wp.position?.x_m ?? 0,
      y_m: wp.position?.y_m ?? 0,
      z_m: wp.position?.z_m ?? defaultAltitude,
    },
    speed_ms: wp.speed_ms ?? defaultSpeed,
    heading_deg: wp.heading_deg ?? null,
    hold_time_s: wp.hold_time_s ?? 0,
    acceptance_radius_m: wp.acceptance_radius_m ?? defaultAcceptance,
  }));

  return {
    name: raw.mission?.name ?? 'Untitled Mission',
    description: raw.mission?.description ?? '',
    waypoints,
    defaults: {
      altitude_m: defaultAltitude,
      speed_ms: defaultSpeed,
      acceptance_radius_m: defaultAcceptance,
    },
    safety: {
      geofence_radius_m: safety.geofence_radius_m ?? 200.0,
      max_altitude_m: safety.max_altitude_m ?? 50.0,
    },
  };
}
