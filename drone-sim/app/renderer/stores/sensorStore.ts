import { create } from 'zustand';
import type { SensorNoiseConfig } from '@/types/airframe';

type NoisePresetLevel = 'perfect' | 'typical' | 'noisy' | 'failing';

interface SensorState {
  configs: Record<string, SensorNoiseConfig>;
  activePresets: Record<string, NoisePresetLevel>;
  enabledSensors: Record<string, boolean>;

  loadSensorConfig: (sensorType: string, config: SensorNoiseConfig) => void;
  setPreset: (sensorType: string, preset: NoisePresetLevel) => void;
  toggleSensor: (sensorType: string) => void;
  setSensorEnabled: (sensorType: string, enabled: boolean) => void;
}

export const useSensorStore = create<SensorState>((set) => ({
  configs: {},
  activePresets: {
    imu: 'typical',
    gps: 'typical',
    barometer: 'typical',
    magnetometer: 'typical',
    lidar: 'typical',
    camera: 'typical',
  },
  enabledSensors: {
    imu: true,
    gps: true,
    barometer: true,
    magnetometer: false,
    lidar: false,
    camera: true,
    depth_camera: false,
    optical_flow: false,
  },

  loadSensorConfig: (sensorType, config) => set((state) => ({
    configs: { ...state.configs, [sensorType]: config },
  })),

  setPreset: (sensorType, preset) => set((state) => ({
    activePresets: { ...state.activePresets, [sensorType]: preset },
  })),

  toggleSensor: (sensorType) => set((state) => ({
    enabledSensors: { ...state.enabledSensors, [sensorType]: !state.enabledSensors[sensorType] },
  })),

  setSensorEnabled: (sensorType, enabled) => set((state) => ({
    enabledSensors: { ...state.enabledSensors, [sensorType]: enabled },
  })),
}));
