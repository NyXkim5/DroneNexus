import React from 'react';
import type { AirframeConfig } from '../../types/airframe';
import { formatWeight, formatDuration, formatPower, twRating, flightTimeRating } from '../../lib/units';
import { ArrowLeft } from 'lucide-react';

interface CompareViewProps {
  drone1: AirframeConfig;
  drone2: AirframeConfig;
  onBack: () => void;
}

function SectionHeader({ title }: { title: string }) {
  return (
    <div className="flex items-center gap-3 mt-6 mb-3">
      <div className="w-1 h-4 bg-nexus-accent/60" />
      <h4 className="text-[11px] text-nexus-muted uppercase tracking-[0.2em] font-bold">
        {title}
      </h4>
      <div className="flex-1 border-t border-nexus-border/40" />
    </div>
  );
}

function CompareRow({ label, val1, val2, unit, higherIsBetter = true, tooltip }: {
  label: string;
  val1: number | string;
  val2: number | string;
  unit?: string;
  higherIsBetter?: boolean;
  tooltip?: string;
}) {
  const n1 = typeof val1 === 'number' ? val1 : parseFloat(val1);
  const n2 = typeof val2 === 'number' ? val2 : parseFloat(val2);
  const bothNumbers = !isNaN(n1) && !isNaN(n2);

  let color1 = '#d4d4d4';
  let color2 = '#d4d4d4';

  if (bothNumbers && n1 !== n2) {
    const better = higherIsBetter ? (n1 > n2 ? '1' : '2') : (n1 < n2 ? '1' : '2');
    color1 = better === '1' ? '#4ade80' : '#ef4444';
    color2 = better === '2' ? '#4ade80' : '#ef4444';
  }

  return (
    <div className="grid grid-cols-3 gap-4 py-2 border-b border-nexus-border/20 text-[12px] group hover:bg-nexus-surface/30 transition-colors">
      <div className="font-mono text-right pr-2" style={{ color: color1 }}>
        [ {val1}{unit ? ` ${unit}` : ''} ]
      </div>
      <div className="text-center text-nexus-muted text-[10px] uppercase tracking-wider flex items-center justify-center gap-1">
        {label}
        {tooltip && (
          <span className="text-nexus-muted/30 group-hover:text-nexus-muted/60 text-[9px] lowercase">
            ({tooltip})
          </span>
        )}
      </div>
      <div className="font-mono pl-2" style={{ color: color2 }}>
        [ {val2}{unit ? ` ${unit}` : ''} ]
      </div>
    </div>
  );
}

export function CompareView({ drone1, drone2, onBack }: CompareViewProps) {
  const c1 = drone1.computed;
  const c2 = drone2.computed;

  return (
    <div className="h-full overflow-y-auto bg-nexus-bg p-6 font-mono">
      {/* Back button */}
      <button
        onClick={onBack}
        className="flex items-center gap-2 mb-5 px-3 py-1.5 rounded-none border border-nexus-border bg-nexus-surface text-nexus-muted text-[11px] font-mono uppercase tracking-wider hover:border-nexus-text/30 hover:text-nexus-text transition-colors"
      >
        <ArrowLeft size={13} />
        Back to Force Disposition
      </button>

      {/* Title */}
      <div className="flex items-center gap-3 mb-5">
        <div className="w-1 h-6 bg-nexus-accent" />
        <h2 className="text-sm font-bold text-nexus-text uppercase tracking-[0.2em]">
          Unit Comparison
        </h2>
      </div>

      <div className="border-t border-nexus-border/60 mb-5" />

      {/* Unit header: Name vs Name */}
      <div className="grid grid-cols-3 gap-4 mb-6">
        <div className="text-right pr-2">
          <h3 className="text-base font-bold text-nexus-accent uppercase tracking-wider">{drone1.name}</h3>
          <p className="text-[10px] text-nexus-muted uppercase tracking-wider mt-0.5">
            {drone1.category?.toUpperCase().replace(/_/g, ' ')} // {drone1.frame.type?.toUpperCase()}
          </p>
        </div>
        <div className="flex items-center justify-center">
          <span className="text-nexus-muted/50 text-xs font-bold uppercase tracking-[0.3em]">VS</span>
        </div>
        <div className="pl-2">
          <h3 className="text-base font-bold text-nexus-info uppercase tracking-wider">{drone2.name}</h3>
          <p className="text-[10px] text-nexus-muted uppercase tracking-wider mt-0.5">
            {drone2.category?.toUpperCase().replace(/_/g, ' ')} // {drone2.frame.type?.toUpperCase()}
          </p>
        </div>
      </div>

      {/* Comparison table */}
      <div className="bg-nexus-panel border border-nexus-border rounded-none border-l-2 border-l-nexus-accent/40 p-5">
        <SectionHeader title="Performance" />
        <CompareRow label="All-Up Weight" val1={c1.all_up_weight_g} val2={c2.all_up_weight_g} unit="g" higherIsBetter={false} tooltip="lighter is better" />
        <CompareRow label="Thrust:Weight" val1={c1.thrust_to_weight} val2={c2.thrust_to_weight} tooltip="higher is more agile" />
        <CompareRow label="Max Speed" val1={c1.max_speed_ms} val2={c2.max_speed_ms} unit="m/s" />
        <CompareRow label="Flight Time" val1={c1.max_flight_time_min} val2={c2.max_flight_time_min} unit="min" />
        <CompareRow label="Max Payload" val1={c1.max_payload_g} val2={c2.max_payload_g} unit="g" />
        <CompareRow label="Hover Throttle" val1={c1.hover_throttle_pct} val2={c2.hover_throttle_pct} unit="%" higherIsBetter={false} tooltip="lower = more efficient" />

        <SectionHeader title="Power System" />
        <CompareRow label="Battery" val1={`${drone1.battery.cells}S ${drone1.battery.capacity_mah}`} val2={`${drone2.battery.cells}S ${drone2.battery.capacity_mah}`} unit="mAh" />
        <CompareRow label="Energy" val1={c1.energy_capacity_wh} val2={c2.energy_capacity_wh} unit="Wh" />
        <CompareRow label="Hover Power" val1={c1.power_at_hover_w} val2={c2.power_at_hover_w} unit="W" higherIsBetter={false} />
        <CompareRow label="Max Power" val1={c1.power_at_max_w} val2={c2.power_at_max_w} unit="W" />

        <SectionHeader title="Airframe" />
        <CompareRow label="Motors" val1={`${drone1.motors.count}x ${drone1.motors.kv}KV`} val2={`${drone2.motors.count}x ${drone2.motors.kv}KV`} />
        <CompareRow label="Props" val1={`${drone1.propellers.size_inch}"x${drone1.propellers.pitch_inch}`} val2={`${drone2.propellers.size_inch}"x${drone2.propellers.pitch_inch}`} />
        <CompareRow label="Frame" val1={`${drone1.frame.wheelbase_mm}mm ${drone1.frame.layout}`} val2={`${drone2.frame.wheelbase_mm}mm ${drone2.frame.layout}`} />
        <CompareRow label="Firmware" val1={drone1.electronics.flight_controller.firmware} val2={drone2.electronics.flight_controller.firmware} />

        <SectionHeader title="Sensor Suite" />
        <CompareRow label="GPS" val1={drone1.sensors.gps.present ? 'Yes' : 'No'} val2={drone2.sensors.gps.present ? 'Yes' : 'No'} />
        <CompareRow label="Compass" val1={drone1.sensors.magnetometer.present ? 'Yes' : 'No'} val2={drone2.sensors.magnetometer.present ? 'Yes' : 'No'} />
        <CompareRow label="LiDAR" val1={drone1.sensors.lidar.present ? 'Yes' : 'No'} val2={drone2.sensors.lidar.present ? 'Yes' : 'No'} />
        <CompareRow label="Camera FOV" val1={drone1.sensors.camera.fov_deg} val2={drone2.sensors.camera.fov_deg} unit="deg" />
      </div>
    </div>
  );
}
