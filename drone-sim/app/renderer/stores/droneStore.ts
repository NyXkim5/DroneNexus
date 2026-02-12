import { create } from 'zustand';
import type { AirframeConfig } from '@/types/airframe';
import { processAirframe, createBlankAirframe } from '@/lib/airframe-parser';
import { computeLimits } from '@/lib/physics';

interface DroneState {
  currentConfig: AirframeConfig;
  selectedComponent: string | null;
  isDirty: boolean;
  filename: string | null;

  // Actions
  loadConfig: (config: AirframeConfig, filename?: string) => void;
  updateConfig: (updater: (config: AirframeConfig) => AirframeConfig) => void;
  selectComponent: (id: string | null) => void;
  createNew: () => void;
  markClean: () => void;
}

export const useDroneStore = create<DroneState>((set) => ({
  currentConfig: createBlankAirframe(),
  selectedComponent: null,
  isDirty: false,
  filename: null,

  loadConfig: (config, filename) => set({
    currentConfig: processAirframe(config),
    selectedComponent: null,
    isDirty: false,
    filename: filename ?? null,
  }),

  updateConfig: (updater) => set((state) => {
    const updated = updater(state.currentConfig);
    updated.computed = computeLimits(updated);
    return { currentConfig: updated, isDirty: true };
  }),

  selectComponent: (id) => set({ selectedComponent: id }),

  createNew: () => set({
    currentConfig: createBlankAirframe(),
    selectedComponent: null,
    isDirty: false,
    filename: null,
  }),

  markClean: () => set({ isDirty: false }),
}));
