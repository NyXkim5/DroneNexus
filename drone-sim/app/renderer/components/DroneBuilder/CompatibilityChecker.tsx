import React, { useMemo } from 'react';
import { useDroneStore } from '../../stores/droneStore';
import { runCompatibilityCheck } from '../../lib/compatibility';
import { AlertTriangle, XCircle, Info, CheckCircle } from 'lucide-react';

export function CompatibilityChecker() {
  const { currentConfig } = useDroneStore();

  const result = useMemo(() => runCompatibilityCheck(currentConfig), [currentConfig]);

  if (result.errors.length === 0 && result.warnings.length === 0) {
    return (
      <div className="px-4 py-2 border-t-2 border-nexus-accent/20 bg-nexus-panel flex items-center gap-2 text-[11px] text-nexus-accent font-mono tracking-[0.15em] uppercase">
        <div className="w-1.5 h-1.5 rounded-full bg-nexus-accent animate-pulse" />
        <CheckCircle size={12} />
        <span>All Systems Nominal</span>
        <div className="flex-1 h-px bg-nexus-border" />
        <span className="text-[9px] text-nexus-muted tracking-[0.2em]">Compat Status</span>
      </div>
    );
  }

  return (
    <div className="border-t-2 border-nexus-accent/20 max-h-32 overflow-y-auto bg-nexus-panel font-mono">
      {/* Section Header */}
      <div className="flex items-center gap-2 px-4 py-1.5 border-b border-nexus-border">
        <div className="w-1 h-3 bg-nexus-warn" />
        <span className="text-[9px] text-nexus-muted tracking-[0.2em] uppercase font-bold">
          System Alerts
        </span>
        <div className="flex-1 h-px bg-nexus-border" />
        <span className="text-[9px] text-nexus-muted/50">
          {result.errors.length + result.warnings.length} ISSUE{result.errors.length + result.warnings.length !== 1 ? 'S' : ''}
        </span>
      </div>

      {result.errors.map((issue, i) => (
        <div key={`err-${i}`} className="px-4 py-1.5 flex items-start gap-2 text-[11px] border-l-2 border-nexus-danger bg-nexus-danger/5">
          <XCircle size={12} className="text-nexus-danger flex-shrink-0 mt-0.5" />
          <div>
            <span className="text-nexus-danger font-bold tracking-[0.1em] uppercase">Critical: {issue.component}</span>{' '}
            <span className="text-nexus-text tracking-wide">{issue.message}</span>
            {issue.fix && <div className="text-nexus-muted/60 mt-0.5 tracking-wide">{issue.fix}</div>}
          </div>
        </div>
      ))}
      {result.warnings.map((issue, i) => (
        <div key={`warn-${i}`} className="px-4 py-1.5 flex items-start gap-2 text-[11px] border-l-2 border-nexus-warn bg-nexus-warn/5">
          <AlertTriangle size={12} className="text-nexus-warn flex-shrink-0 mt-0.5" />
          <div>
            <span className="text-nexus-warn font-bold tracking-[0.1em] uppercase">Caution: {issue.component}</span>{' '}
            <span className="text-nexus-text tracking-wide">{issue.message}</span>
            {issue.fix && <div className="text-nexus-muted/60 mt-0.5 tracking-wide">{issue.fix}</div>}
          </div>
        </div>
      ))}
      {result.info.map((issue, i) => (
        <div key={`info-${i}`} className="px-4 py-1.5 flex items-start gap-2 text-[11px] border-l-2 border-nexus-info bg-nexus-info/5">
          <Info size={12} className="text-nexus-info flex-shrink-0 mt-0.5" />
          <div>
            <span className="text-nexus-info font-bold tracking-[0.1em] uppercase">Notice: {issue.component}</span>{' '}
            <span className="text-nexus-text tracking-wide">{issue.message}</span>
          </div>
        </div>
      ))}
    </div>
  );
}
