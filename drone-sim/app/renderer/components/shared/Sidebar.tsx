import React, { useEffect, useState } from 'react';
import type { ViewId } from '../../App';
import { Wrench, Radio, Play, LayoutGrid } from 'lucide-react';

interface SidebarProps {
  activeView: ViewId;
  onViewChange: (view: ViewId) => void;
}

const NAV_ITEMS: { id: ViewId; label: string; icon: React.ReactNode; description: string }[] = [
  {
    id: 'builder',
    label: 'EQUIP CONFIG',
    icon: <Wrench size={16} />,
    description: 'Equipment Configuration',
  },
  {
    id: 'sensors',
    label: 'SENSOR ARRAY',
    icon: <Radio size={16} />,
    description: 'Sensor Noise Models',
  },
  {
    id: 'flight',
    label: 'FLIGHT OPS',
    icon: <Play size={16} />,
    description: 'Flight Operations',
  },
  {
    id: 'fleet',
    label: 'FORCE DISP',
    icon: <LayoutGrid size={16} />,
    description: 'Force Disposition',
  },
];

export function Sidebar({ activeView, onViewChange }: SidebarProps) {
  const [elapsed, setElapsed] = useState(0);

  // Mission elapsed time counter
  useEffect(() => {
    const interval = setInterval(() => {
      setElapsed((prev) => prev + 1);
    }, 1000);
    return () => clearInterval(interval);
  }, []);

  const formatMET = (totalSeconds: number) => {
    const h = Math.floor(totalSeconds / 3600);
    const m = Math.floor((totalSeconds % 3600) / 60);
    const s = totalSeconds % 60;
    return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  };

  return (
    <nav className="w-52 bg-nexus-panel border-r border-nexus-border flex flex-col">
      {/* Logo area */}
      <div className="px-4 py-4 border-b border-nexus-border">
        <div className="text-nexus-accent font-bold text-sm tracking-[0.2em] font-mono">
          NEXUS
        </div>
        <div className="text-[9px] text-nexus-muted uppercase tracking-[0.15em] mt-0.5 font-mono">
          GROUND CONTROL STATION
        </div>
      </div>

      {/* Navigation */}
      <div className="flex-1 py-2">
        {NAV_ITEMS.map((item) => (
          <button
            key={item.id}
            onClick={() => onViewChange(item.id)}
            className={`nav-item w-full text-left ${activeView === item.id ? 'active' : ''}`}
          >
            <div className="flex-shrink-0 w-5 flex items-center justify-center">
              {item.icon}
            </div>
            <div className="min-w-0">
              <div className="text-[11px] font-mono font-medium tracking-wider">{item.label}</div>
              <div className="text-[9px] text-nexus-muted font-mono tracking-wide normal-case">
                {item.description}
              </div>
            </div>
          </button>
        ))}
      </div>

      {/* Bottom section */}
      <div className="border-t border-nexus-border px-4 py-3 space-y-2">
        {/* Mission Elapsed Time */}
        <div>
          <div className="text-[9px] text-nexus-muted uppercase tracking-[0.2em] font-mono">
            MISSION ELAPSED
          </div>
          <div className="text-xs text-nexus-accent font-mono tracking-wider mt-0.5">
            {formatMET(elapsed)}
          </div>
        </div>

        {/* Connection status */}
        <div className="flex items-center gap-2">
          <div className="flex items-center gap-1">
            <span className="status-indicator active" />
            <span className="text-[9px] text-nexus-muted uppercase tracking-wider font-mono">
              SIM
            </span>
          </div>
          <div className="flex items-center gap-1">
            <span className="status-indicator active" />
            <span className="text-[9px] text-nexus-muted uppercase tracking-wider font-mono">
              GCS
            </span>
          </div>
          <div className="flex items-center gap-1">
            <span className="status-indicator active" />
            <span className="text-[9px] text-nexus-muted uppercase tracking-wider font-mono">
              LINK
            </span>
          </div>
        </div>

        {/* Version */}
        <div className="text-[9px] text-nexus-muted font-mono tracking-wider">
          v1.0.0 // BUILD 2024
        </div>
      </div>
    </nav>
  );
}
