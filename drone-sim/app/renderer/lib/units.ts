/**
 * Unit conversion helpers for drone parameters.
 * Makes the UI friendly for both metric and imperial users.
 */

export function gramsToOunces(g: number): number {
  return Math.round(g * 0.03527396 * 10) / 10;
}

export function gramsToPounds(g: number): number {
  return Math.round(g * 0.00220462 * 100) / 100;
}

export function gramsToKg(g: number): number {
  return Math.round(g / 1000 * 100) / 100;
}

export function mmToInches(mm: number): number {
  return Math.round(mm * 0.03937008 * 10) / 10;
}

export function msToMph(ms: number): number {
  return Math.round(ms * 2.23694 * 10) / 10;
}

export function msToKmh(ms: number): number {
  return Math.round(ms * 3.6 * 10) / 10;
}

export function metersToFeet(m: number): number {
  return Math.round(m * 3.28084);
}

export function mahToWh(mah: number, voltage: number): number {
  return Math.round(mah * voltage / 1000 * 10) / 10;
}

export function celsiusToFahrenheit(c: number): number {
  return Math.round(c * 9 / 5 + 32);
}

export function formatDuration(minutes: number): string {
  if (minutes < 1) return `${Math.round(minutes * 60)}s`;
  const mins = Math.floor(minutes);
  const secs = Math.round((minutes - mins) * 60);
  return secs > 0 ? `${mins}m ${secs}s` : `${mins}m`;
}

export function formatWeight(g: number): string {
  if (g >= 1000) return `${gramsToKg(g)} kg`;
  return `${Math.round(g)} g`;
}

export function formatCurrent(a: number): string {
  return `${Math.round(a * 10) / 10} A`;
}

export function formatVoltage(v: number): string {
  return `${Math.round(v * 100) / 100} V`;
}

export function formatPower(w: number): string {
  if (w >= 1000) return `${Math.round(w / 100) / 10} kW`;
  return `${Math.round(w)} W`;
}

export function cellCountToLabel(cells: number): string {
  return `${cells}S (${(cells * 3.7).toFixed(1)}V nominal)`;
}

/**
 * Human-friendly rating for thrust-to-weight ratio.
 */
export function twRating(tw: number): { label: string; color: string; description: string } {
  if (tw < 1.5) return { label: 'Cannot Fly', color: '#ff3355', description: 'Not enough thrust to sustain flight' };
  if (tw < 2) return { label: 'Marginal', color: '#ff6644', description: 'Can hover but very sluggish, no wind tolerance' };
  if (tw < 3) return { label: 'Moderate', color: '#ffaa00', description: 'Good for steady flight, light payload, calm conditions' };
  if (tw < 5) return { label: 'Good', color: '#88cc00', description: 'Agile, handles wind well, good for most missions' };
  if (tw < 8) return { label: 'Sport', color: '#00ff88', description: 'Very agile, fast acceleration, FPV freestyle capable' };
  return { label: 'Racing', color: '#00ccff', description: 'Extreme performance, very fast, racing/acrobatic flight' };
}

/**
 * Human-friendly rating for flight time.
 */
export function flightTimeRating(minutes: number): { label: string; color: string } {
  if (minutes < 3) return { label: 'Very Short', color: '#ff3355' };
  if (minutes < 6) return { label: 'Short', color: '#ffaa00' };
  if (minutes < 15) return { label: 'Average', color: '#88cc00' };
  if (minutes < 30) return { label: 'Long', color: '#00ff88' };
  return { label: 'Extended', color: '#00ccff' };
}
