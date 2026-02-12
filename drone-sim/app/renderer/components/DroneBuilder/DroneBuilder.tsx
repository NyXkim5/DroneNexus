import React from 'react';
import { ComponentPalette } from './ComponentPalette';
import { DroneCanvas3D } from './DroneCanvas3D';
import { PropertiesPanel } from './PropertiesPanel';
import { ComputedStats } from './ComputedStats';
import { CompatibilityChecker } from './CompatibilityChecker';

export function DroneBuilder() {
  return (
    <div className="h-full flex flex-col bg-nexus-bg font-mono">
      {/* Tactical Section Header */}
      <div className="flex items-center gap-3 px-4 py-2 border-b border-nexus-border bg-nexus-panel">
        <div className="w-1.5 h-4 bg-nexus-accent" />
        <span className="text-[11px] font-bold text-nexus-accent tracking-[0.2em] uppercase">
          Equipment Configuration
        </span>
        <div className="flex-1 h-px bg-nexus-border" />
        <span className="text-[9px] text-nexus-muted tracking-widest uppercase">
          GCS // Config Module
        </span>
      </div>

      <div className="flex flex-1 overflow-hidden">
        {/* Left: Component Palette */}
        <div className="w-64 border-r-2 border-nexus-accent/20 overflow-y-auto bg-nexus-panel">
          <ComponentPalette />
        </div>

        {/* Center: 3D Drone View + Compat */}
        <div className="flex-1 flex flex-col">
          <DroneCanvas3D />
          <CompatibilityChecker />
        </div>

        {/* Right: Properties Panel */}
        <div className="w-80 border-l-2 border-nexus-accent/20 overflow-y-auto bg-nexus-panel">
          <PropertiesPanel />
        </div>
      </div>

      {/* Bottom: Computed Stats Bar */}
      <ComputedStats />
    </div>
  );
}
