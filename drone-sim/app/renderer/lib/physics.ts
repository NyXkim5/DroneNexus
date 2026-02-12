/**
 * Physics engine for drone simulation.
 * Computes thrust, drag, battery drain, flight time, and dynamic limits
 * from airframe YAML configs.
 */
import type { AirframeConfig, ComputedLimits } from '@/types/airframe';

const GRAVITY = 9.81; // m/s^2
const AIR_DENSITY = 1.225; // kg/m^3 at sea level

/**
 * Calculate all-up weight from component weights.
 */
export function calculateAUW(config: AirframeConfig): number {
  const { frame, motors, propellers, battery, electronics, sensors } = config;

  let weight = frame.weight_g;
  weight += motors.weight_g * motors.count;
  weight += propellers.weight_g * motors.count;
  weight += battery.weight_g;
  weight += electronics.flight_controller.weight_g;
  weight += electronics.esc.weight_g;
  weight += electronics.receiver.weight_g;
  weight += electronics.vtx.weight_g;
  weight += sensors.camera.weight_g;

  // Estimate ~20g for wiring, standoffs, screws, antenna
  weight += 20;

  return Math.round(weight);
}

/**
 * Calculate thrust-to-weight ratio.
 */
export function calculateThrustToWeight(config: AirframeConfig): number {
  const totalThrust = config.motors.max_thrust_g * config.motors.count;
  const auw = calculateAUW(config);
  return Math.round((totalThrust / auw) * 100) / 100;
}

/**
 * Estimate hover throttle percentage from T:W ratio.
 * Hover throttle ~= 1 / T:W (as a percentage).
 */
export function calculateHoverThrottle(config: AirframeConfig): number {
  const tw = calculateThrustToWeight(config);
  return Math.round((1 / tw) * 100);
}

/**
 * Estimate max speed from thrust-to-weight and drag coefficient.
 * Simplified model: v_max = sqrt(2 * excess_thrust / (rho * Cd * A))
 */
export function calculateMaxSpeed(config: AirframeConfig): number {
  const auw_kg = calculateAUW(config) / 1000;
  const totalThrust_N = (config.motors.max_thrust_g * config.motors.count / 1000) * GRAVITY;
  const weight_N = auw_kg * GRAVITY;
  const excessThrust = totalThrust_N - weight_N;

  // Estimate frontal area from frame dimensions
  const frameSize_m = config.frame.wheelbase_mm / 1000;
  const frontalArea = frameSize_m * 0.05; // rough estimate
  const Cd = 1.2; // bluff body drag coefficient

  if (excessThrust <= 0) return 0;
  const vMax = Math.sqrt((2 * excessThrust) / (AIR_DENSITY * Cd * frontalArea));
  return Math.round(vMax);
}

/**
 * Estimate max flight time based on battery capacity and average current draw.
 * Uses a simplified model based on hover current.
 */
export function calculateFlightTime(config: AirframeConfig): number {
  const hoverThrottle = calculateHoverThrottle(config) / 100;
  // Average current at hover per motor (rough estimate from throttle percentage)
  const currentPerMotor = config.electrical.avg_hover_current_a / config.motors.count;
  const totalHoverCurrent = currentPerMotor * config.motors.count;

  // Use 80% of battery capacity (don't discharge below 20%)
  const usableCapacity_ah = (config.battery.capacity_mah * 0.8) / 1000;
  const flightTime_h = usableCapacity_ah / totalHoverCurrent;
  return Math.round(flightTime_h * 60 * 10) / 10; // minutes, 1 decimal
}

/**
 * Estimate max payload before T:W drops below 2:1 (minimum safe).
 */
export function calculateMaxPayload(config: AirframeConfig): number {
  const totalThrust = config.motors.max_thrust_g * config.motors.count;
  const auw = calculateAUW(config);
  // T:W = totalThrust / (auw + payload) >= 2
  // payload <= totalThrust/2 - auw
  const maxPayload = totalThrust / 2 - auw;
  return Math.max(0, Math.round(maxPayload));
}

/**
 * Calculate battery energy capacity in Wh.
 */
export function calculateEnergyCapacity(config: AirframeConfig): number {
  return Math.round((config.battery.voltage_nominal * config.battery.capacity_mah / 1000) * 10) / 10;
}

/**
 * Calculate power consumption at hover and max.
 */
export function calculatePower(config: AirframeConfig): { hover: number; max: number } {
  const hover = Math.round(config.battery.voltage_nominal * config.electrical.avg_hover_current_a);
  const max = Math.round(config.battery.voltage_nominal * config.electrical.peak_burst_current_a);
  return { hover, max };
}

/**
 * Calculate propeller tip speed.
 * tip_speed = RPM * pi * diameter / 60
 */
export function calculatePropTipSpeed(config: AirframeConfig): number {
  const diameter_m = config.propellers.size_inch * 0.0254;
  const tipSpeed = (config.propellers.max_rpm * Math.PI * diameter_m) / 60;
  return Math.round(tipSpeed);
}

/**
 * Estimate max angular rate from T:W ratio and moment of inertia.
 * Simplified: angular_rate ~= (T:W - 1) * 400 deg/s (empirical scaling)
 */
export function calculateMaxAngularRate(config: AirframeConfig): number {
  const tw = calculateThrustToWeight(config);
  return Math.round((tw - 1) * 400);
}

/**
 * Estimate thermal flight time limit based on motor heat soak.
 */
export function calculateThermalLimit(config: AirframeConfig): number {
  // Motors heat up proportional to current draw
  // At hover, thermal limit is typically much longer than battery life
  const hoverFraction = calculateHoverThrottle(config) / 100;
  const thermalMargin = config.thermal.thermal_throttle_pct / 100;
  if (hoverFraction >= thermalMargin) return 0; // Already at thermal limit at hover

  // Rough model: time to thermal limit at hover = heat_soak_time / (hover_fraction / thermal_fraction)
  const ratio = hoverFraction / thermalMargin;
  const thermalTime = config.thermal.heat_soak_time_s / ratio / 60;
  return Math.round(thermalTime * 10) / 10;
}

/**
 * Check compatibility and generate warnings.
 */
export function checkCompatibility(config: AirframeConfig): string[] {
  const warnings: string[] = [];

  // Voltage compatibility
  const batteryVoltage = config.battery.voltage_full;
  const escRated = config.compatibility.voltage_compatibility.esc_rated_voltage;
  if (batteryVoltage > escRated) {
    warnings.push(`Battery voltage (${batteryVoltage}V) exceeds ESC rating (${escRated}V)`);
  }

  const fcRange = config.compatibility.voltage_compatibility.fc_input_range;
  if (batteryVoltage < fcRange[0] || batteryVoltage > fcRange[1]) {
    warnings.push(`Battery voltage outside FC input range (${fcRange[0]}-${fcRange[1]}V)`);
  }

  // Motor shaft / prop compatibility
  const motorShaft = config.compatibility.motor_shaft_mm;
  if (motorShaft > 5 && config.propellers.size_inch <= 5) {
    warnings.push('Motor shaft may be too large for prop adapter');
  }

  // T:W ratio checks
  const tw = calculateThrustToWeight(config);
  if (tw < 2) {
    warnings.push(`Thrust-to-weight ratio (${tw}:1) is below minimum safe (2:1)`);
  } else if (tw < 3) {
    warnings.push(`Thrust-to-weight ratio (${tw}:1) is marginal — limited maneuverability`);
  }

  // Prop clearance
  if (config.compatibility.prop_clearance_mm < 5) {
    warnings.push(`Prop clearance (${config.compatibility.prop_clearance_mm}mm) is very tight`);
  }

  // ESC current per motor
  const maxThrustCurrent = config.electrical.peak_burst_current_a / config.motors.count;
  if (maxThrustCurrent > config.electronics.esc.max_current_a * 0.9) {
    warnings.push('Peak current may exceed ESC rating — risk of ESC burnout');
  }

  // Cell count check
  const compat = config.compatibility.voltage_compatibility;
  if (config.battery.cells < compat.min_cells || config.battery.cells > compat.max_cells) {
    warnings.push(`Battery cell count (${config.battery.cells}S) outside compatible range (${compat.min_cells}-${compat.max_cells}S)`);
  }

  // Tip speed warning (approaching speed of sound ~343 m/s)
  const tipSpeed = calculatePropTipSpeed(config);
  if (tipSpeed > 300) {
    warnings.push(`Prop tip speed (${tipSpeed} m/s) approaching speed of sound — efficiency loss`);
  }

  return warnings;
}

/**
 * Compute all derived limits from an airframe config.
 */
export function computeLimits(config: AirframeConfig): ComputedLimits {
  const power = calculatePower(config);
  return {
    all_up_weight_g: calculateAUW(config),
    thrust_to_weight: calculateThrustToWeight(config),
    max_speed_ms: calculateMaxSpeed(config),
    hover_throttle_pct: calculateHoverThrottle(config),
    max_flight_time_min: calculateFlightTime(config),
    max_payload_g: calculateMaxPayload(config),
    max_continuous_current_a: config.electrical.avg_cruise_current_a,
    thermal_flight_limit_min: calculateThermalLimit(config),
    power_at_hover_w: power.hover,
    power_at_max_w: power.max,
    energy_capacity_wh: calculateEnergyCapacity(config),
    prop_tip_speed_ms: calculatePropTipSpeed(config),
    max_angular_rate_dps: calculateMaxAngularRate(config),
    compatibility_warnings: checkCompatibility(config),
  };
}

/**
 * Simulate battery drain over time.
 * Returns voltage at a given time (seconds) into flight.
 */
export function simulateBatteryVoltage(config: AirframeConfig, elapsed_s: number, throttle_pct: number): number {
  const capacityAh = config.battery.capacity_mah / 1000;
  const currentDraw = config.electrical.avg_hover_current_a * (throttle_pct / calculateHoverThrottle(config));
  const consumed_ah = (currentDraw * elapsed_s) / 3600;
  const remainingFraction = Math.max(0, 1 - consumed_ah / capacityAh);

  // Battery discharge curve (LiPo approximation)
  const voltageRange = config.battery.voltage_full - config.battery.voltage_empty;
  // LiPo discharge is roughly: flat in the middle, drops at start and end
  const dischargeCurve = 1 - Math.pow(1 - remainingFraction, 0.3) * 0.1 - (1 - remainingFraction) * 0.9;
  return config.battery.voltage_empty + voltageRange * dischargeCurve;
}

/**
 * Motor mixing matrix for different frame configurations.
 * Returns throttle multipliers for each motor given roll/pitch/yaw inputs.
 * Convention: input range [-1, 1], output range [0, 1]
 */
export function getMotorMixingMatrix(layout: string, motorCount: number): number[][] {
  // Each row: [throttle, roll, pitch, yaw] multipliers
  // Positive roll = right side up
  // Positive pitch = nose up
  // Positive yaw = clockwise from top

  switch (layout) {
    case 'X':
      if (motorCount === 4) {
        return [
          // Motor positions (top view): FR, FL, BL, BR
          [1, -1, 1, -1],  // Front Right (CW)
          [1, 1, 1, 1],    // Front Left (CCW)
          [1, 1, -1, -1],  // Back Left (CW)
          [1, -1, -1, 1],  // Back Right (CCW)
        ];
      }
      break;
    case 'H':
      if (motorCount === 4) {
        return [
          [1, -1, 1, -1],
          [1, 1, 1, 1],
          [1, 1, -1, -1],
          [1, -1, -1, 1],
        ];
      }
      break;
    case '+':
      if (motorCount === 4) {
        return [
          [1, 0, 1, -1],   // Front (CW)
          [1, -1, 0, 1],   // Right (CCW)
          [1, 0, -1, -1],  // Back (CW)
          [1, 1, 0, 1],    // Left (CCW)
        ];
      }
      break;
  }

  // Hexacopter X layout
  if (motorCount === 6) {
    return [
      [1, -0.5, 1, -1],    // Front Right
      [1, 0.5, 1, 1],      // Front Left
      [1, 1, 0, -1],       // Left
      [1, 0.5, -1, 1],     // Back Left
      [1, -0.5, -1, -1],   // Back Right
      [1, -1, 0, 1],       // Right
    ];
  }

  // Octocopter X layout
  if (motorCount === 8) {
    const a = Math.sin(Math.PI / 8); // ~0.383
    const b = Math.cos(Math.PI / 8); // ~0.924
    return [
      [1, -a, b, -1],
      [1, a, b, 1],
      [1, b, a, -1],
      [1, b, -a, 1],
      [1, a, -b, -1],
      [1, -a, -b, 1],
      [1, -b, -a, -1],
      [1, -b, a, 1],
    ];
  }

  // Default quad X
  return [
    [1, -1, 1, -1],
    [1, 1, 1, 1],
    [1, 1, -1, -1],
    [1, -1, -1, 1],
  ];
}

/**
 * Simple PID controller for simulation.
 */
export class PIDController {
  private integral = 0;
  private lastError = 0;
  private lastTime = 0;

  constructor(
    public kp: number,
    public ki: number,
    public kd: number,
    public outputMin = -1,
    public outputMax = 1,
  ) {}

  update(setpoint: number, measurement: number, time: number): number {
    const dt = this.lastTime > 0 ? time - this.lastTime : 0.01;
    this.lastTime = time;

    const error = setpoint - measurement;
    this.integral += error * dt;
    const derivative = dt > 0 ? (error - this.lastError) / dt : 0;
    this.lastError = error;

    let output = this.kp * error + this.ki * this.integral + this.kd * derivative;
    output = Math.max(this.outputMin, Math.min(this.outputMax, output));
    return output;
  }

  reset(): void {
    this.integral = 0;
    this.lastError = 0;
    this.lastTime = 0;
  }
}

/**
 * Ground effect model — thrust increase near ground.
 * Returns a multiplier > 1 when altitude is low.
 */
export function groundEffectMultiplier(altitude_m: number, propDiameter_m: number): number {
  if (altitude_m <= 0) return 1.5;
  const ratio = altitude_m / propDiameter_m;
  if (ratio > 3) return 1.0; // No ground effect above 3x prop diameter
  return 1 + 0.5 * Math.exp(-ratio);
}

/**
 * Simple wind disturbance model.
 * Returns force vector [fx, fy, fz] in Newtons.
 */
export function windForce(
  windSpeed_ms: number,
  windDirection_deg: number,
  gustIntensity: number,
  time_s: number,
  frontalArea_m2: number,
): [number, number, number] {
  const Cd = 1.2;
  const gustSpeed = gustIntensity * Math.sin(time_s * 0.5) * Math.sin(time_s * 2.3);
  const effectiveSpeed = windSpeed_ms + gustSpeed;

  const dirRad = (windDirection_deg * Math.PI) / 180;
  const forceMag = 0.5 * AIR_DENSITY * Cd * frontalArea_m2 * effectiveSpeed * effectiveSpeed;

  return [
    forceMag * Math.cos(dirRad),
    forceMag * Math.sin(dirRad),
    gustSpeed * 0.1, // Small vertical component from gusts
  ];
}
