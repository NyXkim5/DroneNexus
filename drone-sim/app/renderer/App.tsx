import React, { useState } from 'react';
import { Sidebar } from './components/shared/Sidebar';
import { Header } from './components/shared/Header';
import { StatusBar } from './components/shared/StatusBar';
import { DroneBuilder } from './components/DroneBuilder/DroneBuilder';
import { SensorConfigurator } from './components/SensorPanel/SensorConfigurator';
import { FlightSimView } from './components/FlightSim/FlightSimView';
import { FleetGrid } from './components/FleetOverview/FleetGrid';

export type ViewId = 'builder' | 'sensors' | 'flight' | 'fleet';

const VIEW_LABELS: Record<ViewId, string> = {
  builder: 'EQUIP CONFIG',
  sensors: 'SENSOR ARRAY',
  flight: 'FLIGHT OPS',
  fleet: 'FORCE DISP',
};

export function App() {
  const [activeView, setActiveView] = useState<ViewId>('builder');

  return (
    <div className="h-screen flex flex-col bg-nexus-bg text-nexus-text font-mono">
      {/* Classification banner */}
      <div className="classification-banner">
        NEXUS // TACTICAL DRONE SIMULATION SYSTEM
      </div>

      <div className="flex flex-1 overflow-hidden">
        <Sidebar activeView={activeView} onViewChange={setActiveView} />
        <div className="flex-1 flex flex-col">
          <Header activeView={VIEW_LABELS[activeView]} />
          <main className="flex-1 overflow-hidden">
            {activeView === 'builder' && <DroneBuilder />}
            {activeView === 'sensors' && <SensorConfigurator />}
            {activeView === 'flight' && <FlightSimView />}
            {activeView === 'fleet' && <FleetGrid />}
          </main>
          <StatusBar />
        </div>
      </div>

      {/* Scanline CRT overlay */}
      <div className="scanline" />
    </div>
  );
}
