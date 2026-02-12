import React, { useRef, useEffect, useCallback } from 'react';
import { useSimStore } from '../../stores/simStore';

function PIDSlider({ label, value, onChange, min, max, step, color }: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  min: number;
  max: number;
  step: number;
  color: string;
}) {
  return (
    <div className="flex items-center gap-2">
      <label className="text-xs text-nexus-muted w-4">{label}</label>
      <input
        type="range"
        className="flex-1 h-1.5 appearance-none bg-nexus-border rounded-full cursor-pointer"
        style={{ accentColor: color }}
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(parseFloat(e.target.value))}
      />
      <span className="text-xs font-mono w-8 text-right" style={{ color }}>{value}</span>
    </div>
  );
}

function ResponseCurve({ gains, axis }: { gains: { p: number; i: number; d: number }; axis: string }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const w = canvas.width;
    const h = canvas.height;

    ctx.fillStyle = '#0a0e17';
    ctx.fillRect(0, 0, w, h);

    // Grid
    ctx.strokeStyle = '#1e293b';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(0, h / 2);
    ctx.lineTo(w, h / 2);
    ctx.stroke();

    // Simulate step response
    const { p, i, d } = gains;
    const kp = p / 100;
    const ki = i / 1000;
    const kd = d / 1000;

    let position = 0;
    let velocity = 0;
    let integral = 0;
    let lastError = 0;
    const target = 1;
    const dt = 0.01;
    const points: number[] = [];

    for (let t = 0; t < 2; t += dt) {
      const error = target - position;
      integral += error * dt;
      const derivative = (error - lastError) / dt;
      lastError = error;

      const force = kp * error + ki * integral + kd * derivative;
      velocity += force * dt - velocity * 0.5 * dt; // damping
      position += velocity * dt;
      points.push(position);
    }

    // Draw target line
    ctx.strokeStyle = '#334155';
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    const targetY = h - (target / 1.5) * h * 0.8 - h * 0.1;
    ctx.moveTo(0, targetY);
    ctx.lineTo(w, targetY);
    ctx.stroke();
    ctx.setLineDash([]);

    // Draw response
    ctx.strokeStyle = '#00ff88';
    ctx.lineWidth = 2;
    ctx.beginPath();
    points.forEach((val, i) => {
      const x = (i / points.length) * w;
      const y = h - (val / 1.5) * h * 0.8 - h * 0.1;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();

    // Labels
    ctx.fillStyle = '#64748b';
    ctx.font = '10px JetBrains Mono, monospace';
    ctx.fillText('target', w - 40, targetY - 4);
    ctx.fillText('0s', 2, h - 2);
    ctx.fillText('2s', w - 16, h - 2);
  }, [gains]);

  useEffect(() => { draw(); }, [draw]);

  return (
    <canvas
      ref={canvasRef}
      width={220}
      height={80}
      className="w-full rounded border border-nexus-border mt-2"
    />
  );
}

export function PIDTuner() {
  const { pidGains, updatePID, running } = useSimStore();

  const axes = [
    { key: 'roll', label: 'Roll', color: '#ff3355' },
    { key: 'pitch', label: 'Pitch', color: '#3388ff' },
    { key: 'yaw', label: 'Yaw', color: '#ffaa00' },
    { key: 'altitude', label: 'Altitude', color: '#00ff88' },
  ];

  return (
    <div>
      <div className="panel-header">PID Tuning</div>
      <div className="p-3 text-xs text-nexus-muted mb-2">
        Adjust PID gains to change how the drone responds to commands.
        P = how aggressively it corrects, I = removes steady-state error,
        D = dampens oscillation.
      </div>

      {axes.map(({ key, label, color }) => {
        const gains = pidGains[key as keyof typeof pidGains];
        return (
          <div key={key} className="px-3 py-2 border-t border-nexus-border/50">
            <div className="text-xs font-medium mb-2" style={{ color }}>{label}</div>
            <PIDSlider
              label="P"
              value={gains.p}
              onChange={(p) => updatePID(key, { ...gains, p })}
              min={0} max={100} step={1} color={color}
            />
            <PIDSlider
              label="I"
              value={gains.i}
              onChange={(i) => updatePID(key, { ...gains, i })}
              min={0} max={200} step={1} color={color}
            />
            <PIDSlider
              label="D"
              value={gains.d}
              onChange={(d) => updatePID(key, { ...gains, d })}
              min={0} max={100} step={1} color={color}
            />
            <ResponseCurve gains={gains} axis={key} />
          </div>
        );
      })}
    </div>
  );
}
