import React from 'react';
import { useDroneStore } from '../../stores/droneStore';
import type { AirframeConfig } from '../../types/airframe';

function getNestedValue(obj: Record<string, unknown>, path: string): unknown {
  return path.split('.').reduce((acc: unknown, key) => {
    if (acc && typeof acc === 'object') return (acc as Record<string, unknown>)[key];
    return undefined;
  }, obj);
}

function setNestedValue(obj: Record<string, unknown>, path: string, value: unknown): Record<string, unknown> {
  const clone = JSON.parse(JSON.stringify(obj));
  const keys = path.split('.');
  let current = clone;
  for (let i = 0; i < keys.length - 1; i++) {
    current = current[keys[i]] as Record<string, unknown>;
  }
  current[keys[keys.length - 1]] = value;
  return clone;
}

interface FieldConfig {
  key: string;
  label: string;
  type: 'number' | 'text' | 'select' | 'boolean';
  unit?: string;
  options?: string[];
  tooltip?: string;
  min?: number;
  max?: number;
  step?: number;
}

const FIELD_CONFIGS: Record<string, FieldConfig[]> = {
  'frame': [
    { key: 'frame.type', label: 'Type', type: 'select', options: ['quadcopter', 'hexacopter', 'octocopter'], tooltip: 'Number and arrangement of motors' },
    { key: 'frame.layout', label: 'Layout', type: 'select', options: ['X', 'H', '+', 'deadcat'], tooltip: 'Motor arm arrangement pattern' },
    { key: 'frame.arm_length_mm', label: 'Arm Length', type: 'number', unit: 'mm', min: 50, max: 500, tooltip: 'Distance from center to motor mount' },
    { key: 'frame.wheelbase_mm', label: 'Wheelbase', type: 'number', unit: 'mm', min: 100, max: 1000, tooltip: 'Diagonal motor-to-motor distance' },
    { key: 'frame.weight_g', label: 'Frame Weight', type: 'number', unit: 'g', min: 10, max: 5000, tooltip: 'Weight of the frame alone' },
    { key: 'frame.material', label: 'Material', type: 'select', options: ['carbon_fiber', 'aluminum', 'plastic', 'titanium'] },
  ],
  'frame.type': [
    { key: 'frame.type', label: 'Type', type: 'select', options: ['quadcopter', 'hexacopter', 'octocopter'] },
  ],
  'frame.layout': [
    { key: 'frame.layout', label: 'Layout', type: 'select', options: ['X', 'H', '+', 'deadcat'] },
  ],
  'motors': [
    { key: 'motors.model', label: 'Model', type: 'text', tooltip: 'Motor model name (e.g., EMAX ECO II 2306)' },
    { key: 'motors.count', label: 'Count', type: 'number', min: 3, max: 8, step: 1, tooltip: 'Number of motors' },
    { key: 'motors.kv', label: 'KV Rating', type: 'number', unit: 'KV', min: 100, max: 5000, tooltip: 'RPM per volt — higher KV = faster spin, less torque' },
    { key: 'motors.max_thrust_g', label: 'Max Thrust/Motor', type: 'number', unit: 'g', min: 100, max: 10000, tooltip: 'Maximum thrust each motor can produce' },
    { key: 'motors.weight_g', label: 'Weight/Motor', type: 'number', unit: 'g', min: 5, max: 500, tooltip: 'Weight of each motor' },
    { key: 'motors.idle_throttle_pct', label: 'Idle Throttle', type: 'number', unit: '%', min: 0, max: 20, tooltip: 'Minimum throttle to keep motors spinning' },
  ],
  'propellers': [
    { key: 'propellers.model', label: 'Model', type: 'text' },
    { key: 'propellers.size_inch', label: 'Size', type: 'number', unit: 'in', min: 2, max: 30, step: 0.1, tooltip: 'Propeller diameter in inches' },
    { key: 'propellers.pitch_inch', label: 'Pitch', type: 'number', unit: 'in', min: 1, max: 15, step: 0.1, tooltip: 'Theoretical forward distance per revolution' },
    { key: 'propellers.blades', label: 'Blades', type: 'number', min: 2, max: 6, step: 1, tooltip: '2-blade = efficient, 3-blade = more grip' },
    { key: 'propellers.weight_g', label: 'Weight', type: 'number', unit: 'g', min: 1, max: 100, tooltip: 'Weight per propeller' },
    { key: 'propellers.max_rpm', label: 'Max RPM', type: 'number', min: 1000, max: 50000, step: 1000 },
  ],
  'battery': [
    { key: 'battery.cells', label: 'Cell Count', type: 'number', min: 1, max: 14, step: 1, unit: 'S', tooltip: '1S = 3.7V nominal. Common: 4S, 6S' },
    { key: 'battery.capacity_mah', label: 'Capacity', type: 'number', unit: 'mAh', min: 100, max: 50000, step: 50, tooltip: 'Energy storage — higher = longer flight, heavier' },
    { key: 'battery.weight_g', label: 'Weight', type: 'number', unit: 'g', min: 10, max: 10000 },
    { key: 'battery.discharge_rate_c', label: 'Discharge Rate', type: 'number', unit: 'C', min: 10, max: 200, tooltip: 'Max safe discharge rate. 100C on 1300mAh = 130A max' },
    { key: 'battery.connector', label: 'Connector', type: 'select', options: ['XT30', 'XT60', 'XT90', 'XT150', 'EC3', 'EC5'] },
  ],
  'electronics.flight_controller': [
    { key: 'electronics.flight_controller.model', label: 'Model', type: 'text' },
    { key: 'electronics.flight_controller.firmware', label: 'Firmware', type: 'select', options: ['Betaflight', 'ArduPilot', 'PX4', 'iNav'], tooltip: 'Flight control software' },
    { key: 'electronics.flight_controller.processor', label: 'Processor', type: 'text' },
    { key: 'electronics.flight_controller.weight_g', label: 'Weight', type: 'number', unit: 'g', min: 1, max: 200 },
  ],
  'electronics.esc': [
    { key: 'electronics.esc.model', label: 'Model', type: 'text' },
    { key: 'electronics.esc.protocol', label: 'Protocol', type: 'select', options: ['DShot150', 'DShot300', 'DShot600', 'DShot1200', 'PWM', 'OneShot'] },
    { key: 'electronics.esc.max_current_a', label: 'Max Current', type: 'number', unit: 'A', min: 10, max: 200, tooltip: 'Per-motor current rating' },
    { key: 'electronics.esc.weight_g', label: 'Weight', type: 'number', unit: 'g', min: 1, max: 200 },
  ],
  'electronics.receiver': [
    { key: 'electronics.receiver.model', label: 'Model', type: 'text' },
    { key: 'electronics.receiver.protocol', label: 'Protocol', type: 'select', options: ['ELRS', 'Crossfire', 'FrSky', 'Spektrum', 'FlySky'] },
    { key: 'electronics.receiver.frequency', label: 'Frequency', type: 'select', options: ['900MHz', '2.4GHz'] },
    { key: 'electronics.receiver.weight_g', label: 'Weight', type: 'number', unit: 'g', min: 1, max: 50 },
  ],
  'electronics.vtx': [
    { key: 'electronics.vtx.model', label: 'Model', type: 'text' },
    { key: 'electronics.vtx.max_power_mw', label: 'Max Power', type: 'number', unit: 'mW', min: 25, max: 2000 },
    { key: 'electronics.vtx.frequency_band', label: 'Band', type: 'select', options: ['5.8GHz', '2.4GHz', '1.3GHz'] },
    { key: 'electronics.vtx.weight_g', label: 'Weight', type: 'number', unit: 'g', min: 1, max: 100 },
  ],
  'sensors.imu': [
    { key: 'sensors.imu.model', label: 'Model', type: 'text', tooltip: 'Inertial Measurement Unit — measures rotation and acceleration' },
    { key: 'sensors.imu.gyro_range_dps', label: 'Gyro Range', type: 'number', unit: 'dps', tooltip: 'Maximum rotation rate measurable' },
    { key: 'sensors.imu.accel_range_g', label: 'Accel Range', type: 'number', unit: 'g', tooltip: 'Maximum acceleration measurable' },
    { key: 'sensors.imu.sample_rate_hz', label: 'Sample Rate', type: 'number', unit: 'Hz', tooltip: 'How many times per second the sensor reads' },
  ],
  'sensors.barometer': [
    { key: 'sensors.barometer.model', label: 'Model', type: 'text', tooltip: 'Measures air pressure to estimate altitude' },
    { key: 'sensors.barometer.resolution_cm', label: 'Resolution', type: 'number', unit: 'cm', tooltip: 'Smallest altitude change detectable' },
  ],
  'sensors.magnetometer': [
    { key: 'sensors.magnetometer.present', label: 'Installed', type: 'boolean', tooltip: 'Compass sensor — needed for GPS navigation' },
    { key: 'sensors.magnetometer.model', label: 'Model', type: 'text' },
  ],
  'sensors.gps': [
    { key: 'sensors.gps.present', label: 'Installed', type: 'boolean', tooltip: 'GPS for position hold, return-to-home, and waypoint missions' },
    { key: 'sensors.gps.model', label: 'Model', type: 'text' },
    { key: 'sensors.gps.update_rate_hz', label: 'Update Rate', type: 'number', unit: 'Hz' },
    { key: 'sensors.gps.accuracy_m', label: 'Accuracy', type: 'number', unit: 'm' },
  ],
  'sensors.lidar': [
    { key: 'sensors.lidar.present', label: 'Installed', type: 'boolean', tooltip: 'Laser rangefinder for obstacle detection or altitude' },
    { key: 'sensors.lidar.model', label: 'Model', type: 'text' },
    { key: 'sensors.lidar.range_m', label: 'Range', type: 'number', unit: 'm' },
    { key: 'sensors.lidar.type', label: 'Type', type: 'select', options: ['360', 'downward', 'forward'] },
  ],
  'sensors.camera': [
    { key: 'sensors.camera.model', label: 'Model', type: 'text', tooltip: 'FPV or recording camera' },
    { key: 'sensors.camera.resolution', label: 'Resolution', type: 'text' },
    { key: 'sensors.camera.fov_deg', label: 'Field of View', type: 'number', unit: 'deg', tooltip: 'Wider = more visible area, more distortion' },
    { key: 'sensors.camera.weight_g', label: 'Weight', type: 'number', unit: 'g' },
  ],
  'sensors.depth_camera': [
    { key: 'sensors.depth_camera.present', label: 'Installed', type: 'boolean', tooltip: 'Depth camera for 3D obstacle detection' },
    { key: 'sensors.depth_camera.model', label: 'Model', type: 'text' },
    { key: 'sensors.depth_camera.range_m', label: 'Range', type: 'number', unit: 'm' },
    { key: 'sensors.depth_camera.type', label: 'Type', type: 'select', options: ['stereo', 'tof'] },
  ],
  'sensors.optical_flow': [
    { key: 'sensors.optical_flow.present', label: 'Installed', type: 'boolean', tooltip: 'Tracks ground movement for indoor position hold' },
    { key: 'sensors.optical_flow.model', label: 'Model', type: 'text' },
    { key: 'sensors.optical_flow.range_m', label: 'Range', type: 'number', unit: 'm' },
  ],
  'thermal': [
    { key: 'thermal.motor_max_temp_c', label: 'Motor Max Temp', type: 'number', unit: 'C' },
    { key: 'thermal.esc_max_temp_c', label: 'ESC Max Temp', type: 'number', unit: 'C' },
    { key: 'thermal.cooling', label: 'Cooling', type: 'select', options: ['passive', 'active_fan', 'ducted'] },
    { key: 'thermal.thermal_throttle_pct', label: 'Thermal Throttle', type: 'number', unit: '%' },
  ],
  'mechanical': [
    { key: 'mechanical.vibration.damping', label: 'FC Damping', type: 'select', options: ['hard_mount', 'soft_mount', 'foam_tape'] },
    { key: 'mechanical.vibration.dominant_frequency_hz', label: 'Vibration Freq', type: 'number', unit: 'Hz' },
  ],
  'compatibility': [
    { key: 'compatibility.fc_stack_size', label: 'FC Stack Size', type: 'select', options: ['20x20', '25.5x25.5', '30.5x30.5'] },
    { key: 'compatibility.motor_shaft_mm', label: 'Motor Shaft', type: 'number', unit: 'mm' },
    { key: 'compatibility.prop_clearance_mm', label: 'Prop Clearance', type: 'number', unit: 'mm' },
  ],
  'electrical': [
    { key: 'electrical.avg_hover_current_a', label: 'Hover Current', type: 'number', unit: 'A', tooltip: 'Average current draw while hovering' },
    { key: 'electrical.avg_cruise_current_a', label: 'Cruise Current', type: 'number', unit: 'A' },
    { key: 'electrical.peak_burst_current_a', label: 'Peak Current', type: 'number', unit: 'A' },
    { key: 'electrical.wire_gauge.battery_to_esc', label: 'Battery Wire', type: 'select', options: ['10AWG', '12AWG', '14AWG', '16AWG'] },
  ],
};

const FRIENDLY_NAMES: Record<string, string> = {
  'frame': 'Frame Configuration',
  'motors': 'Motor Specifications',
  'propellers': 'Propeller Specifications',
  'battery': 'Battery Configuration',
  'electronics.flight_controller': 'Flight Controller',
  'electronics.esc': 'Electronic Speed Controller (ESC)',
  'electronics.receiver': 'Radio Receiver',
  'electronics.vtx': 'Video Transmitter (VTX)',
  'sensors.imu': 'IMU (Gyro + Accelerometer)',
  'sensors.barometer': 'Barometer (Altitude Sensor)',
  'sensors.magnetometer': 'Magnetometer (Compass)',
  'sensors.gps': 'GPS Module',
  'sensors.lidar': 'LiDAR (Laser Range Finder)',
  'sensors.camera': 'Camera',
  'sensors.depth_camera': 'Depth Camera',
  'sensors.optical_flow': 'Optical Flow Sensor',
  'thermal': 'Thermal Limits',
  'mechanical': 'Mechanical Properties',
  'compatibility': 'Component Compatibility',
  'electrical': 'Electrical System',
};

const EXPLANATIONS: Record<string, string> = {
  'frame': 'The skeleton of your drone. Determines size, weight, and what components fit.',
  'motors': 'Spin the propellers to create thrust. KV rating determines speed vs torque.',
  'propellers': 'Generate lift by pushing air down. Bigger props = more efficient, smaller = more agile.',
  'battery': 'Stores energy for flight. More capacity = longer flight but heavier.',
  'electronics.flight_controller': 'The brain. Reads sensors, runs PID loops, controls motors.',
  'electronics.esc': 'Converts battery power to motor speed. Must handle the current your motors draw.',
  'electronics.receiver': 'Receives commands from your radio transmitter.',
  'electronics.vtx': 'Broadcasts live video from the camera to your goggles/monitor.',
  'sensors.imu': 'Measures rotation speed and acceleration. Essential for stable flight.',
  'sensors.barometer': 'Uses air pressure to estimate altitude. Enables altitude hold mode.',
  'sensors.magnetometer': 'Digital compass. Required for GPS navigation modes.',
  'sensors.gps': 'Satellite positioning. Enables position hold, return-to-home, waypoints.',
  'sensors.lidar': 'Laser range finding for precise altitude or obstacle detection.',
  'sensors.camera': 'Eyes of the drone — for FPV flying or recording.',
  'sensors.depth_camera': 'Creates 3D depth maps for obstacle avoidance.',
  'sensors.optical_flow': 'Tracks ground texture movement for indoor position hold.',
  'thermal': 'Temperature limits that affect how long and hard you can fly.',
  'mechanical': 'Physical properties affecting vibration, balance, and handling.',
  'compatibility': 'Checks that all your components work together.',
  'electrical': 'Power distribution, wiring, and current flow.',
};

export function PropertiesPanel() {
  const { currentConfig, selectedComponent, updateConfig } = useDroneStore();

  if (!selectedComponent) {
    return (
      <div className="p-6 text-center font-mono">
        <div className="w-8 h-px bg-nexus-border mx-auto mb-4" />
        <div className="text-nexus-muted text-[11px] tracking-widest uppercase mb-2">
          No Component Selected
        </div>
        <div className="text-[10px] text-nexus-muted/50 tracking-wide">
          Select a component from the manifest or 3D viewport to inspect specifications
        </div>
        <div className="w-8 h-px bg-nexus-border mx-auto mt-4" />
      </div>
    );
  }

  const fields = FIELD_CONFIGS[selectedComponent] || [];
  const title = FRIENDLY_NAMES[selectedComponent] || selectedComponent;
  const explanation = EXPLANATIONS[selectedComponent];

  const handleChange = (fieldPath: string, value: unknown) => {
    updateConfig((config) => {
      const clone = JSON.parse(JSON.stringify(config));
      const updated = setNestedValue(clone, fieldPath, value);
      return updated as AirframeConfig;
    });
  };

  return (
    <div className="p-4 font-mono">
      {/* Tactical Header */}
      <div className="flex items-center gap-2 mb-3">
        <div className="w-1.5 h-4 bg-nexus-accent" />
        <span className="text-[10px] font-bold text-nexus-accent tracking-[0.2em] uppercase">
          Specifications
        </span>
        <div className="flex-1 h-px bg-nexus-border" />
      </div>

      {/* Component Name */}
      <div className="text-[11px] font-bold text-nexus-text tracking-[0.15em] uppercase mb-3">
        {title}
      </div>

      {/* Explanation Box — green left border */}
      {explanation && (
        <div className="mb-4 p-3 bg-nexus-bg border-l-2 border-nexus-accent/50 text-[10px] text-nexus-muted leading-relaxed tracking-wide">
          {explanation}
        </div>
      )}

      {/* Field Divider */}
      <div className="flex items-center gap-2 mb-3">
        <span className="text-[9px] text-nexus-muted/50 tracking-[0.2em] uppercase">Parameters</span>
        <div className="flex-1 h-px bg-nexus-border" />
      </div>

      <div className="space-y-3">
        {fields.map((field) => {
          const value = getNestedValue(currentConfig as unknown as Record<string, unknown>, field.key);

          return (
            <div key={field.key}>
              <label className="flex items-center justify-between mb-1">
                <span className="text-[10px] text-nexus-muted tracking-[0.1em] uppercase">{field.label}</span>
                {field.unit && (
                  <span className="text-[9px] text-nexus-accent/60 tracking-wider font-bold">
                    [{field.unit}]
                  </span>
                )}
              </label>

              {field.tooltip && (
                <div className="text-[9px] text-nexus-muted/40 mb-1 tracking-wide">{field.tooltip}</div>
              )}

              {field.type === 'number' && (
                <input
                  type="number"
                  className="w-full bg-nexus-bg border border-nexus-border rounded-none px-3 py-1.5 text-[11px] text-nexus-text font-mono tracking-wider focus:outline-none focus:border-nexus-accent focus:ring-1 focus:ring-nexus-accent/30 transition-colors"
                  value={value as number ?? 0}
                  min={field.min}
                  max={field.max}
                  step={field.step ?? 1}
                  onChange={(e) => handleChange(field.key, parseFloat(e.target.value) || 0)}
                />
              )}

              {field.type === 'text' && (
                <input
                  type="text"
                  className="w-full bg-nexus-bg border border-nexus-border rounded-none px-3 py-1.5 text-[11px] text-nexus-text font-mono tracking-wider focus:outline-none focus:border-nexus-accent focus:ring-1 focus:ring-nexus-accent/30 transition-colors"
                  value={(value as string) ?? ''}
                  onChange={(e) => handleChange(field.key, e.target.value)}
                />
              )}

              {field.type === 'select' && (
                <select
                  className="w-full bg-nexus-bg border border-nexus-border rounded-none px-3 py-1.5 text-[11px] text-nexus-text font-mono tracking-wider uppercase focus:outline-none focus:border-nexus-accent focus:ring-1 focus:ring-nexus-accent/30 transition-colors"
                  value={(value as string) ?? ''}
                  onChange={(e) => handleChange(field.key, e.target.value)}
                >
                  {field.options?.map((opt) => (
                    <option key={opt} value={opt}>{opt}</option>
                  ))}
                </select>
              )}

              {field.type === 'boolean' && (
                <button
                  className={`w-full text-left px-3 py-1.5 rounded-none text-[11px] font-bold tracking-[0.15em] uppercase border transition-colors ${
                    value
                      ? 'bg-nexus-accent/10 border-nexus-accent/40 text-nexus-accent'
                      : 'bg-nexus-bg border-nexus-border text-nexus-muted'
                  }`}
                  onClick={() => handleChange(field.key, !value)}
                >
                  {value ? '[ ACTIVE ]' : '[ INACTIVE ]'}
                </button>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
