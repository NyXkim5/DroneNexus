/**
 * Local physics simulation loop — drives telemetry without Docker/ROS2.
 * Runs at ~30Hz, updates the simStore with realistic-ish flight behavior.
 * Supports position-based flight physics with mission execution.
 */
import { useEffect, useRef } from 'react';
import { useSimStore, SimState } from '../stores/simStore';
import { executeMission, MissionExecutorInput, MissionExecutorOutput } from './missionExecutor';

const DT = 1 / 30; // seconds per tick
const TAKEOFF_ALT = 10; // meters target
const CLIMB_RATE = 3; // m/s
const DESCENT_RATE = 2; // m/s
const HOVER_CURRENT = 8.5; // amps
const BATTERY_FULL_V = 25.2;
const BATTERY_EMPTY_V = 19.8;
const BATTERY_CAPACITY_MAH = 1300;
const RTL_SPEED = 5; // m/s
const RTL_ARRIVAL_RADIUS = 2; // meters
const TRAIL_INTERVAL = 0.5; // seconds between trail points
const VELOCITY_DECAY = 3; // rate of velocity decay in loiter/alt_hold (m/s^2)

export function useSimLoop() {
  const rafId = useRef<number | null>(null);
  const lastTime = useRef<number>(0);
  const flightStart = useRef<number>(0);
  const mah_used = useRef<number>(0);
  const pos = useRef<{ x: number; y: number }>({ x: 0, y: 0 });
  const vel = useRef<{ vx: number; vy: number }>({ vx: 0, vy: 0 });
  const trailTimer = useRef<number>(0);

  useEffect(() => {
    function tick(timestamp: number) {
      const state = useSimStore.getState();
      if (!state.running) {
        rafId.current = requestAnimationFrame(tick);
        return;
      }

      if (lastTime.current === 0) lastTime.current = timestamp;
      const elapsed = Math.min((timestamp - lastTime.current) / 1000, 0.1);
      lastTime.current = timestamp;

      const { simState, telemetry, windSpeed, windDirection, gustIntensity, failsafeActive, flightMode } = state;
      const t = { ...telemetry };

      // Wind force components (wind direction: 0=north, 90=east, in degrees)
      const windRadians = (windDirection * Math.PI) / 180;
      const windForceX = Math.sin(windRadians) * windSpeed * 0.3; // east component
      const windForceY = Math.cos(windRadians) * windSpeed * 0.3; // north component

      // --- State machine transitions ---
      if (simState === 'ARMED') {
        t.armed = true;
        t.battery_current = 1.5; // idle draw
        t.pos_x = pos.current.x;
        t.pos_y = pos.current.y;
        t.vel_x = 0;
        t.vel_y = 0;
      }

      if (simState === 'TAKING_OFF') {
        if (flightStart.current === 0) flightStart.current = timestamp;
        t.armed = true;
        t.in_air = true;
        t.vertical_speed = CLIMB_RATE;
        t.alt_agl += CLIMB_RATE * elapsed;
        t.alt_msl = t.alt_agl + 10;
        t.battery_current = HOVER_CURRENT * 1.3;
        t.pitch = -2;
        t.roll = Math.sin(timestamp / 500) * 0.5;

        // Position stays at current pos ref values
        t.pos_x = pos.current.x;
        t.pos_y = pos.current.y;
        t.vel_x = 0;
        t.vel_y = 0;
        t.ground_speed = 0;

        if (t.alt_agl >= TAKEOFF_ALT) {
          t.alt_agl = TAKEOFF_ALT;
          t.vertical_speed = 0;
          t.pitch = 0;
          state.setSimState('FLYING');
        }
      }

      if (simState === 'FLYING') {
        t.armed = true;
        t.in_air = true;

        const time_s = (timestamp - flightStart.current) / 1000;

        // Hover altitude oscillations
        t.vertical_speed = Math.sin(time_s * 0.3) * 0.15;
        t.alt_agl = TAKEOFF_ALT + Math.sin(time_s * 0.3) * 0.2;
        t.alt_msl = t.alt_agl + 10;

        // --- Flight mode-dependent velocity control ---
        if (flightMode === 'AUTO' && state.missionStatus === 'RUNNING' && state.mission) {
          // Mission execution
          const missionInput: MissionExecutorInput = {
            mission: state.mission,
            currentWaypointIndex: state.currentWaypointIndex,
            dronePosition: { x: pos.current.x, y: pos.current.y, z: t.alt_agl },
            waypointHoldRemaining: state.waypointHoldRemaining,
            elapsed,
          };
          const missionResult: MissionExecutorOutput = executeMission(missionInput);

          // Apply desired velocity from mission
          vel.current.vx = missionResult.desiredVelocity.x;
          vel.current.vy = missionResult.desiredVelocity.y;
          t.heading = missionResult.desiredHeading;

          // Update mission state
          if (missionResult.nextWaypointIndex !== state.currentWaypointIndex) {
            state.setWaypointIndex(missionResult.nextWaypointIndex);
          }
          state.setWaypointHoldRemaining(missionResult.holdRemaining);

          if (missionResult.missionComplete) {
            state.setMissionStatus('COMPLETE');
          }
          if (missionResult.triggerLanding) {
            state.setSimState('LANDING');
          }
        } else if (flightMode === 'RTL') {
          // Steer toward origin (0,0) at RTL_SPEED
          const dx = -pos.current.x;
          const dy = -pos.current.y;
          const distToOrigin = Math.sqrt(dx * dx + dy * dy);

          if (distToOrigin < RTL_ARRIVAL_RADIUS) {
            // Close enough, trigger landing
            vel.current.vx = 0;
            vel.current.vy = 0;
            state.setSimState('LANDING');
          } else {
            // Steer toward origin
            vel.current.vx = (dx / distToOrigin) * RTL_SPEED;
            vel.current.vy = (dy / distToOrigin) * RTL_SPEED;
            // Heading faces toward origin
            t.heading = ((Math.atan2(dx, dy) * 180) / Math.PI + 360) % 360;
          }
        } else if (flightMode === 'LOITER' || flightMode === 'ALT_HOLD') {
          // Hover in place: velocity decays to zero
          const speed = Math.sqrt(vel.current.vx * vel.current.vx + vel.current.vy * vel.current.vy);
          if (speed > 0.01) {
            const decay = Math.min(VELOCITY_DECAY * elapsed, speed);
            vel.current.vx -= (vel.current.vx / speed) * decay;
            vel.current.vy -= (vel.current.vy / speed) * decay;
          } else {
            vel.current.vx = 0;
            vel.current.vy = 0;
          }
        } else {
          // STABILIZE / MANUAL: gentle drift from wind only (no commanded velocity)
          // Wind force is applied below; no additional commanded velocity here.
        }

        // Apply wind force to velocity
        if (windSpeed > 0) {
          vel.current.vx += windForceX * elapsed;
          vel.current.vy += windForceY * elapsed;
        }

        // Gust effects on velocity
        if (gustIntensity > 0 && Math.random() < 0.02) {
          vel.current.vx += (Math.random() - 0.5) * gustIntensity * 1.5;
          vel.current.vy += (Math.random() - 0.5) * gustIntensity * 1.5;
        }

        // Integrate position
        pos.current.x += vel.current.vx * elapsed;
        pos.current.y += vel.current.vy * elapsed;

        // Update telemetry with position/velocity
        t.pos_x = pos.current.x;
        t.pos_y = pos.current.y;
        t.vel_x = vel.current.vx;
        t.vel_y = vel.current.vy;
        t.ground_speed = Math.sqrt(vel.current.vx * vel.current.vx + vel.current.vy * vel.current.vy);

        // Heading = direction of travel when moving, otherwise keep current
        if (t.ground_speed > 0.3 && flightMode !== 'AUTO' && flightMode !== 'RTL') {
          t.heading = ((Math.atan2(vel.current.vx, vel.current.vy) * 180) / Math.PI + 360) % 360;
        }

        // Attitude from wind and motion
        t.roll = Math.sin(time_s * 0.7) * 1.5 + windSpeed * 0.3;
        t.pitch = Math.cos(time_s * 0.5) * 1.0;

        if (windSpeed > 0) {
          t.roll += windSpeed * 0.5 * Math.sin(time_s * 2);
          t.pitch += windSpeed * 0.3 * Math.cos(time_s * 1.5);
        }
        if (gustIntensity > 0 && Math.random() < 0.02) {
          t.roll += (Math.random() - 0.5) * gustIntensity * 3;
          t.pitch += (Math.random() - 0.5) * gustIntensity * 2;
        }

        t.battery_current = HOVER_CURRENT + windSpeed * 0.5 + Math.random() * 0.5;

        // Failsafe handling
        if (failsafeActive === 'gps_loss') {
          t.gps_satellites = Math.max(0, t.gps_satellites - 2 * elapsed);
          t.gps_hdop = Math.min(10, t.gps_hdop + 0.5 * elapsed);
          if (t.gps_satellites <= 0) t.gps_fix_type = 'None';
          else if (t.gps_satellites < 4) t.gps_fix_type = '2D';
        }
        if (failsafeActive === 'rc_loss') {
          t.rssi = Math.max(0, t.rssi - 20 * elapsed);
        }
        if (failsafeActive === 'low_battery') {
          t.battery_remaining_pct = Math.max(0, t.battery_remaining_pct - 5 * elapsed);
          t.battery_current = HOVER_CURRENT * 1.5;
        }
        if (failsafeActive === 'geofence') {
          t.ground_speed = 5;
          t.heading = (t.heading + 3) % 360;
        }
      }

      if (simState === 'LANDING') {
        t.armed = true;
        t.in_air = t.alt_agl > 0.1;
        t.vertical_speed = -DESCENT_RATE;
        t.alt_agl = Math.max(0, t.alt_agl - DESCENT_RATE * elapsed);
        t.alt_msl = t.alt_agl + 10;
        t.battery_current = HOVER_CURRENT * 0.8;
        t.pitch = 1;

        // Decelerate horizontal velocity during landing
        const hSpeed = Math.sqrt(vel.current.vx * vel.current.vx + vel.current.vy * vel.current.vy);
        if (hSpeed > 0.01) {
          const decel = Math.min(VELOCITY_DECAY * elapsed, hSpeed);
          vel.current.vx -= (vel.current.vx / hSpeed) * decel;
          vel.current.vy -= (vel.current.vy / hSpeed) * decel;
        } else {
          vel.current.vx = 0;
          vel.current.vy = 0;
        }

        // Integrate position during landing (coasting to stop)
        pos.current.x += vel.current.vx * elapsed;
        pos.current.y += vel.current.vy * elapsed;

        t.pos_x = pos.current.x;
        t.pos_y = pos.current.y;
        t.vel_x = vel.current.vx;
        t.vel_y = vel.current.vy;
        t.ground_speed = Math.sqrt(vel.current.vx * vel.current.vx + vel.current.vy * vel.current.vy);

        if (t.alt_agl <= 0.1) {
          t.alt_agl = 0;
          t.alt_msl = 10;
          t.vertical_speed = 0;
          t.ground_speed = 0;
          t.pitch = 0;
          t.roll = 0;
          t.armed = false;
          t.in_air = false;
          t.battery_current = 0;
          vel.current.vx = 0;
          vel.current.vy = 0;
          t.vel_x = 0;
          t.vel_y = 0;
          flightStart.current = 0;
          state.setSimState('LANDED');
        }
      }

      if (simState === 'LANDED') {
        t.armed = false;
        t.battery_current = 0.5;
        t.vertical_speed = 0;
        t.ground_speed = 0;
        t.pos_x = pos.current.x;
        t.pos_y = pos.current.y;
        t.vel_x = 0;
        t.vel_y = 0;
      }

      // --- Battery drain (continuous when armed) ---
      if (t.armed) {
        mah_used.current += (t.battery_current * elapsed / 3.6);
        t.battery_remaining_pct = Math.max(0,
          (1 - mah_used.current / BATTERY_CAPACITY_MAH) * 100
        );
        t.battery_voltage = BATTERY_EMPTY_V +
          (BATTERY_FULL_V - BATTERY_EMPTY_V) * (t.battery_remaining_pct / 100);

        if (t.battery_remaining_pct <= 10 && simState === 'FLYING') {
          state.setSimState('LANDING');
          state.triggerFailsafe('low_battery');
        }
      }

      // --- Flight timer ---
      if (t.in_air) {
        t.flight_time_s += elapsed;
      }

      // --- GPS jitter ---
      if (t.gps_fix_type !== 'None') {
        t.lat += (Math.random() - 0.5) * 0.000001;
        t.lon += (Math.random() - 0.5) * 0.000001;
      }

      // --- Trail recording ---
      if (t.in_air) {
        trailTimer.current += elapsed;
        if (trailTimer.current >= TRAIL_INTERVAL) {
          trailTimer.current -= TRAIL_INTERVAL;
          state.addTrailPoint({ x: pos.current.x, y: pos.current.y, z: t.alt_agl });
        }
      }

      state.updateTelemetry(t);
      rafId.current = requestAnimationFrame(tick);
    }

    rafId.current = requestAnimationFrame(tick);

    return () => {
      if (rafId.current) cancelAnimationFrame(rafId.current);
      lastTime.current = 0;
      flightStart.current = 0;
      mah_used.current = 0;
      pos.current = { x: 0, y: 0 };
      vel.current = { vx: 0, vy: 0 };
      trailTimer.current = 0;
    };
  }, []);
}
