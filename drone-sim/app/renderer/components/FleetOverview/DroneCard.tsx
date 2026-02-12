import React from 'react';
import { formatWeight, formatDuration, twRating, flightTimeRating } from '../../lib/units';
import { Pencil, Trash2, Check } from 'lucide-react';
import { useFleetStore } from '../../stores/fleetStore';
import type { AirframeConfig } from '../../types/airframe';

interface DroneCardProps {
  drone: { id: string; filename: string; config: AirframeConfig };
  isSelected: boolean;
  onSelect: () => void;
  onOpen: () => void;
}

export function DroneCard({ drone, isSelected, onSelect, onOpen }: DroneCardProps) {
  const { removeDrone } = useFleetStore();
  const { config } = drone;
  const { computed } = config;
  const tw = twRating(computed.thrust_to_weight);
  const ft = flightTimeRating(computed.max_flight_time_min);

  const frameDesignation = {
    quadcopter: 'QUAD-X',
    hexacopter: 'HEX-X',
    octocopter: 'OCTO-X',
    fixed_wing: 'FXWG-1',
    vtol: 'VTOL-X',
  }[config.frame.type] || 'UAS-X';

  return (
    <div
      className={`relative bg-nexus-panel border font-mono transition-all cursor-pointer rounded-none ${
        isSelected
          ? 'border-nexus-accent shadow-[0_0_12px_rgba(74,222,128,0.12)] border-l-2 border-l-nexus-accent'
          : 'border-nexus-border hover:border-nexus-border/80 border-l-2 border-l-nexus-accent/40'
      }`}
      onClick={onSelect}
    >
      {/* Unit header */}
      <div className="px-4 pt-3 pb-2">
        <div className="flex items-start justify-between mb-2">
          <div className="flex items-center gap-2">
            <span className="text-[10px] px-2 py-0.5 rounded-none bg-nexus-accent/15 text-nexus-accent font-mono font-bold uppercase tracking-wider border border-nexus-accent/30">
              {frameDesignation}
            </span>
            {isSelected && (
              <span className="flex items-center gap-1 text-[10px] text-nexus-accent uppercase tracking-wider">
                <Check size={11} strokeWidth={3} />
                SEL
              </span>
            )}
          </div>
          <span className="text-[9px] text-nexus-muted uppercase tracking-wider">
            {config.category?.toUpperCase().replace(/_/g, ' ')}
          </span>
        </div>

        {/* Unit name */}
        <h3 className="text-sm font-bold text-nexus-text tracking-wide uppercase">
          {config.name}
        </h3>
        <p className="text-[10px] text-nexus-muted/70 mt-0.5 line-clamp-2 leading-relaxed">
          {config.description}
        </p>
      </div>

      {/* Divider */}
      <div className="border-t border-nexus-border/40 mx-3" />

      {/* Stats grid 2x3 */}
      <div className="grid grid-cols-3 gap-x-3 gap-y-2 px-4 py-3 text-[11px]">
        <div>
          <div className="text-[9px] text-nexus-muted uppercase tracking-wider mb-0.5">Mass</div>
          <div className="font-mono text-nexus-text">[ {formatWeight(computed.all_up_weight_g)} ]</div>
        </div>
        <div>
          <div className="text-[9px] text-nexus-muted uppercase tracking-wider mb-0.5">T:W</div>
          <div className="font-mono" style={{ color: tw.color }}>[ {computed.thrust_to_weight}:1 ]</div>
        </div>
        <div>
          <div className="text-[9px] text-nexus-muted uppercase tracking-wider mb-0.5">Endurance</div>
          <div className="font-mono" style={{ color: ft.color }}>[ {formatDuration(computed.max_flight_time_min)} ]</div>
        </div>
        <div>
          <div className="text-[9px] text-nexus-muted uppercase tracking-wider mb-0.5">Payload</div>
          <div className="font-mono text-nexus-text">[ {formatWeight(computed.max_payload_g)} ]</div>
        </div>
        <div>
          <div className="text-[9px] text-nexus-muted uppercase tracking-wider mb-0.5">Power</div>
          <div className="font-mono text-nexus-text">[ {config.battery.cells}S {config.battery.capacity_mah} ]</div>
        </div>
        <div>
          <div className="text-[9px] text-nexus-muted uppercase tracking-wider mb-0.5">Motors</div>
          <div className="font-mono text-nexus-text">[ {config.motors.count}x {config.motors.kv}KV ]</div>
        </div>
      </div>

      {/* Divider */}
      <div className="border-t border-nexus-border/40 mx-3" />

      {/* Hardware tags */}
      <div className="flex flex-wrap gap-1.5 px-4 py-2.5">
        {config.sensors.gps.present && (
          <span className="text-[9px] px-1.5 py-0.5 rounded-none border border-nexus-info/30 bg-nexus-info/8 text-nexus-info uppercase tracking-wider font-bold">
            GPS
          </span>
        )}
        {config.sensors.magnetometer.present && (
          <span className="text-[9px] px-1.5 py-0.5 rounded-none border border-nexus-info/30 bg-nexus-info/8 text-nexus-info uppercase tracking-wider font-bold">
            MAG
          </span>
        )}
        {config.sensors.lidar.present && (
          <span className="text-[9px] px-1.5 py-0.5 rounded-none border border-nexus-info/30 bg-nexus-info/8 text-nexus-info uppercase tracking-wider font-bold">
            LIDAR
          </span>
        )}
        {config.sensors.optical_flow?.present && (
          <span className="text-[9px] px-1.5 py-0.5 rounded-none border border-nexus-info/30 bg-nexus-info/8 text-nexus-info uppercase tracking-wider font-bold">
            OPTFLOW
          </span>
        )}
        <span className="text-[9px] px-1.5 py-0.5 rounded-none border border-nexus-muted/20 bg-nexus-muted/5 text-nexus-muted uppercase tracking-wider">
          {config.electronics.flight_controller.firmware}
        </span>
      </div>

      {/* Warnings */}
      {computed.compatibility_warnings.length > 0 && (
        <div className="px-4 pb-2">
          <span className="text-[10px] text-nexus-warn font-mono uppercase tracking-wider">
            {'\u26A0'} {computed.compatibility_warnings.length} ALERT{computed.compatibility_warnings.length > 1 ? 'S' : ''}
          </span>
        </div>
      )}

      {/* Action buttons */}
      <div className="flex border-t border-nexus-border/50">
        <button
          onClick={(e) => { e.stopPropagation(); onOpen(); }}
          className="flex-1 flex items-center justify-center gap-1.5 py-2 text-[10px] text-nexus-muted uppercase tracking-wider font-mono hover:bg-nexus-surface hover:text-nexus-text transition-colors border-r border-nexus-border/50"
        >
          <Pencil size={11} /> CONFIG
        </button>
        <button
          onClick={(e) => { e.stopPropagation(); removeDrone(drone.id); }}
          className="flex-1 flex items-center justify-center gap-1.5 py-2 text-[10px] text-nexus-danger/70 uppercase tracking-wider font-mono hover:bg-nexus-danger/5 hover:text-nexus-danger transition-colors"
        >
          <Trash2 size={11} /> REMOVE
        </button>
      </div>
    </div>
  );
}
