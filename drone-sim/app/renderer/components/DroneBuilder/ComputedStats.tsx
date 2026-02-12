import React from 'react';
import { useDroneStore } from '../../stores/droneStore';
import { formatWeight, formatDuration, formatPower, formatCurrent, twRating, flightTimeRating } from '../../lib/units';

export function ComputedStats() {
  const { currentConfig } = useDroneStore();
  const { computed } = currentConfig;

  const tw = twRating(computed.thrust_to_weight);
  const ft = flightTimeRating(computed.max_flight_time_min);

  // Map rating colors to tactical palette
  const mapColor = (color: string) => {
    const colorMap: Record<string, string> = {
      '#e2e8f0': '#d4d4d4',
      '#ff3355': '#ef4444',
      '#ffaa00': '#f59e0b',
      '#00ff88': '#4ade80',
    };
    return colorMap[color] || color;
  };

  const stats = [
    {
      label: 'All-Up Weight',
      value: formatWeight(computed.all_up_weight_g),
      color: '#d4d4d4',
      tooltip: 'Total weight of the drone with all components and battery',
    },
    {
      label: 'Thrust:Weight',
      value: `${computed.thrust_to_weight}:1`,
      sub: tw.label,
      color: mapColor(tw.color),
      tooltip: tw.description,
    },
    {
      label: 'Max Flight Time',
      value: formatDuration(computed.max_flight_time_min),
      sub: ft.label,
      color: mapColor(ft.color),
      tooltip: 'Estimated hover time with 80% battery usage',
    },
    {
      label: 'Max Payload',
      value: formatWeight(computed.max_payload_g),
      color: computed.max_payload_g > 0 ? '#d4d4d4' : '#ef4444',
      tooltip: 'Maximum additional weight before T:W drops below 2:1',
    },
    {
      label: 'Max Speed',
      value: `${computed.max_speed_ms} m/s`,
      color: '#d4d4d4',
      tooltip: 'Estimated maximum forward speed',
    },
    {
      label: 'Hover Power',
      value: formatPower(computed.power_at_hover_w),
      color: '#d4d4d4',
      tooltip: 'Power consumption while hovering',
    },
    {
      label: 'Energy',
      value: `${computed.energy_capacity_wh} Wh`,
      color: '#d4d4d4',
      tooltip: 'Total battery energy capacity',
    },
    {
      label: 'Hover Throttle',
      value: `${computed.hover_throttle_pct}%`,
      color: computed.hover_throttle_pct > 50 ? '#ef4444' : computed.hover_throttle_pct > 30 ? '#f59e0b' : '#4ade80',
      tooltip: 'Throttle percentage needed to hover — lower is better',
    },
  ];

  return (
    <div
      className="h-20 border-t-2 border-nexus-accent/20 bg-nexus-panel flex items-center px-4 gap-6 overflow-x-auto font-mono relative"
      style={{
        backgroundImage: `
          linear-gradient(rgba(74, 222, 128, 0.03) 1px, transparent 1px),
          linear-gradient(90deg, rgba(74, 222, 128, 0.03) 1px, transparent 1px)
        `,
        backgroundSize: '20px 20px',
      }}
    >
      {/* Leading label */}
      <div className="flex-shrink-0 flex items-center gap-2">
        <div className="w-1 h-6 bg-nexus-accent/40" />
        <span className="text-[8px] text-nexus-accent/50 tracking-[0.25em] uppercase">
          HUD
        </span>
      </div>

      {stats.map((stat) => (
        <div key={stat.label} className="flex-shrink-0 group relative">
          <div
            className="text-base font-bold tracking-wider"
            style={{
              color: stat.color,
              textShadow: stat.color !== '#d4d4d4' ? `0 0 8px ${stat.color}40` : 'none',
            }}
          >
            {'\u00AB'} {stat.value} {'\u00BB'}
          </div>
          <div className="text-[8px] text-nexus-muted tracking-[0.2em] uppercase flex items-center gap-1">
            {stat.label}
            {stat.sub && (
              <span
                className="text-[8px] px-1.5 py-px font-bold tracking-[0.1em] uppercase rounded-none border"
                style={{
                  color: stat.color,
                  borderColor: `${stat.color}40`,
                  backgroundColor: `${stat.color}10`,
                }}
              >
                {stat.sub}
              </span>
            )}
          </div>
          {stat.tooltip && (
            <div className="absolute hidden group-hover:block bottom-full left-0 mb-2 w-48 p-2 bg-nexus-bg border border-nexus-accent/30 text-[9px] text-nexus-text font-mono tracking-wide z-50">
              {stat.tooltip}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
