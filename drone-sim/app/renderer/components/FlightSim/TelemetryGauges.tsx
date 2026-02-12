import React from 'react';
import { useSimStore } from '../../stores/simStore';

function Gauge({ label, value, unit, min, max, color, warning, critical }: {
  label: string;
  value: number;
  unit: string;
  min: number;
  max: number;
  color: string;
  warning?: number;
  critical?: number;
}) {
  const pct = Math.max(0, Math.min(100, ((value - min) / (max - min)) * 100));

  let barColor = color;
  if (critical !== undefined && value <= critical) barColor = '#ff3355';
  else if (warning !== undefined && value <= warning) barColor = '#ffaa00';

  return (
    <div className="px-4 py-2">
      <div className="flex justify-between items-baseline mb-1">
        <span className="text-xs text-nexus-muted">{label}</span>
        <span className="text-sm font-mono" style={{ color: barColor }}>
          {typeof value === 'number' ? value.toFixed(1) : value} {unit}
        </span>
      </div>
      <div className="slider-track">
        <div
          className="slider-fill"
          style={{ width: `${pct}%`, backgroundColor: barColor }}
        />
      </div>
    </div>
  );
}

export function TelemetryGauges() {
  const { telemetry, running } = useSimStore();

  if (!running) {
    return (
      <div className="p-6 text-center text-sm text-nexus-muted">
        Start the simulation to see live telemetry
      </div>
    );
  }

  return (
    <div className="border-b border-nexus-border">
      <div className="panel-header">Telemetry</div>

      <Gauge label="Altitude AGL" value={telemetry.alt_agl} unit="m" min={0} max={500} color="#00ff88" />
      <Gauge label="Ground Speed" value={telemetry.ground_speed} unit="m/s" min={0} max={50} color="#3388ff" />
      <Gauge label="Vertical Speed" value={telemetry.vertical_speed} unit="m/s" min={-10} max={10} color="#00ccff" />

      <div className="px-4 py-1 border-t border-nexus-border/50" />

      <Gauge
        label="Battery Voltage"
        value={telemetry.battery_voltage}
        unit="V"
        min={18} max={26}
        color="#00ff88"
        warning={20.4}
        critical={19.2}
      />
      <Gauge
        label="Battery Remaining"
        value={telemetry.battery_remaining_pct}
        unit="%"
        min={0} max={100}
        color="#00ff88"
        warning={25}
        critical={10}
      />
      <Gauge label="Current Draw" value={telemetry.battery_current} unit="A" min={0} max={100} color="#ffaa00" />

      <div className="px-4 py-1 border-t border-nexus-border/50" />

      <Gauge
        label="GPS Satellites"
        value={telemetry.gps_satellites}
        unit=""
        min={0} max={20}
        color="#3388ff"
      />
      <Gauge label="HDOP" value={telemetry.gps_hdop} unit="" min={0} max={5} color="#3388ff" />
      <Gauge label="Signal Strength" value={telemetry.rssi} unit="%" min={0} max={100} color="#00ff88" warning={50} critical={25} />

      <div className="px-4 py-1 border-t border-nexus-border/50" />

      <div className="px-4 py-2 grid grid-cols-3 gap-2 text-xs">
        <div>
          <div className="text-nexus-muted">Heading</div>
          <div className="font-mono">{telemetry.heading.toFixed(0)}&deg;</div>
        </div>
        <div>
          <div className="text-nexus-muted">Roll</div>
          <div className="font-mono">{telemetry.roll.toFixed(1)}&deg;</div>
        </div>
        <div>
          <div className="text-nexus-muted">Pitch</div>
          <div className="font-mono">{telemetry.pitch.toFixed(1)}&deg;</div>
        </div>
      </div>

      <div className="px-4 py-2 flex items-center gap-2 text-xs border-t border-nexus-border/50">
        <span className="text-nexus-muted">GPS Fix:</span>
        <span className={`font-mono ${
          telemetry.gps_fix_type === '3D' || telemetry.gps_fix_type === '3D-RTK' ? 'text-nexus-accent' :
          telemetry.gps_fix_type === '2D' ? 'text-nexus-warn' : 'text-nexus-danger'
        }`}>
          {telemetry.gps_fix_type}
        </span>
        <span className="text-nexus-muted ml-auto">Flight: {Math.floor(telemetry.flight_time_s / 60)}:{String(Math.floor(telemetry.flight_time_s % 60)).padStart(2, '0')}</span>
      </div>
    </div>
  );
}
