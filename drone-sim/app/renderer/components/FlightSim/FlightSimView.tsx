import React from 'react';
import { TelemetryGauges } from './TelemetryGauges';
import { PIDTuner } from './PIDTuner';
import { FlightModeSelector } from './FlightModeSelector';
import { FailsafePanel } from './FailsafePanel';
import { WorldRenderer } from './WorldRenderer';
import { useSimStore } from '../../stores/simStore';
import { Play, Square, RotateCcw } from 'lucide-react';
import { useSimLoop } from '../../lib/simLoop';

export function FlightSimView() {
  const { running, simState, startSim, stopSim, setSimState } = useSimStore();
  useSimLoop();

  const handleArm = () => {
    if (simState === 'IDLE') setSimState('ARMED');
  };

  const handleTakeoff = () => {
    if (simState === 'ARMED') setSimState('TAKING_OFF');
  };

  const handleLand = () => {
    if (['FLYING', 'TAKING_OFF'].includes(simState)) setSimState('LANDING');
  };

  return (
    <div className="h-full flex flex-col">
      {/* Toolbar */}
      <div className="h-12 border-b border-nexus-border bg-nexus-panel flex items-center px-4 gap-3">
        <button
          onClick={running ? stopSim : startSim}
          className={running ? 'btn-danger' : 'btn-primary'}
        >
          {running ? <><Square size={14} /> Stop Sim</> : <><Play size={14} /> Start Sim</>}
        </button>

        {running && (
          <>
            <div className="w-px h-6 bg-nexus-border" />
            <button onClick={handleArm} className="btn-ghost" disabled={simState !== 'IDLE'}>
              Arm
            </button>
            <button onClick={handleTakeoff} className="btn-ghost" disabled={simState !== 'ARMED'}>
              Takeoff
            </button>
            <button onClick={handleLand} className="btn-ghost" disabled={!['FLYING', 'TAKING_OFF'].includes(simState)}>
              Land
            </button>
            <button onClick={() => setSimState('IDLE')} className="btn-ghost">
              <RotateCcw size={14} /> Reset
            </button>

            <div className="flex-1" />

            <div className="flex items-center gap-2 text-sm">
              <span className={`w-2 h-2 rounded-full ${
                simState === 'FLYING' ? 'bg-nexus-accent animate-pulse' :
                simState === 'EMERGENCY' ? 'bg-nexus-danger animate-pulse' :
                'bg-nexus-muted'
              }`} />
              <span className="font-mono text-nexus-text">{simState}</span>
            </div>
          </>
        )}
      </div>

      <div className="flex flex-1 overflow-hidden">
        {/* Left panel: Telemetry + Controls */}
        <div className="w-80 border-r border-nexus-border overflow-y-auto">
          <TelemetryGauges />
          <FlightModeSelector />
          <FailsafePanel />
        </div>

        {/* Center: 3D World View */}
        <div className="flex-1">
          <WorldRenderer />
        </div>

        {/* Right panel: PID Tuner */}
        <div className="w-72 border-l border-nexus-border overflow-y-auto">
          <PIDTuner />
        </div>
      </div>
    </div>
  );
}
