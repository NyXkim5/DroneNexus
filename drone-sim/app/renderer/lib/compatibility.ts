/**
 * Cross-component compatibility checker.
 * Validates that all hardware components work together correctly.
 */
import type { AirframeConfig } from '@/types/airframe';

export interface CompatibilityResult {
  valid: boolean;
  errors: CompatibilityIssue[];
  warnings: CompatibilityIssue[];
  info: CompatibilityIssue[];
}

export interface CompatibilityIssue {
  category: 'voltage' | 'mechanical' | 'thermal' | 'firmware' | 'weight' | 'performance';
  severity: 'error' | 'warning' | 'info';
  component: string;
  message: string;
  fix?: string;
}

export function runCompatibilityCheck(config: AirframeConfig): CompatibilityResult {
  const issues: CompatibilityIssue[] = [];

  checkVoltageCompatibility(config, issues);
  checkMechanicalFit(config, issues);
  checkThermalLimits(config, issues);
  checkFirmwareSupport(config, issues);
  checkWeightBalance(config, issues);
  checkPerformance(config, issues);

  return {
    valid: issues.filter(i => i.severity === 'error').length === 0,
    errors: issues.filter(i => i.severity === 'error'),
    warnings: issues.filter(i => i.severity === 'warning'),
    info: issues.filter(i => i.severity === 'info'),
  };
}

function checkVoltageCompatibility(config: AirframeConfig, issues: CompatibilityIssue[]): void {
  const battV = config.battery.voltage_full;
  const compat = config.compatibility.voltage_compatibility;

  // ESC voltage rating
  if (battV > compat.esc_rated_voltage) {
    issues.push({
      category: 'voltage',
      severity: 'error',
      component: 'ESC',
      message: `Battery full voltage (${battV}V) exceeds ESC rated voltage (${compat.esc_rated_voltage}V). This will destroy the ESC.`,
      fix: `Use a ${compat.max_cells}S or lower battery, or upgrade to ESCs rated for ${battV}V+.`,
    });
  }

  // FC voltage input range
  const fcRange = compat.fc_input_range;
  if (battV > fcRange[1]) {
    issues.push({
      category: 'voltage',
      severity: 'error',
      component: 'Flight Controller',
      message: `Battery voltage (${battV}V) exceeds FC maximum input (${fcRange[1]}V).`,
      fix: `Use a lower cell count battery or upgrade the FC.`,
    });
  }
  if (config.battery.voltage_empty < fcRange[0]) {
    issues.push({
      category: 'voltage',
      severity: 'warning',
      component: 'Flight Controller',
      message: `Battery empty voltage (${config.battery.voltage_empty}V) is near FC minimum input (${fcRange[0]}V). FC may brown-out before low battery warning.`,
      fix: `Set battery warning voltage higher to avoid FC brownout.`,
    });
  }

  // Motor rated voltage
  if (battV > compat.motors_rated_voltage * 1.1) {
    issues.push({
      category: 'voltage',
      severity: 'warning',
      component: 'Motors',
      message: `Battery voltage (${battV}V) exceeds motor rated voltage (${compat.motors_rated_voltage}V) by >10%.`,
      fix: `Motors will run hot and have reduced lifespan. Consider lower KV motors.`,
    });
  }

  // Cell count in range
  if (config.battery.cells < compat.min_cells) {
    issues.push({
      category: 'voltage',
      severity: 'error',
      component: 'Battery',
      message: `${config.battery.cells}S battery below minimum ${compat.min_cells}S for this build.`,
    });
  }
  if (config.battery.cells > compat.max_cells) {
    issues.push({
      category: 'voltage',
      severity: 'error',
      component: 'Battery',
      message: `${config.battery.cells}S battery exceeds maximum ${compat.max_cells}S for this build.`,
    });
  }
}

function checkMechanicalFit(config: AirframeConfig, issues: CompatibilityIssue[]): void {
  // Prop clearance
  if (config.compatibility.prop_clearance_mm < 3) {
    issues.push({
      category: 'mechanical',
      severity: 'error',
      component: 'Propellers',
      message: `Prop clearance (${config.compatibility.prop_clearance_mm}mm) is dangerously low. Props may clip each other or the frame.`,
      fix: `Use smaller props or a larger frame.`,
    });
  } else if (config.compatibility.prop_clearance_mm < 6) {
    issues.push({
      category: 'mechanical',
      severity: 'warning',
      component: 'Propellers',
      message: `Prop clearance (${config.compatibility.prop_clearance_mm}mm) is tight. May cause vibration issues.`,
    });
  }

  // Motor shaft vs prop bore
  if (config.compatibility.motor_shaft_mm > 5 && config.propellers.size_inch <= 5) {
    issues.push({
      category: 'mechanical',
      severity: 'warning',
      component: 'Motors/Props',
      message: `${config.compatibility.motor_shaft_mm}mm motor shaft unusual for ${config.propellers.size_inch}" props. Verify prop adapter compatibility.`,
    });
  }

  // Battery fit
  const battFit = config.compatibility.battery_fit;
  if (config.battery.capacity_mah > 3000 && battFit.max_length_mm < 100) {
    issues.push({
      category: 'mechanical',
      severity: 'warning',
      component: 'Battery',
      message: `Large capacity battery (${config.battery.capacity_mah}mAh) may not fit in ${battFit.max_length_mm}mm battery slot.`,
    });
  }

  // FC stack size
  const stackSize = config.compatibility.fc_stack_size;
  if (stackSize === '20x20' && config.frame.wheelbase_mm > 300) {
    issues.push({
      category: 'mechanical',
      severity: 'info',
      component: 'Flight Controller',
      message: `20x20mm FC stack is small for a ${config.frame.wheelbase_mm}mm frame. Consider 30.5x30.5mm for easier wiring.`,
    });
  }
}

function checkThermalLimits(config: AirframeConfig, issues: CompatibilityIssue[]): void {
  // Motor heating at sustained high throttle
  const tw = (config.motors.max_thrust_g * config.motors.count) /
    (config.frame.weight_g + config.battery.weight_g + config.motors.weight_g * config.motors.count);

  if (tw < 3 && config.thermal.cooling === 'passive') {
    issues.push({
      category: 'thermal',
      severity: 'warning',
      component: 'Motors',
      message: `Low T:W ratio (${tw.toFixed(1)}) with passive cooling means motors run near max current. Thermal throttling likely in warm conditions.`,
      fix: `Add active cooling or reduce weight.`,
    });
  }

  // ESC current headroom
  const peakPerMotor = config.electrical.peak_burst_current_a / config.motors.count;
  if (peakPerMotor > config.electronics.esc.max_current_a * 0.85) {
    issues.push({
      category: 'thermal',
      severity: 'warning',
      component: 'ESC',
      message: `Peak current per motor (${peakPerMotor.toFixed(0)}A) is within 15% of ESC rating (${config.electronics.esc.max_current_a}A). ESC will run hot.`,
      fix: `Upgrade to higher-rated ESCs for thermal margin.`,
    });
  }
}

function checkFirmwareSupport(config: AirframeConfig, issues: CompatibilityIssue[]): void {
  const firmware = config.electronics.flight_controller.firmware;
  const supported = config.compatibility.firmware_support;

  if (!supported.includes(firmware)) {
    issues.push({
      category: 'firmware',
      severity: 'error',
      component: 'Flight Controller',
      message: `${firmware} is not in the supported firmware list for this FC: [${supported.join(', ')}].`,
    });
  }

  // ESC protocol support
  const escProtocol = config.electronics.esc.protocol;
  const supportedProtocols = config.compatibility.esc_protocol_supported;
  if (!supportedProtocols.includes(escProtocol)) {
    issues.push({
      category: 'firmware',
      severity: 'warning',
      component: 'ESC',
      message: `ESC protocol ${escProtocol} not in supported list: [${supportedProtocols.join(', ')}].`,
    });
  }
}

function checkWeightBalance(config: AirframeConfig, issues: CompatibilityIssue[]): void {
  const cg = config.mechanical.center_of_gravity_mm;
  const armLength = config.frame.arm_length_mm;

  // CG offset from center
  const horizontalOffset = Math.sqrt(cg[0] * cg[0] + cg[1] * cg[1]);
  const maxAcceptableOffset = armLength * 0.1; // 10% of arm length

  if (horizontalOffset > maxAcceptableOffset) {
    issues.push({
      category: 'weight',
      severity: 'warning',
      component: 'Frame',
      message: `Center of gravity is offset ${horizontalOffset.toFixed(0)}mm from center. Max recommended: ${maxAcceptableOffset.toFixed(0)}mm.`,
      fix: `Adjust battery position or component placement to center the CG.`,
    });
  }
}

function checkPerformance(config: AirframeConfig, issues: CompatibilityIssue[]): void {
  const totalThrust = config.motors.max_thrust_g * config.motors.count;
  const auw = config.frame.weight_g + config.battery.weight_g +
    config.motors.weight_g * config.motors.count +
    config.propellers.weight_g * config.motors.count +
    config.electronics.flight_controller.weight_g +
    config.electronics.esc.weight_g +
    config.electronics.receiver.weight_g +
    config.electronics.vtx.weight_g +
    config.sensors.camera.weight_g + 20;

  const tw = totalThrust / auw;

  if (tw < 1.5) {
    issues.push({
      category: 'performance',
      severity: 'error',
      component: 'System',
      message: `Thrust-to-weight ratio (${tw.toFixed(1)}:1) is below 1.5:1. Drone may not be able to fly safely.`,
    });
  } else if (tw < 2) {
    issues.push({
      category: 'performance',
      severity: 'warning',
      component: 'System',
      message: `Thrust-to-weight ratio (${tw.toFixed(1)}:1) is marginal. Very limited maneuverability and wind resistance.`,
    });
  }

  // Flight time estimate
  const hoverThrottle = 1 / tw;
  const hoverCurrent = config.electrical.avg_hover_current_a;
  const usableCapacity = config.battery.capacity_mah * 0.8 / 1000;
  const flightTimeMin = (usableCapacity / hoverCurrent) * 60;

  if (flightTimeMin < 3) {
    issues.push({
      category: 'performance',
      severity: 'warning',
      component: 'Battery',
      message: `Estimated flight time (${flightTimeMin.toFixed(1)} min) is very short.`,
      fix: `Use a larger capacity battery or more efficient motors/props.`,
    });
  }
}
