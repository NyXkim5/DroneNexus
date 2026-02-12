import { create } from 'zustand';

export type FlightMode = 'MANUAL' | 'STABILIZE' | 'ALT_HOLD' | 'LOITER' | 'AUTO' | 'RTL' | 'LAND';
export type SimState = 'IDLE' | 'ARMED' | 'TAKING_OFF' | 'FLYING' | 'LANDING' | 'LANDED' | 'EMERGENCY';
export type MissionStatus = 'NONE' | 'LOADED' | 'RUNNING' | 'PAUSED' | 'COMPLETE';

interface PIDGains {
  p: number;
  i: number;
  d: number;
}

export interface MissionWaypointENU {
  id: number;
  name: string;
  type: 'takeoff' | 'waypoint' | 'land' | 'orbit' | 'survey_start' | 'survey_end';
  position: { x_m: number; y_m: number; z_m: number };
  speed_ms: number;
  heading_deg: number | null;
  hold_time_s: number;
  acceptance_radius_m: number;
}

export interface MissionData {
  name: string;
  description: string;
  waypoints: MissionWaypointENU[];
  defaults: {
    altitude_m: number;
    speed_ms: number;
    acceptance_radius_m: number;
  };
  safety: {
    geofence_radius_m: number;
    max_altitude_m: number;
  };
}

export interface SimTelemetry {
  lat: number;
  lon: number;
  alt_msl: number;
  alt_agl: number;
  pos_x: number;
  pos_y: number;
  vel_x: number;
  vel_y: number;
  roll: number;
  pitch: number;
  yaw: number;
  heading: number;
  ground_speed: number;
  vertical_speed: number;
  battery_voltage: number;
  battery_current: number;
  battery_remaining_pct: number;
  gps_satellites: number;
  gps_hdop: number;
  gps_fix_type: string;
  rssi: number;
  armed: boolean;
  in_air: boolean;
  flight_time_s: number;
}

interface SimulationState {
  running: boolean;
  simState: SimState;
  flightMode: FlightMode;
  telemetry: SimTelemetry;
  pidGains: {
    roll: PIDGains;
    pitch: PIDGains;
    yaw: PIDGains;
    altitude: PIDGains;
  };
  windSpeed: number;
  windDirection: number;
  gustIntensity: number;
  failsafeActive: string | null;

  // Mission state
  mission: MissionData | null;
  currentWaypointIndex: number;
  missionStatus: MissionStatus;
  waypointHoldRemaining: number;
  flightTrail: Array<{ x: number; y: number; z: number }>;

  // Actions
  startSim: () => void;
  stopSim: () => void;
  setFlightMode: (mode: FlightMode) => void;
  setSimState: (state: SimState) => void;
  updateTelemetry: (data: Partial<SimTelemetry>) => void;
  updatePID: (axis: string, gains: PIDGains) => void;
  setWind: (speed: number, direction: number, gust: number) => void;
  triggerFailsafe: (type: string | null) => void;
  loadMission: (mission: MissionData) => void;
  clearMission: () => void;
  setWaypointIndex: (index: number) => void;
  setMissionStatus: (status: MissionStatus) => void;
  setWaypointHoldRemaining: (time: number) => void;
  addTrailPoint: (point: { x: number; y: number; z: number }) => void;
  clearTrail: () => void;
}

const defaultTelemetry: SimTelemetry = {
  lat: 33.6405,
  lon: -117.8443,
  alt_msl: 0,
  alt_agl: 0,
  pos_x: 0,
  pos_y: 0,
  vel_x: 0,
  vel_y: 0,
  roll: 0,
  pitch: 0,
  yaw: 0,
  heading: 0,
  ground_speed: 0,
  vertical_speed: 0,
  battery_voltage: 25.2,
  battery_current: 0,
  battery_remaining_pct: 100,
  gps_satellites: 14,
  gps_hdop: 0.8,
  gps_fix_type: '3D',
  rssi: 95,
  armed: false,
  in_air: false,
  flight_time_s: 0,
};

export const useSimStore = create<SimulationState>((set) => ({
  running: false,
  simState: 'IDLE',
  flightMode: 'STABILIZE',
  telemetry: { ...defaultTelemetry },
  pidGains: {
    roll: { p: 45, i: 80, d: 25 },
    pitch: { p: 45, i: 80, d: 25 },
    yaw: { p: 40, i: 50, d: 0 },
    altitude: { p: 50, i: 30, d: 15 },
  },
  windSpeed: 0,
  windDirection: 0,
  gustIntensity: 0,
  failsafeActive: null,
  mission: null,
  currentWaypointIndex: 0,
  missionStatus: 'NONE',
  waypointHoldRemaining: 0,
  flightTrail: [],

  startSim: () => set({ running: true }),
  stopSim: () => set({
    running: false, simState: 'IDLE', telemetry: { ...defaultTelemetry },
    mission: null, currentWaypointIndex: 0, missionStatus: 'NONE',
    waypointHoldRemaining: 0, flightTrail: [],
  }),
  setFlightMode: (mode) => set({ flightMode: mode }),
  setSimState: (state) => set({ simState: state }),
  updateTelemetry: (data) => set((s) => ({
    telemetry: { ...s.telemetry, ...data },
  })),
  updatePID: (axis, gains) => set((s) => ({
    pidGains: { ...s.pidGains, [axis]: gains },
  })),
  setWind: (speed, direction, gust) => set({ windSpeed: speed, windDirection: direction, gustIntensity: gust }),
  triggerFailsafe: (type) => set({ failsafeActive: type }),
  loadMission: (mission) => set({ mission, currentWaypointIndex: 0, missionStatus: 'LOADED', waypointHoldRemaining: 0 }),
  clearMission: () => set({ mission: null, currentWaypointIndex: 0, missionStatus: 'NONE', waypointHoldRemaining: 0, flightTrail: [] }),
  setWaypointIndex: (index) => set({ currentWaypointIndex: index }),
  setMissionStatus: (status) => set({ missionStatus: status }),
  setWaypointHoldRemaining: (time) => set({ waypointHoldRemaining: time }),
  addTrailPoint: (point) => set((s) => ({
    flightTrail: s.flightTrail.length > 2000
      ? [...s.flightTrail.slice(-1500), point]
      : [...s.flightTrail, point],
  })),
  clearTrail: () => set({ flightTrail: [] }),
}));
