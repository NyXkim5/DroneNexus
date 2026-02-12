import React from 'react';
import { SensorCard } from './SensorCard';
import { useSensorStore } from '../../stores/sensorStore';
import { useDroneStore } from '../../stores/droneStore';

const SENSOR_INFO: Record<string, { name: string; description: string; icon: string }> = {
  imu: {
    name: 'IMU (Gyro + Accel)',
    description: 'Measures how fast the drone is spinning (gyroscope) and how it\'s accelerating. This is the most critical sensor — without it, the drone cannot balance. Think of it like your inner ear for balance.',
    icon: 'rotate-3d',
  },
  gps: {
    name: 'GPS',
    description: 'Uses satellites to know where the drone is on Earth. Enables "return to home", position hold, and waypoint missions. Takes a few seconds to get a "fix" when first powered on. Accuracy is typically 2-5 meters.',
    icon: 'satellite',
  },
  barometer: {
    name: 'Barometer',
    description: 'Measures air pressure to estimate altitude. The higher you go, the lower the pressure. Not super accurate (can drift a few meters) but helps hold altitude steady. Affected by wind and temperature changes.',
    icon: 'gauge',
  },
  magnetometer: {
    name: 'Magnetometer (Compass)',
    description: 'A digital compass that senses Earth\'s magnetic field. Required for GPS navigation to know which direction is North. Can be confused by nearby metal, motors, or power lines.',
    icon: 'compass',
  },
  lidar: {
    name: 'LiDAR',
    description: 'Shoots laser beams to measure distance to objects. Can be pointed down (for precise altitude) or in a 360-degree sweep (for obstacle detection). Very accurate but adds weight and cost.',
    icon: 'scan-line',
  },
  camera: {
    name: 'Camera',
    description: 'The drone\'s eyes. Used for FPV (First Person View) flying, recording video, or computer vision tasks like object detection. Resolution, field of view, and frame rate all affect what the drone can "see".',
    icon: 'camera',
  },
  depth_camera: {
    name: 'Depth Camera',
    description: 'Creates a 3D map of what\'s in front of the drone, showing how far away each point is. Used for obstacle avoidance. Can be stereo (two cameras) or time-of-flight (measures light bounce time).',
    icon: 'box',
  },
  optical_flow: {
    name: 'Optical Flow',
    description: 'A downward-facing camera that watches the ground move. Lets the drone hold position indoors where GPS doesn\'t work. Works best over textured surfaces at low altitude.',
    icon: 'move',
  },
};

export function SensorConfigurator() {
  const { enabledSensors } = useSensorStore();
  const { currentConfig } = useDroneStore();

  return (
    <div className="h-full overflow-y-auto p-6 bg-nexus-bg font-mono">
      {/* Tactical Header */}
      <div className="mb-6">
        <div className="flex items-center gap-3 mb-2">
          <div className="w-1.5 h-5 bg-nexus-accent" />
          <h2 className="text-[12px] font-bold text-nexus-accent tracking-[0.25em] uppercase">
            Sensor Array Configuration
          </h2>
          <div className="flex-1 h-px bg-nexus-border" />
          <span className="text-[9px] text-nexus-muted tracking-[0.2em] uppercase">
            GCS // Sensors
          </span>
        </div>
        <p className="text-[11px] text-nexus-muted tracking-wide leading-relaxed ml-5">
          Configure operational sensor loadout and signal degradation profiles.
          Noise parameters affect navigation accuracy and positional stability.
        </p>
      </div>

      <div className="grid grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4 gap-4">
        {Object.entries(SENSOR_INFO).map(([sensorType, info]) => (
          <SensorCard
            key={sensorType}
            sensorType={sensorType}
            name={info.name}
            description={info.description}
            enabled={enabledSensors[sensorType] ?? false}
          />
        ))}
      </div>

      {/* Signal Condition Reference */}
      <div className="mt-8 p-4 bg-nexus-panel border border-nexus-border rounded-none">
        <div className="flex items-center gap-2 mb-3">
          <div className="w-1 h-3 bg-nexus-accent" />
          <h3 className="text-[10px] font-bold text-nexus-muted tracking-[0.2em] uppercase">
            Signal Condition Reference
          </h3>
          <div className="flex-1 h-px bg-nexus-border" />
        </div>
        <div className="grid grid-cols-4 gap-4 text-[10px]">
          <div className="border-l-2 border-nexus-accent/40 pl-2">
            <div className="text-nexus-accent font-bold tracking-[0.15em] uppercase mb-1">Laboratory</div>
            <div className="text-nexus-muted tracking-wide leading-relaxed">
              Zero noise. Sterile test conditions — isolates logic from sensor error.
            </div>
          </div>
          <div className="border-l-2 border-nexus-info/40 pl-2">
            <div className="text-nexus-info font-bold tracking-[0.15em] uppercase mb-1">Operational</div>
            <div className="text-nexus-muted tracking-wide leading-relaxed">
              Standard field conditions. Nominal signal quality. Recommended baseline.
            </div>
          </div>
          <div className="border-l-2 border-nexus-warn/40 pl-2">
            <div className="text-nexus-warn font-bold tracking-[0.15em] uppercase mb-1">Degraded</div>
            <div className="text-nexus-muted tracking-wide leading-relaxed">
              Adverse environment — high vibration, EMI, poor satellite geometry. Stress test.
            </div>
          </div>
          <div className="border-l-2 border-nexus-danger/40 pl-2">
            <div className="text-nexus-danger font-bold tracking-[0.15em] uppercase mb-1">Compromised</div>
            <div className="text-nexus-muted tracking-wide leading-relaxed">
              Partial sensor failure. Tests failsafe and fault-tolerance protocols.
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
