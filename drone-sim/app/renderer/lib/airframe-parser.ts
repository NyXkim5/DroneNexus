/**
 * YAML airframe parser and validator.
 * Converts between YAML configs and TypeScript types with validation.
 */
import type { AirframeConfig, SensorNoiseConfig, MissionConfig } from '@/types/airframe';
import { computeLimits } from './physics';
import { runCompatibilityCheck } from './compatibility';

/**
 * Validate an airframe config has all required fields.
 * Returns list of missing/invalid field paths.
 */
export function validateAirframe(config: unknown): string[] {
  const errors: string[] = [];
  const c = config as Record<string, unknown>;

  const requiredTopLevel = ['name', 'frame', 'motors', 'propellers', 'battery', 'electronics', 'sensors'];
  for (const key of requiredTopLevel) {
    if (!(key in c)) errors.push(`Missing required field: ${key}`);
  }

  if (c.frame && typeof c.frame === 'object') {
    const frame = c.frame as Record<string, unknown>;
    for (const f of ['type', 'layout', 'weight_g', 'wheelbase_mm']) {
      if (!(f in frame)) errors.push(`Missing frame.${f}`);
    }
  }

  if (c.motors && typeof c.motors === 'object') {
    const motors = c.motors as Record<string, unknown>;
    for (const f of ['count', 'kv', 'max_thrust_g', 'weight_g']) {
      if (!(f in motors)) errors.push(`Missing motors.${f}`);
    }
  }

  if (c.battery && typeof c.battery === 'object') {
    const batt = c.battery as Record<string, unknown>;
    for (const f of ['cells', 'capacity_mah', 'weight_g', 'voltage_nominal']) {
      if (!(f in batt)) errors.push(`Missing battery.${f}`);
    }
  }

  return errors;
}

/**
 * Load and process an airframe — recalculates computed limits.
 */
export function processAirframe(raw: AirframeConfig): AirframeConfig {
  // Ensure all sections exist with defaults
  const config = applyDefaults(raw);

  // Recalculate computed limits
  config.computed = computeLimits(config);

  // Add compatibility warnings
  const compat = runCompatibilityCheck(config);
  config.computed.compatibility_warnings = [
    ...compat.errors.map(e => `ERROR: ${e.message}`),
    ...compat.warnings.map(w => `WARNING: ${w.message}`),
  ];

  return config;
}

/**
 * Apply default values for missing optional fields.
 */
function applyDefaults(config: AirframeConfig): AirframeConfig {
  return {
    ...config,
    thermal: config.thermal ?? {
      motor_max_temp_c: 80,
      esc_max_temp_c: 85,
      fc_max_temp_c: 75,
      ambient_operating_range_c: [-10, 50],
      cooling: 'passive',
      heat_soak_time_s: 180,
      thermal_throttle_pct: 90,
    },
    electrical: config.electrical ?? {
      power_distribution: { type: 'integrated', model: 'Unknown' },
      total_max_current_a: config.electronics.esc.max_current_a * config.motors.count,
      avg_hover_current_a: 10,
      avg_cruise_current_a: 20,
      peak_burst_current_a: config.electronics.esc.max_current_a * config.motors.count * 0.8,
      wire_gauge: { battery_to_esc: '14AWG', esc_to_motor: '20AWG', signal_wires: '28AWG' },
      voltage_regulator: { output_5v_a: 2, output_9v_a: 2 },
      connector_types: { battery: 'XT60', motor: '3.5mm bullet', signal: 'JST-SH' },
    },
    compatibility: config.compatibility ?? {
      fc_stack_size: '30.5x30.5',
      esc_protocol_supported: ['DShot300', 'DShot600', 'PWM'],
      motor_shaft_mm: 5,
      prop_clearance_mm: 8,
      battery_fit: { max_length_mm: 80, max_width_mm: 40, max_height_mm: 30, strap_type: 'rubber' },
      voltage_compatibility: {
        min_cells: 4, max_cells: 6,
        motors_rated_voltage: config.battery.voltage_full,
        esc_rated_voltage: config.battery.voltage_full + 1,
        fc_input_range: [7, 36],
      },
      firmware_support: ['Betaflight', 'ArduPilot'],
    },
    mechanical: config.mechanical ?? {
      center_of_gravity_mm: [0, 0, -10],
      moment_of_inertia: { roll: 0.005, pitch: 0.005, yaw: 0.008 },
      vibration: { dominant_frequency_hz: 200, damping: 'soft_mount', fc_gyro_filtering_hz: 150 },
      mounting: {
        fc_pattern_mm: '30.5x30.5',
        fc_standoff_height_mm: 5,
        motor_mount_pattern: '16x19',
        screw_sizes: { frame: 'M3x8', motor: 'M3x6', fc_stack: 'M3x25', camera: 'M2x5' },
        antenna_mount: 'SMA',
      },
      build_notes: [],
    },
    computed: config.computed ?? {
      all_up_weight_g: 0,
      thrust_to_weight: 0,
      max_speed_ms: 0,
      hover_throttle_pct: 0,
      max_flight_time_min: 0,
      max_payload_g: 0,
      max_continuous_current_a: 0,
      thermal_flight_limit_min: 0,
      power_at_hover_w: 0,
      power_at_max_w: 0,
      energy_capacity_wh: 0,
      prop_tip_speed_ms: 0,
      max_angular_rate_dps: 0,
      compatibility_warnings: [],
    },
  };
}

/**
 * Create a blank airframe template.
 */
export function createBlankAirframe(): AirframeConfig {
  return processAirframe({
    name: 'New Drone',
    description: 'Custom drone build',
    category: 'FPV',
    icon: 'quad-x',
    frame: { type: 'quadcopter', layout: 'X', arm_length_mm: 110, weight_g: 300, material: 'carbon_fiber', wheelbase_mm: 220 },
    motors: { count: 4, model: 'Generic 2306', kv: 2400, max_thrust_g: 1300, weight_g: 33, idle_throttle_pct: 5, motor_positions: [[110,110],[-110,110],[-110,-110],[110,-110]] },
    propellers: { size_inch: 5, pitch_inch: 3, blades: 3, model: 'Generic 5x3x3', weight_g: 4, max_rpm: 28000 },
    battery: { cells: 6, voltage_nominal: 22.2, voltage_full: 25.2, voltage_empty: 19.8, capacity_mah: 1300, weight_g: 200, discharge_rate_c: 100, connector: 'XT60' },
    electronics: {
      flight_controller: { model: 'Generic F405', firmware: 'Betaflight', processor: 'STM32F405', weight_g: 8 },
      esc: { model: 'Generic 4in1 55A', protocol: 'DShot600', max_current_a: 55, weight_g: 12 },
      receiver: { model: 'Generic ELRS', protocol: 'ELRS', frequency: '2.4GHz', weight_g: 2 },
      vtx: { model: 'Generic VTX', max_power_mw: 800, frequency_band: '5.8GHz', weight_g: 6 },
    },
    sensors: {
      imu: { model: 'BMI270', gyro_range_dps: 2000, accel_range_g: 16, sample_rate_hz: 8000 },
      barometer: { model: 'BMP388', resolution_cm: 50 },
      magnetometer: { present: false },
      gps: { present: false },
      lidar: { present: false },
      camera: { model: 'Generic Camera', resolution: '1080p', fov_deg: 120, weight_g: 30 },
      optical_flow: { present: false },
    },
  } as AirframeConfig);
}
