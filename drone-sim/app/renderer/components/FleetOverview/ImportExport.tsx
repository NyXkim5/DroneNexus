import React from 'react';
import { useDroneStore } from '../../stores/droneStore';
import { useFleetStore } from '../../stores/fleetStore';
import { processAirframe } from '../../lib/airframe-parser';
import { Download, Upload, FileUp } from 'lucide-react';

export function ImportExport() {
  const { currentConfig } = useDroneStore();
  const { addDrone } = useFleetStore();

  const handleExportCurrent = async () => {
    await window.api.exportAirframe(currentConfig);
  };

  const handleImportToFleet = async () => {
    const config = await window.api.importAirframe();
    if (config) {
      const processed = processAirframe(config);
      addDrone('imported.yaml', processed);
    }
  };

  return (
    <div className="flex gap-2">
      <button onClick={handleExportCurrent} className="btn-ghost text-xs flex items-center gap-1">
        <Download size={12} /> Export Current
      </button>
      <button onClick={handleImportToFleet} className="btn-ghost text-xs flex items-center gap-1">
        <Upload size={12} /> Import YAML
      </button>
    </div>
  );
}
