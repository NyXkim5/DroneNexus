import { create } from 'zustand';
import type { AirframeConfig } from '@/types/airframe';

interface FleetDrone {
  id: string;
  filename: string;
  config: AirframeConfig;
}

interface FleetState {
  drones: FleetDrone[];
  compareIds: string[];

  addDrone: (filename: string, config: AirframeConfig) => void;
  removeDrone: (id: string) => void;
  updateDrone: (id: string, config: AirframeConfig) => void;
  setCompare: (ids: string[]) => void;
  clearFleet: () => void;
}

export const useFleetStore = create<FleetState>((set) => ({
  drones: [],
  compareIds: [],

  addDrone: (filename, config) => set((state) => ({
    drones: [...state.drones, {
      id: `${config.name}-${Date.now()}`,
      filename,
      config,
    }],
  })),

  removeDrone: (id) => set((state) => ({
    drones: state.drones.filter(d => d.id !== id),
    compareIds: state.compareIds.filter(cid => cid !== id),
  })),

  updateDrone: (id, config) => set((state) => ({
    drones: state.drones.map(d => d.id === id ? { ...d, config } : d),
  })),

  setCompare: (ids) => set({ compareIds: ids.slice(0, 2) }),

  clearFleet: () => set({ drones: [], compareIds: [] }),
}));
