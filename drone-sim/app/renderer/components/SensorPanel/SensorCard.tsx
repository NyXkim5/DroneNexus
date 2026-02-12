import React, { useState } from 'react';
import { useSensorStore } from '../../stores/sensorStore';
import { NoisePreview } from './NoisePreview';
import { Power, ChevronDown } from 'lucide-react';
import type { NoisePresetLevel } from '../../lib/sensors';

interface SensorCardProps {
  sensorType: string;
  name: string;
  description: string;
  enabled: boolean;
}

const PRESET_COLORS: Record<string, string> = {
  perfect: '#4ade80',
  typical: '#60a5fa',
  noisy: '#f59e0b',
  failing: '#ef4444',
};

const PRESET_LABELS: Record<string, string> = {
  perfect: 'Laboratory',
  typical: 'Operational',
  noisy: 'Degraded',
  failing: 'Compromised',
};

export function SensorCard({ sensorType, name, description, enabled }: SensorCardProps) {
  const { activePresets, toggleSensor, setPreset } = useSensorStore();
  const [showTooltip, setShowTooltip] = useState(false);
  const activePreset = activePresets[sensorType] ?? 'typical';

  return (
    <div
      className={`bg-nexus-panel rounded-none font-mono transition-colors ${
        enabled
          ? 'border border-nexus-accent/30 border-l-2 border-l-nexus-accent'
          : 'border border-nexus-border border-l-2 border-l-nexus-muted/30'
      }`}
    >
      <div className="p-3">
        {/* Header: name + status + power toggle */}
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2 min-w-0">
            <div
              className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${
                enabled ? 'bg-nexus-accent animate-pulse' : 'bg-nexus-muted/40'
              }`}
            />
            <h3 className="text-[11px] font-bold text-nexus-text tracking-[0.1em] uppercase truncate">
              {name}
            </h3>
          </div>
          <button
            onClick={() => toggleSensor(sensorType)}
            className={`flex-shrink-0 px-2 py-1 rounded-none text-[9px] font-bold tracking-[0.15em] uppercase border transition-colors ${
              enabled
                ? 'bg-nexus-accent/15 border-nexus-accent/40 text-nexus-accent'
                : 'bg-nexus-bg border-nexus-border text-nexus-muted'
            }`}
            title={enabled ? 'Disable sensor' : 'Enable sensor'}
          >
            <div className="flex items-center gap-1">
              <Power size={10} />
              <span>{enabled ? 'ON' : 'OFF'}</span>
            </div>
          </button>
        </div>

        {/* Status line */}
        <div className="text-[9px] text-nexus-muted/60 tracking-[0.15em] uppercase mb-2">
          Status: {enabled ? 'ONLINE' : 'OFFLINE'}
        </div>

        {/* Description tooltip */}
        <div className="relative mb-3">
          <button
            className="text-[10px] text-nexus-accent/60 tracking-wide uppercase hover:text-nexus-accent transition-colors"
            onClick={() => setShowTooltip(!showTooltip)}
          >
            [{showTooltip ? 'Hide' : 'Show'} Intel]
          </button>
          {showTooltip && (
            <div className="mt-2 p-3 bg-nexus-bg border border-nexus-accent/20 border-l-2 border-l-nexus-accent/50 rounded-none text-[10px] text-nexus-text leading-relaxed tracking-wide">
              {description}
            </div>
          )}
        </div>

        {/* Noise Preset Selector */}
        {enabled && (
          <>
            <div className="mb-3">
              <label className="text-[9px] text-nexus-muted tracking-[0.2em] uppercase mb-1.5 block">
                Signal Condition
              </label>
              <div className="grid grid-cols-4 gap-1">
                {(['perfect', 'typical', 'noisy', 'failing'] as NoisePresetLevel[]).map((preset) => (
                  <button
                    key={preset}
                    onClick={() => setPreset(sensorType, preset)}
                    className={`px-1 py-1 rounded-none text-[8px] font-bold tracking-[0.1em] uppercase transition-colors border ${
                      activePreset === preset
                        ? 'border-current'
                        : 'border-nexus-border bg-nexus-bg'
                    }`}
                    style={{
                      color: activePreset === preset ? PRESET_COLORS[preset] : '#737373',
                      backgroundColor: activePreset === preset ? `${PRESET_COLORS[preset]}12` : undefined,
                    }}
                  >
                    {PRESET_LABELS[preset]}
                  </button>
                ))}
              </div>
            </div>

            {/* Noise Preview */}
            <NoisePreview sensorType={sensorType} preset={activePreset} />
          </>
        )}
      </div>
    </div>
  );
}
