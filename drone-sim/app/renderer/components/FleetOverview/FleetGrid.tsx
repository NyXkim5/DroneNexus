import React, { useEffect, useState } from 'react';
import { useFleetStore } from '../../stores/fleetStore';
import { useDroneStore } from '../../stores/droneStore';
import { DroneCard } from './DroneCard';
import { CompareView } from './CompareView';
import { processAirframe } from '../../lib/airframe-parser';
import { Plus, Upload, GitCompare } from 'lucide-react';
import type { AirframeConfig } from '../../types/airframe';

export function FleetGrid() {
  const { drones, addDrone, compareIds, setCompare } = useFleetStore();
  const { loadConfig } = useDroneStore();
  const [showCompare, setShowCompare] = useState(false);
  const [loading, setLoading] = useState(false);

  // Load all presets on first mount
  useEffect(() => {
    if (drones.length > 0) return;
    loadPresets();
  }, []);

  const loadPresets = async () => {
    setLoading(true);
    try {
      const files = await window.api.listAirframes();
      for (const file of files) {
        const raw = await window.api.loadAirframe(file);
        const config = processAirframe(raw);
        addDrone(file, config);
      }
    } catch {
      // No files or no API
    }
    setLoading(false);
  };

  const handleImport = async () => {
    const config = await window.api.importAirframe();
    if (config) {
      const processed = processAirframe(config);
      addDrone('imported.yaml', processed);
    }
  };

  const handleOpenInBuilder = (config: AirframeConfig, filename: string) => {
    loadConfig(config, filename);
  };

  const handleToggleCompare = (id: string) => {
    if (compareIds.includes(id)) {
      setCompare(compareIds.filter(c => c !== id));
    } else if (compareIds.length < 2) {
      setCompare([...compareIds, id]);
    }
  };

  if (showCompare && compareIds.length === 2) {
    const drone1 = drones.find(d => d.id === compareIds[0]);
    const drone2 = drones.find(d => d.id === compareIds[1]);
    if (drone1 && drone2) {
      return (
        <CompareView
          drone1={drone1.config}
          drone2={drone2.config}
          onBack={() => setShowCompare(false)}
        />
      );
    }
  }

  return (
    <div className="h-full overflow-y-auto bg-nexus-bg p-6 font-mono">
      {/* Header bar */}
      <div className="flex items-end justify-between mb-6">
        <div>
          <div className="flex items-center gap-3 mb-1">
            <div className="w-1 h-6 bg-nexus-accent" />
            <h2 className="text-sm font-bold text-nexus-text uppercase tracking-[0.2em]">
              Force Disposition
            </h2>
          </div>
          <p className="text-[11px] text-nexus-muted uppercase tracking-wider ml-4">
            Unit Status Overview — [ {drones.length} ] unit{drones.length !== 1 ? 's' : ''} active
          </p>
        </div>
        <div className="flex gap-2">
          {compareIds.length === 2 && (
            <button
              onClick={() => setShowCompare(true)}
              className="flex items-center gap-2 px-3 py-1.5 rounded-none border border-nexus-accent bg-nexus-accent/10 text-nexus-accent text-[11px] font-mono uppercase tracking-wider hover:bg-nexus-accent/20 transition-colors"
            >
              <GitCompare size={13} />
              Compare Selected
            </button>
          )}
          <button
            onClick={handleImport}
            className="flex items-center gap-2 px-3 py-1.5 rounded-none border border-nexus-border bg-nexus-surface text-nexus-muted text-[11px] font-mono uppercase tracking-wider hover:border-nexus-text/30 hover:text-nexus-text transition-colors"
          >
            <Upload size={13} />
            Import Unit
          </button>
          <button
            onClick={loadPresets}
            className="flex items-center gap-2 px-3 py-1.5 rounded-none border border-nexus-border bg-nexus-surface text-nexus-muted text-[11px] font-mono uppercase tracking-wider hover:border-nexus-text/30 hover:text-nexus-text transition-colors"
          >
            <Plus size={13} />
            Load Presets
          </button>
        </div>
      </div>

      {/* Horizontal rule */}
      <div className="border-t border-nexus-border/60 mb-5" />

      {/* Loading state */}
      {loading && (
        <div className="text-center py-16">
          <div className="text-nexus-muted text-xs uppercase tracking-widest font-mono animate-pulse">
            // Loading airframe presets ...
          </div>
        </div>
      )}

      {/* Unit grid */}
      <div className="grid grid-cols-2 xl:grid-cols-3 gap-4">
        {drones.map((drone) => (
          <DroneCard
            key={drone.id}
            drone={drone}
            isSelected={compareIds.includes(drone.id)}
            onSelect={() => handleToggleCompare(drone.id)}
            onOpen={() => handleOpenInBuilder(drone.config, drone.filename)}
          />
        ))}
      </div>

      {/* Empty state */}
      {drones.length === 0 && !loading && (
        <div className="text-center py-20">
          <div className="text-nexus-muted text-xs uppercase tracking-widest font-mono mb-1">
            // No units loaded
          </div>
          <div className="text-nexus-muted/50 text-[10px] uppercase tracking-wider font-mono mb-6">
            Select LOAD PRESETS to initialize force disposition
          </div>
          <button
            onClick={loadPresets}
            className="px-4 py-2 rounded-none border border-nexus-accent bg-nexus-accent/10 text-nexus-accent text-[11px] font-mono uppercase tracking-wider hover:bg-nexus-accent/20 transition-colors"
          >
            <Plus size={13} className="inline mr-2 -mt-px" />
            Load Presets
          </button>
        </div>
      )}
    </div>
  );
}
