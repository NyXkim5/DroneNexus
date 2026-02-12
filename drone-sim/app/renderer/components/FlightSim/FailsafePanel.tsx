import React from 'react';
import { useSimStore } from '../../stores/simStore';
import { AlertTriangle, Battery, Satellite, Radio, MapPin, Shield } from 'lucide-react';

const FAILSAFES = [
  {
    id: 'low_battery',
    label: 'Low Battery',
    description: 'Simulates battery dropping to critical level. Drone should RTL or land.',
    icon: <Battery size={16} />,
    color: '#ffaa00',
  },
  {
    id: 'gps_loss',
    label: 'GPS Loss',
    description: 'Simulates losing GPS signal. Drone should switch to altitude hold or land.',
    icon: <Satellite size={16} />,
    color: '#3388ff',
  },
  {
    id: 'rc_loss',
    label: 'RC Link Loss',
    description: 'Simulates losing radio control signal. Drone should RTL after timeout.',
    icon: <Radio size={16} />,
    color: '#ff3355',
  },
  {
    id: 'geofence',
    label: 'Geofence Breach',
    description: 'Simulates flying past the allowed boundary. Drone should stop and RTL.',
    icon: <MapPin size={16} />,
    color: '#aa55ff',
  },
];

export function FailsafePanel() {
  const { failsafeActive, triggerFailsafe, running } = useSimStore();

  return (
    <div>
      <div className="panel-header flex items-center gap-2">
        <Shield size={14} />
        Failsafe Testing
      </div>

      <div className="p-3 text-xs text-nexus-muted mb-1">
        Trigger these scenarios to test how your drone handles emergencies.
        Click again to clear the failsafe.
      </div>

      <div className="px-3 pb-3 space-y-2">
        {FAILSAFES.map((fs) => {
          const isActive = failsafeActive === fs.id;
          return (
            <button
              key={fs.id}
              onClick={() => triggerFailsafe(isActive ? null : fs.id)}
              disabled={!running}
              className={`w-full text-left px-3 py-2 rounded text-sm transition-all border ${
                isActive
                  ? 'animate-pulse'
                  : 'border-transparent hover:bg-white/5'
              } ${!running ? 'opacity-40' : ''}`}
              style={{
                borderColor: isActive ? fs.color : 'transparent',
                backgroundColor: isActive ? `${fs.color}15` : undefined,
              }}
            >
              <div className="flex items-center gap-2">
                <span style={{ color: isActive ? fs.color : '#64748b' }}>{fs.icon}</span>
                <span className="font-medium" style={{ color: isActive ? fs.color : '#e2e8f0' }}>
                  {fs.label}
                </span>
                {isActive && (
                  <span className="ml-auto text-[10px] px-2 py-0.5 rounded" style={{ color: fs.color, backgroundColor: `${fs.color}20` }}>
                    ACTIVE
                  </span>
                )}
              </div>
              <div className="text-[10px] text-nexus-muted mt-1 ml-6">{fs.description}</div>
            </button>
          );
        })}
      </div>

      {/* Wind controls */}
      <div className="px-3 pb-3 border-t border-nexus-border/50 pt-3">
        <div className="text-xs font-medium text-nexus-muted mb-2">Wind Disturbance</div>
        <WindControl />
      </div>
    </div>
  );
}

function WindControl() {
  const { windSpeed, windDirection, gustIntensity, setWind, running } = useSimStore();

  return (
    <div className="space-y-2">
      <div>
        <label className="text-[10px] text-nexus-muted flex justify-between">
          <span>Wind Speed</span>
          <span className="font-mono">{windSpeed} m/s</span>
        </label>
        <input
          type="range"
          className="w-full h-1 appearance-none bg-nexus-border rounded cursor-pointer"
          style={{ accentColor: '#00ccff' }}
          min={0} max={20} step={0.5}
          value={windSpeed}
          disabled={!running}
          onChange={(e) => setWind(parseFloat(e.target.value), windDirection, gustIntensity)}
        />
      </div>
      <div>
        <label className="text-[10px] text-nexus-muted flex justify-between">
          <span>Wind Direction</span>
          <span className="font-mono">{windDirection}&deg;</span>
        </label>
        <input
          type="range"
          className="w-full h-1 appearance-none bg-nexus-border rounded cursor-pointer"
          style={{ accentColor: '#00ccff' }}
          min={0} max={360} step={5}
          value={windDirection}
          disabled={!running}
          onChange={(e) => setWind(windSpeed, parseFloat(e.target.value), gustIntensity)}
        />
      </div>
      <div>
        <label className="text-[10px] text-nexus-muted flex justify-between">
          <span>Gust Intensity</span>
          <span className="font-mono">{gustIntensity} m/s</span>
        </label>
        <input
          type="range"
          className="w-full h-1 appearance-none bg-nexus-border rounded cursor-pointer"
          style={{ accentColor: '#ffaa00' }}
          min={0} max={10} step={0.5}
          value={gustIntensity}
          disabled={!running}
          onChange={(e) => setWind(windSpeed, windDirection, parseFloat(e.target.value))}
        />
      </div>
    </div>
  );
}
