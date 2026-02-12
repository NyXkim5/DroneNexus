import React from 'react';
import { useSimStore, type FlightMode } from '../../stores/simStore';

const MODES: { id: FlightMode; label: string; description: string; color: string }[] = [
  {
    id: 'MANUAL',
    label: 'Manual',
    description: 'Direct motor control. No stabilization. Expert only.',
    color: '#ff3355',
  },
  {
    id: 'STABILIZE',
    label: 'Stabilize',
    description: 'Self-leveling when sticks centered. You control throttle.',
    color: '#ffaa00',
  },
  {
    id: 'ALT_HOLD',
    label: 'Alt Hold',
    description: 'Holds altitude automatically. You control horizontal movement.',
    color: '#88cc00',
  },
  {
    id: 'LOITER',
    label: 'Loiter',
    description: 'GPS position and altitude hold. Hands-off hovering. Requires GPS.',
    color: '#00ff88',
  },
  {
    id: 'AUTO',
    label: 'Auto',
    description: 'Follows a pre-planned waypoint mission automatically.',
    color: '#3388ff',
  },
  {
    id: 'RTL',
    label: 'Return Home',
    description: 'Flies back to launch point and lands automatically.',
    color: '#aa55ff',
  },
  {
    id: 'LAND',
    label: 'Land',
    description: 'Descends straight down and lands at current position.',
    color: '#00ccff',
  },
];

export function FlightModeSelector() {
  const { flightMode, setFlightMode, running } = useSimStore();

  return (
    <div className="border-b border-nexus-border">
      <div className="panel-header">Flight Mode</div>
      <div className="p-3 space-y-1">
        {MODES.map((mode) => (
          <button
            key={mode.id}
            onClick={() => setFlightMode(mode.id)}
            disabled={!running}
            className={`w-full text-left px-3 py-2 rounded text-sm transition-all ${
              flightMode === mode.id
                ? 'border border-current'
                : 'hover:bg-white/5 border border-transparent'
            } ${!running ? 'opacity-40' : ''}`}
            style={{
              color: flightMode === mode.id ? mode.color : '#64748b',
              backgroundColor: flightMode === mode.id ? `${mode.color}15` : undefined,
            }}
          >
            <div className="font-medium">{mode.label}</div>
            <div className="text-[10px] mt-0.5 opacity-70">{mode.description}</div>
          </button>
        ))}
      </div>
    </div>
  );
}
