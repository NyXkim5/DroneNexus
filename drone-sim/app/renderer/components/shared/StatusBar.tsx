import React from 'react';
import { useDroneStore } from '../../stores/droneStore';
import { useSimStore } from '../../stores/simStore';
import { formatWeight, formatDuration } from '../../lib/units';
import { twRating, flightTimeRating } from '../../lib/units';

export function StatusBar() {
  const { currentConfig } = useDroneStore();
  const { running, simState, flightMode, telemetry } = useSimStore();
  const { computed } = currentConfig;

  const tw = twRating(computed.thrust_to_weight);
  const ft = flightTimeRating(computed.max_flight_time_min);
  const warningCount = computed.compatibility_warnings.length;

  // System status determination
  const isCritical = warningCount > 3;
  const isCaution = warningCount > 0;

  return (
    <footer className="h-8 bg-nexus-panel border-t border-nexus-border flex items-center px-4 gap-4 text-[11px] font-mono flex-shrink-0">
      {/* Left section: Stats */}
      <div className="flex items-center gap-1.5">
        <span className="text-nexus-muted uppercase tracking-wider">AUW</span>
        <span className="text-nexus-text">[ {formatWeight(computed.all_up_weight_g)} ]</span>
      </div>

      <span className="text-nexus-border">|</span>

      <div className="flex items-center gap-1.5">
        <span className="text-nexus-muted uppercase tracking-wider">T:W</span>
        <span style={{ color: tw.color }}>&lt; {computed.thrust_to_weight}:1 &gt;</span>
        <span className="text-nexus-muted text-[9px]">({tw.label})</span>
      </div>

      <span className="text-nexus-border">|</span>

      <div className="flex items-center gap-1.5">
        <span className="text-nexus-muted uppercase tracking-wider">FLT TIME</span>
        <span style={{ color: ft.color }}>[ {formatDuration(computed.max_flight_time_min)} ]</span>
      </div>

      <span className="text-nexus-border">|</span>

      <div className="flex items-center gap-1.5">
        <span className="text-nexus-muted uppercase tracking-wider">PAYLOAD</span>
        <span className="text-nexus-text">[ {formatWeight(computed.max_payload_g)} ]</span>
      </div>

      {/* Center section: System status */}
      <div className="flex-1 flex justify-center">
        {isCritical ? (
          <span className="flex items-center gap-1.5">
            <span className="status-indicator critical" />
            <span className="text-nexus-danger uppercase tracking-[0.15em] font-bold">CRITICAL</span>
          </span>
        ) : isCaution ? (
          <span className="flex items-center gap-1.5">
            <span className="status-indicator warning" />
            <span className="text-nexus-warn uppercase tracking-[0.15em]">
              CAUTION: {warningCount} ALERT{warningCount > 1 ? 'S' : ''}
            </span>
          </span>
        ) : (
          <span className="flex items-center gap-1.5">
            <span className="status-indicator active" />
            <span className="text-nexus-accent uppercase tracking-[0.15em]">SYSTEM NOMINAL</span>
          </span>
        )}
      </div>

      {/* Right section: Sim state + Link quality */}
      <div className="flex items-center gap-3">
        {running && (
          <>
            <div className="flex items-center gap-2">
              <span className="status-indicator active" />
              <span className="text-nexus-accent uppercase tracking-wider">{simState}</span>
              <span className="text-nexus-border">|</span>
              <span className="text-nexus-text uppercase tracking-wider">{flightMode}</span>
              {telemetry.flight_time_s > 0 && (
                <>
                  <span className="text-nexus-border">|</span>
                  <span className="text-nexus-muted">
                    T+{Math.floor(telemetry.flight_time_s / 60)}:{String(Math.floor(telemetry.flight_time_s % 60)).padStart(2, '0')}
                  </span>
                </>
              )}
            </div>
            <span className="text-nexus-border">|</span>
          </>
        )}

        <div className="flex items-center gap-1.5">
          <span className="text-nexus-muted uppercase tracking-wider">RSSI</span>
          <span className={
            telemetry.rssi > 70
              ? 'text-nexus-accent'
              : telemetry.rssi > 40
                ? 'text-nexus-warn'
                : 'text-nexus-danger'
          }>
            [ {telemetry.rssi}% ]
          </span>
        </div>
      </div>
    </footer>
  );
}
