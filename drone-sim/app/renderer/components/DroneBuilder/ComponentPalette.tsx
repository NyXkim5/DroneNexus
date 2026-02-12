import React, { useState } from 'react';
import { useDroneStore } from '../../stores/droneStore';
import { ChevronDown, ChevronRight, Box, Cpu, Battery, Radio, Zap, Eye, Cog } from 'lucide-react';

interface ComponentCategory {
  id: string;
  label: string;
  icon: React.ReactNode;
  items: { id: string; label: string; field: string }[];
}

const CATEGORIES: ComponentCategory[] = [
  {
    id: 'frame',
    label: 'Frame',
    icon: <Box size={16} />,
    items: [
      { id: 'frame-type', label: 'Frame Type', field: 'frame.type' },
      { id: 'frame-layout', label: 'Layout', field: 'frame.layout' },
      { id: 'frame-size', label: 'Size & Weight', field: 'frame' },
    ],
  },
  {
    id: 'motors',
    label: 'Motors & Props',
    icon: <Cog size={16} />,
    items: [
      { id: 'motors-spec', label: 'Motor Specs', field: 'motors' },
      { id: 'props-spec', label: 'Propeller Specs', field: 'propellers' },
    ],
  },
  {
    id: 'power',
    label: 'Power System',
    icon: <Battery size={16} />,
    items: [
      { id: 'battery', label: 'Battery', field: 'battery' },
      { id: 'electrical', label: 'Electrical System', field: 'electrical' },
    ],
  },
  {
    id: 'electronics',
    label: 'Electronics',
    icon: <Cpu size={16} />,
    items: [
      { id: 'fc', label: 'Flight Controller', field: 'electronics.flight_controller' },
      { id: 'esc', label: 'ESC', field: 'electronics.esc' },
      { id: 'rx', label: 'Receiver', field: 'electronics.receiver' },
      { id: 'vtx', label: 'Video TX', field: 'electronics.vtx' },
    ],
  },
  {
    id: 'sensors',
    label: 'Sensors',
    icon: <Radio size={16} />,
    items: [
      { id: 'imu', label: 'IMU', field: 'sensors.imu' },
      { id: 'baro', label: 'Barometer', field: 'sensors.barometer' },
      { id: 'mag', label: 'Magnetometer', field: 'sensors.magnetometer' },
      { id: 'gps', label: 'GPS', field: 'sensors.gps' },
      { id: 'lidar', label: 'LiDAR', field: 'sensors.lidar' },
      { id: 'camera', label: 'Camera', field: 'sensors.camera' },
      { id: 'depth', label: 'Depth Camera', field: 'sensors.depth_camera' },
      { id: 'optflow', label: 'Optical Flow', field: 'sensors.optical_flow' },
    ],
  },
  {
    id: 'thermal',
    label: 'Thermal & Mechanical',
    icon: <Zap size={16} />,
    items: [
      { id: 'thermal', label: 'Thermal Limits', field: 'thermal' },
      { id: 'mechanical', label: 'Mechanical', field: 'mechanical' },
      { id: 'compat', label: 'Compatibility', field: 'compatibility' },
    ],
  },
];

export function ComponentPalette() {
  const [expandedCategories, setExpandedCategories] = useState<Set<string>>(new Set(['frame', 'motors', 'power']));
  const { selectComponent, selectedComponent } = useDroneStore();

  const toggleCategory = (id: string) => {
    setExpandedCategories((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  return (
    <div className="py-2 font-mono">
      {/* Tactical Header */}
      <div className="flex items-center gap-2 px-4 py-2 mb-1">
        <div className="w-1 h-3 bg-nexus-accent" />
        <span className="text-[10px] font-bold text-nexus-accent tracking-[0.2em] uppercase">
          Equip Manifest
        </span>
        <div className="flex-1 h-px bg-nexus-border" />
      </div>

      {CATEGORIES.map((cat) => (
        <div key={cat.id}>
          <button
            onClick={() => toggleCategory(cat.id)}
            className="w-full flex items-center gap-2 px-4 py-2 text-[11px] font-bold text-nexus-muted hover:text-nexus-accent hover:bg-nexus-accent/5 transition-colors tracking-[0.15em] uppercase"
          >
            {expandedCategories.has(cat.id) ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
            {cat.icon}
            <span>{cat.label}</span>
          </button>
          {expandedCategories.has(cat.id) && (
            <div className="ml-4">
              {cat.items.map((item) => (
                <button
                  key={item.id}
                  onClick={() => selectComponent(item.field)}
                  className={`w-full text-left px-4 py-1.5 text-[11px] font-mono tracking-wide transition-colors rounded-none ${
                    selectedComponent === item.field
                      ? 'text-nexus-accent bg-nexus-accent/10 border-l-2 border-nexus-accent'
                      : 'text-nexus-muted hover:text-nexus-text hover:bg-white/5 border-l-2 border-transparent'
                  }`}
                >
                  <span className="text-nexus-muted/50 mr-1.5">&mdash;</span>
                  <span className="uppercase">{item.label}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
