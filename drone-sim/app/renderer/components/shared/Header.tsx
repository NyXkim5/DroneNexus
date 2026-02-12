import React, { useEffect, useState } from 'react';
import { useDroneStore } from '../../stores/droneStore';
import { useSimStore } from '../../stores/simStore';
import { Save, FolderOpen, FilePlus, Download, Upload } from 'lucide-react';

interface HeaderProps {
  activeView: string;
}

export function Header({ activeView }: HeaderProps) {
  const { currentConfig, isDirty, filename, loadConfig, createNew, markClean } = useDroneStore();
  const { telemetry } = useSimStore();
  const [airframes, setAirframes] = useState<string[]>([]);
  const [showDropdown, setShowDropdown] = useState(false);
  const [utcTime, setUtcTime] = useState('');

  // Live UTC clock
  useEffect(() => {
    const tick = () => {
      const now = new Date();
      setUtcTime(
        now.toISOString().slice(11, 19) + 'Z'
      );
    };
    tick();
    const interval = setInterval(tick, 1000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    window.api?.listAirframes().then(setAirframes).catch(() => {});
  }, []);

  const handleLoad = async (file: string) => {
    const config = await window.api.loadAirframe(file);
    loadConfig(config, file);
    setShowDropdown(false);
  };

  const handleSave = async () => {
    if (filename) {
      await window.api.saveAirframe(filename, currentConfig);
      markClean();
    }
  };

  const handleExport = async () => {
    await window.api.exportAirframe(currentConfig);
  };

  const handleImport = async () => {
    const config = await window.api.importAirframe();
    if (config) loadConfig(config);
  };

  // Determine system status
  const warningCount = currentConfig.computed.compatibility_warnings.length;
  const sysNominal = warningCount === 0;

  // GPS coordinates from telemetry
  const lat = telemetry.lat.toFixed(4);
  const lon = Math.abs(telemetry.lon).toFixed(4);
  const latDir = telemetry.lat >= 0 ? 'N' : 'S';
  const lonDir = telemetry.lon >= 0 ? 'E' : 'W';

  return (
    <header
      className="h-10 bg-nexus-panel border-b border-nexus-border flex items-center px-4 gap-4 text-xs font-mono"
      style={{ WebkitAppRegion: 'drag' } as React.CSSProperties}
    >
      {/* Left section: GCS + Operator */}
      <div
        className="flex items-center gap-3"
        style={{ WebkitAppRegion: 'no-drag' } as React.CSSProperties}
      >
        <span className="text-nexus-accent font-bold tracking-wider">GCS</span>
        <span className="text-nexus-border">|</span>
        <span className="text-nexus-muted uppercase tracking-wider">OPERATOR-01</span>
      </div>

      {/* Center section: View + Airframe */}
      <div className="flex-1 flex items-center justify-center gap-3">
        <span className="text-nexus-muted uppercase tracking-[0.15em]">{activeView}</span>
        <span className="text-nexus-border">//</span>
        <span className="text-nexus-text uppercase tracking-wider">
          {currentConfig.name}
          {isDirty && <span className="text-nexus-warn ml-1">*</span>}
        </span>
      </div>

      {/* Right section: Clock, GPS, System Status */}
      <div
        className="flex items-center gap-3"
        style={{ WebkitAppRegion: 'no-drag' } as React.CSSProperties}
      >
        {/* UTC Clock */}
        <span className="text-nexus-accent tracking-wider">{utcTime}</span>

        <span className="text-nexus-border">|</span>

        {/* GPS Coordinates */}
        <span className="text-nexus-muted">
          {lat}&deg;{latDir} {lon}&deg;{lonDir}
        </span>

        <span className="text-nexus-border">|</span>

        {/* System Status */}
        {sysNominal ? (
          <span className="flex items-center gap-1.5">
            <span className="status-indicator active" />
            <span className="text-nexus-accent uppercase tracking-wider">SYS NOMINAL</span>
          </span>
        ) : (
          <span className="flex items-center gap-1.5">
            <span className="status-indicator warning" />
            <span className="text-nexus-warn uppercase tracking-wider">SYS CAUTION</span>
          </span>
        )}

        <span className="text-nexus-border">|</span>

        {/* Action buttons */}
        <div className="flex items-center gap-1">
          <button onClick={createNew} className="btn-ghost p-1.5" title="New config">
            <FilePlus size={14} />
          </button>

          <div className="relative">
            <button
              onClick={() => setShowDropdown(!showDropdown)}
              className="btn-ghost p-1.5"
              title="Open airframe"
            >
              <FolderOpen size={14} />
            </button>
            {showDropdown && (
              <div className="absolute right-0 top-full mt-1 w-64 bg-nexus-panel border border-nexus-border border-l-2 border-l-nexus-accent shadow-xl z-50">
                <div className="panel-header">AIRFRAME PRESETS</div>
                <div className="max-h-64 overflow-y-auto">
                  {airframes.map((file) => (
                    <button
                      key={file}
                      onClick={() => handleLoad(file)}
                      className="w-full text-left px-4 py-2 text-xs font-mono uppercase tracking-wider hover:bg-white/5 transition-colors text-nexus-text"
                    >
                      {file.replace(/\.ya?ml$/, '')}
                    </button>
                  ))}
                  {airframes.length === 0 && (
                    <div className="px-4 py-3 text-xs text-nexus-muted font-mono">
                      NO AIRFRAME FILES FOUND
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>

          <button
            onClick={handleSave}
            className="btn-ghost p-1.5"
            title="Save"
            disabled={!isDirty}
          >
            <Save size={14} className={isDirty ? 'text-nexus-accent' : ''} />
          </button>

          <button onClick={handleImport} className="btn-ghost p-1.5" title="Import YAML">
            <Upload size={14} />
          </button>

          <button onClick={handleExport} className="btn-ghost p-1.5" title="Export YAML">
            <Download size={14} />
          </button>
        </div>
      </div>
    </header>
  );
}
