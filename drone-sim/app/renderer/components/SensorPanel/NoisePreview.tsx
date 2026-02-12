import React, { useRef, useEffect, useCallback } from 'react';

interface NoisePreviewProps {
  sensorType: string;
  preset: string;
}

// Noise intensity multipliers per preset
const NOISE_LEVELS: Record<string, number> = {
  perfect: 0,
  typical: 1,
  noisy: 3,
  failing: 8,
};

const PRESET_COLORS: Record<string, string> = {
  perfect: '#00ff88',
  typical: '#3388ff',
  noisy: '#ffaa00',
  failing: '#ff3355',
};

function gaussianRandom(): number {
  const u1 = Math.random();
  const u2 = Math.random();
  return Math.sqrt(-2 * Math.log(u1)) * Math.cos(2 * Math.PI * u2);
}

export function NoisePreview({ sensorType, preset }: NoisePreviewProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const animFrameRef = useRef<number>(0);
  const bufferRef = useRef<number[]>([]);

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const w = canvas.width;
    const h = canvas.height;
    const noiseLevel = NOISE_LEVELS[preset] ?? 1;
    const color = PRESET_COLORS[preset] ?? '#3388ff';

    // Shift buffer left and add new sample
    const buffer = bufferRef.current;
    if (buffer.length > w) buffer.shift();

    // Generate noise sample — base signal + noise
    const t = Date.now() / 1000;
    const baseSignal = Math.sin(t * 2) * 0.3; // Slow oscillation (simulated sensor reading)
    const noise = gaussianRandom() * noiseLevel * 0.05;

    // Add occasional spikes for "failing" preset
    let spike = 0;
    if (preset === 'failing' && Math.random() < 0.02) {
      spike = (Math.random() - 0.5) * 0.8;
    }

    buffer.push(baseSignal + noise + spike);

    // Draw
    ctx.fillStyle = '#0a0e17';
    ctx.fillRect(0, 0, w, h);

    // Grid lines
    ctx.strokeStyle = '#1e293b';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(0, h / 2);
    ctx.lineTo(w, h / 2);
    ctx.stroke();

    // Signal line
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.5;
    ctx.beginPath();

    for (let i = 0; i < buffer.length; i++) {
      const x = (i / w) * w;
      const y = h / 2 - buffer[i] * h * 0.8;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.stroke();

    // Glow effect
    ctx.strokeStyle = `${color}40`;
    ctx.lineWidth = 4;
    ctx.beginPath();
    for (let i = 0; i < buffer.length; i++) {
      const x = (i / w) * w;
      const y = h / 2 - buffer[i] * h * 0.8;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.stroke();

    animFrameRef.current = requestAnimationFrame(draw);
  }, [preset]);

  useEffect(() => {
    bufferRef.current = [];
    animFrameRef.current = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(animFrameRef.current);
  }, [draw]);

  return (
    <div>
      <label className="text-xs text-nexus-muted mb-1 block">Noise Preview</label>
      <canvas
        ref={canvasRef}
        width={200}
        height={60}
        className="w-full rounded border border-nexus-border"
      />
    </div>
  );
}
