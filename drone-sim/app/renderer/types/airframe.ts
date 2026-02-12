// Full TypeScript types matching the airframe YAML schema

export interface AirframeConfig {
  name: string;
  description: string;
  category: 'FPV' | 'Commercial' | 'Industrial' | 'Agricultural' | 'Racing';
  icon: string;

  frame: FrameConfig;
  motors: MotorConfig;
  propellers: PropellerConfig;
  battery: BatteryConfig;
  electronics: ElectronicsConfig;
  sensors: SensorSuite;
  thermal: ThermalConfig;
  electrical: ElectricalConfig;
  compatibility: CompatibilityConfig;
  mechanical: MechanicalConfig;
  computed: ComputedLimits;
}

export interface FrameConfig {
  type: 'quadcopter' | 'hexacopter' | 'octocopter' | 'fixed_wing' | 'vtol';
  layout: 'X' | 'H' | '+' | 'deadcat' | 'Y6' | 'coaxial';
  arm_length_mm: number;
  weight_g: number;
  material: string;
  wheelbase_mm: number;
}

export interface MotorConfig {
  count: number;
  model: string;
  kv: number;
  max_thrust_g: number;
  weight_g: number;
  idle_throttle_pct: number;
  motor_positions: [number, number][];
}

export interface PropellerConfig {
  size_inch: number;
  pitch_inch: number;
  blades: number;
  model: string;
  weight_g: number;
  max_rpm: number;
}

export interface BatteryConfig {
  cells: number;
  voltage_nominal: number;
  voltage_full: number;
  voltage_empty: number;
  capacity_mah: number;
  weight_g: number;
  discharge_rate_c: number;
  connector: string;
}

export interface FlightControllerConfig {
  model: string;
  firmware: 'Betaflight' | 'ArduPilot' | 'PX4' | 'iNav';
  processor: string;
  weight_g: number;
}

export interface ESCConfig {
  model: string;
  protocol: string;
  max_current_a: number;
  weight_g: number;
}

export interface ReceiverConfig {
  model: string;
  protocol: string;
  frequency: string;
  weight_g: number;
}

export interface VTXConfig {
  model: string;
  max_power_mw: number;
  frequency_band: string;
  weight_g: number;
}

export interface ElectronicsConfig {
  flight_controller: FlightControllerConfig;
  esc: ESCConfig;
  receiver: ReceiverConfig;
  vtx: VTXConfig;
}

export interface IMUSensor {
  model: string;
  gyro_range_dps: number;
  accel_range_g: number;
  sample_rate_hz: number;
}

export interface BarometerSensor {
  model: string;
  resolution_cm: number;
}

export interface GPSSensor {
  present: boolean;
  model?: string;
  update_rate_hz?: number;
  accuracy_m?: number;
}

export interface LiDARSensor {
  present: boolean;
  model?: string;
  range_m?: number;
  points_per_sec?: number;
  type?: '360' | 'downward' | 'forward';
}

export interface CameraSensor {
  model: string;
  resolution: string;
  fov_deg: number;
  weight_g: number;
}

export interface DepthCameraSensor {
  present: boolean;
  model?: string;
  range_m?: number;
  resolution?: string;
  type?: 'stereo' | 'tof';
}

export interface OpticalFlowSensor {
  present: boolean;
  model?: string;
  range_m?: number;
}

export interface MagnetometerSensor {
  present: boolean;
  model?: string;
}

export interface SensorSuite {
  imu: IMUSensor;
  barometer: BarometerSensor;
  magnetometer: MagnetometerSensor;
  gps: GPSSensor;
  lidar: LiDARSensor;
  camera: CameraSensor;
  depth_camera?: DepthCameraSensor;
  optical_flow: OpticalFlowSensor;
}

export interface ThermalConfig {
  motor_max_temp_c: number;
  esc_max_temp_c: number;
  fc_max_temp_c: number;
  ambient_operating_range_c: [number, number];
  cooling: 'passive' | 'active_fan' | 'ducted';
  heat_soak_time_s: number;
  thermal_throttle_pct: number;
}

export interface WireGauge {
  battery_to_esc: string;
  esc_to_motor: string;
  signal_wires: string;
}

export interface VoltageRegulator {
  output_5v_a: number;
  output_9v_a: number;
}

export interface ConnectorTypes {
  battery: string;
  motor: string;
  signal: string;
}

export interface ElectricalConfig {
  power_distribution: { type: string; model: string };
  total_max_current_a: number;
  avg_hover_current_a: number;
  avg_cruise_current_a: number;
  peak_burst_current_a: number;
  wire_gauge: WireGauge;
  voltage_regulator: VoltageRegulator;
  connector_types: ConnectorTypes;
}

export interface BatteryFit {
  max_length_mm: number;
  max_width_mm: number;
  max_height_mm: number;
  strap_type: string;
}

export interface VoltageCompatibility {
  min_cells: number;
  max_cells: number;
  motors_rated_voltage: number;
  esc_rated_voltage: number;
  fc_input_range: [number, number];
}

export interface CompatibilityConfig {
  fc_stack_size: string;
  esc_protocol_supported: string[];
  motor_shaft_mm: number;
  prop_clearance_mm: number;
  battery_fit: BatteryFit;
  voltage_compatibility: VoltageCompatibility;
  firmware_support: string[];
}

export interface MomentOfInertia {
  roll: number;
  pitch: number;
  yaw: number;
}

export interface Vibration {
  dominant_frequency_hz: number;
  damping: 'hard_mount' | 'soft_mount' | 'foam_tape';
  fc_gyro_filtering_hz: number;
}

export interface Mounting {
  fc_pattern_mm: string;
  fc_standoff_height_mm: number;
  motor_mount_pattern: string;
  screw_sizes: Record<string, string>;
  antenna_mount: string;
}

export interface MechanicalConfig {
  center_of_gravity_mm: [number, number, number];
  moment_of_inertia: MomentOfInertia;
  vibration: Vibration;
  mounting: Mounting;
  build_notes: string[];
}

export interface ComputedLimits {
  all_up_weight_g: number;
  thrust_to_weight: number;
  max_speed_ms: number;
  hover_throttle_pct: number;
  max_flight_time_min: number;
  max_payload_g: number;
  max_continuous_current_a: number;
  thermal_flight_limit_min: number;
  power_at_hover_w: number;
  power_at_max_w: number;
  energy_capacity_wh: number;
  prop_tip_speed_ms: number;
  max_angular_rate_dps: number;
  compatibility_warnings: string[];
}

// Sensor noise config types
export interface NoisePreset {
  name: string;
  parameters: Record<string, number>;
}

export interface SensorNoiseConfig {
  sensor_type: string;
  description: string;
  unit: string;
  presets: {
    perfect: Record<string, number>;
    typical: Record<string, number>;
    noisy: Record<string, number>;
    failing: Record<string, number>;
  };
}

// Mission types
export interface MissionWaypoint {
  lat: number;
  lon: number;
  alt: number;
  speed?: number;
  type: 'waypoint' | 'loiter' | 'land' | 'takeoff';
  loiter_time_s?: number;
  radius_m?: number;
}

export interface MissionConfig {
  name: string;
  description: string;
  home: { lat: number; lon: number; alt: number };
  waypoints: MissionWaypoint[];
  settings: {
    default_speed_ms: number;
    default_altitude_m: number;
    rtl_altitude_m: number;
  };
}

// Electron API type declaration
declare global {
  interface Window {
    api: {
      loadAirframe: (filename: string) => Promise<AirframeConfig>;
      saveAirframe: (filename: string, data: AirframeConfig) => Promise<boolean>;
      listAirframes: () => Promise<string[]>;
      loadSensorConfig: (filename: string) => Promise<SensorNoiseConfig>;
      listSensorConfigs: () => Promise<string[]>;
      loadMission: (filename: string) => Promise<MissionConfig>;
      listMissions: () => Promise<string[]>;
      exportAirframe: (data: AirframeConfig) => Promise<string | null>;
      importAirframe: () => Promise<AirframeConfig | null>;
    };
  }
}
