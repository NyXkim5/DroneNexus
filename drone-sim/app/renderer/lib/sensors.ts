/**
 * Sensor noise simulation models.
 * Generates realistic noise for IMU, GPS, barometer, magnetometer,
 * LiDAR, and camera based on configurable noise profiles.
 */

/**
 * Gaussian random number using Box-Muller transform.
 */
function gaussianRandom(mean = 0, stddev = 1): number {
  const u1 = Math.random();
  const u2 = Math.random();
  const z = Math.sqrt(-2 * Math.log(u1)) * Math.cos(2 * Math.PI * u2);
  return z * stddev + mean;
}

/**
 * Random walk process — accumulates small random steps.
 */
class RandomWalk {
  private value = 0;

  constructor(private stepSize: number, private limit: number) {}

  step(): number {
    this.value += gaussianRandom(0, this.stepSize);
    this.value = Math.max(-this.limit, Math.min(this.limit, this.value));
    return this.value;
  }

  reset(): void {
    this.value = 0;
  }
}

// --- IMU Noise Model ---

export interface IMUNoiseParams {
  gyro_noise_density: number;       // deg/s/sqrt(Hz)
  gyro_bias_instability: number;    // deg/s
  gyro_random_walk: number;         // deg/s^2/sqrt(Hz)
  accel_noise_density: number;      // m/s^2/sqrt(Hz)
  accel_bias_instability: number;   // m/s^2
  accel_random_walk: number;        // m/s^3/sqrt(Hz)
}

export class IMUNoiseModel {
  private gyroBiasWalk: [RandomWalk, RandomWalk, RandomWalk];
  private accelBiasWalk: [RandomWalk, RandomWalk, RandomWalk];

  constructor(private params: IMUNoiseParams) {
    this.gyroBiasWalk = [
      new RandomWalk(params.gyro_random_walk, params.gyro_bias_instability),
      new RandomWalk(params.gyro_random_walk, params.gyro_bias_instability),
      new RandomWalk(params.gyro_random_walk, params.gyro_bias_instability),
    ];
    this.accelBiasWalk = [
      new RandomWalk(params.accel_random_walk, params.accel_bias_instability),
      new RandomWalk(params.accel_random_walk, params.accel_bias_instability),
      new RandomWalk(params.accel_random_walk, params.accel_bias_instability),
    ];
  }

  /**
   * Apply noise to gyroscope readings [x, y, z] in deg/s.
   */
  addGyroNoise(clean: [number, number, number]): [number, number, number] {
    return clean.map((v, i) =>
      v + gaussianRandom(0, this.params.gyro_noise_density) + this.gyroBiasWalk[i].step()
    ) as [number, number, number];
  }

  /**
   * Apply noise to accelerometer readings [x, y, z] in m/s^2.
   */
  addAccelNoise(clean: [number, number, number]): [number, number, number] {
    return clean.map((v, i) =>
      v + gaussianRandom(0, this.params.accel_noise_density) + this.accelBiasWalk[i].step()
    ) as [number, number, number];
  }
}

// --- GPS Noise Model ---

export interface GPSNoiseParams {
  horizontal_accuracy_m: number;
  vertical_accuracy_m: number;
  dropout_probability: number;      // 0-1, per update
  multipath_probability: number;    // 0-1, chance of multipath error
  multipath_error_m: number;        // meters of multipath offset
  update_rate_hz: number;
  hdop_noise: number;
  satellite_variation: number;      // +/- from base satellite count
}

export class GPSNoiseModel {
  private dropoutActive = false;
  private dropoutRemaining = 0;
  private multipathOffset = { lat: 0, lon: 0 };

  constructor(private params: GPSNoiseParams) {}

  /**
   * Apply noise to GPS position. Returns null during dropouts.
   */
  addNoise(lat: number, lon: number, alt: number, baseSats: number): {
    lat: number;
    lon: number;
    alt: number;
    satellites: number;
    hdop: number;
    fix_type: string;
  } | null {
    // Check for dropout
    if (this.dropoutActive) {
      this.dropoutRemaining--;
      if (this.dropoutRemaining <= 0) {
        this.dropoutActive = false;
      }
      return null;
    }

    if (Math.random() < this.params.dropout_probability) {
      this.dropoutActive = true;
      this.dropoutRemaining = Math.floor(Math.random() * 30) + 5; // 5-35 updates
      return null;
    }

    // Multipath simulation
    if (Math.random() < this.params.multipath_probability) {
      const angle = Math.random() * 2 * Math.PI;
      const dist = this.params.multipath_error_m;
      this.multipathOffset = {
        lat: (dist * Math.cos(angle)) / 111320,
        lon: (dist * Math.sin(angle)) / (111320 * Math.cos(lat * Math.PI / 180)),
      };
    } else {
      this.multipathOffset = { lat: 0, lon: 0 };
    }

    // Position noise
    const latNoise = gaussianRandom(0, this.params.horizontal_accuracy_m / 111320);
    const lonNoise = gaussianRandom(0, this.params.horizontal_accuracy_m / (111320 * Math.cos(lat * Math.PI / 180)));
    const altNoise = gaussianRandom(0, this.params.vertical_accuracy_m);

    const satellites = Math.max(0, Math.round(baseSats + gaussianRandom(0, this.params.satellite_variation)));
    const hdop = Math.max(0.5, 1.0 + gaussianRandom(0, this.params.hdop_noise));

    let fix_type = '3D';
    if (satellites >= 12) fix_type = '3D-DGPS';
    else if (satellites < 6) fix_type = '2D';
    else if (satellites < 4) fix_type = 'NO_FIX';

    return {
      lat: lat + latNoise + this.multipathOffset.lat,
      lon: lon + lonNoise + this.multipathOffset.lon,
      alt: alt + altNoise,
      satellites,
      hdop,
      fix_type,
    };
  }
}

// --- Barometer Noise Model ---

export interface BaroNoiseParams {
  altitude_noise_m: number;         // standard deviation in meters
  drift_rate_m_per_min: number;     // slow drift over time
  temperature_sensitivity: number;  // m per degree C change
}

export class BaroNoiseModel {
  private drift = new RandomWalk(0.001, 0.5);
  private elapsed = 0;

  constructor(private params: BaroNoiseParams) {}

  addNoise(trueAltitude: number, dt: number): number {
    this.elapsed += dt;
    const noise = gaussianRandom(0, this.params.altitude_noise_m);
    const driftValue = this.drift.step() * this.params.drift_rate_m_per_min;
    return trueAltitude + noise + driftValue;
  }
}

// --- Magnetometer Noise Model ---

export interface MagNoiseParams {
  hard_iron_offset: [number, number, number];  // uT
  soft_iron_scale: [number, number, number];   // multipliers
  noise_density: number;                        // uT
  interference_probability: number;             // 0-1
  interference_magnitude: number;               // uT
}

export class MagNoiseModel {
  constructor(private params: MagNoiseParams) {}

  addNoise(clean: [number, number, number]): [number, number, number] {
    // Hard iron offset
    let result = clean.map((v, i) => v + this.params.hard_iron_offset[i]) as [number, number, number];

    // Soft iron distortion
    result = result.map((v, i) => v * this.params.soft_iron_scale[i]) as [number, number, number];

    // Random noise
    result = result.map(v => v + gaussianRandom(0, this.params.noise_density)) as [number, number, number];

    // Interference (e.g., from motors, wiring)
    if (Math.random() < this.params.interference_probability) {
      const interference = this.params.interference_magnitude;
      result = result.map(v => v + gaussianRandom(0, interference)) as [number, number, number];
    }

    return result;
  }
}

// --- LiDAR Noise Model ---

export interface LiDARNoiseParams {
  range_noise_m: number;            // standard deviation
  min_range_m: number;
  max_range_m: number;
  angular_resolution_deg: number;
  dropout_probability: number;       // per-ray dropout chance
  false_positive_probability: number;
}

export class LiDARNoiseModel {
  constructor(private params: LiDARNoiseParams) {}

  addNoise(trueRange: number): number | null {
    // Range check
    if (trueRange < this.params.min_range_m || trueRange > this.params.max_range_m) {
      return null;
    }

    // Dropout
    if (Math.random() < this.params.dropout_probability) {
      return null;
    }

    // False positive
    if (Math.random() < this.params.false_positive_probability) {
      return Math.random() * this.params.max_range_m;
    }

    // Range noise (increases with distance)
    const distanceFactor = 1 + (trueRange / this.params.max_range_m) * 0.5;
    return Math.max(
      this.params.min_range_m,
      trueRange + gaussianRandom(0, this.params.range_noise_m * distanceFactor)
    );
  }

  /**
   * Generate a full 360-degree scan with noise.
   */
  generateScan(trueRanges: number[]): (number | null)[] {
    return trueRanges.map(r => this.addNoise(r));
  }
}

// --- Camera Noise Model ---

export interface CameraNoiseParams {
  exposure_noise: number;           // 0-1, pixel intensity noise
  motion_blur_factor: number;       // 0-1, proportional to angular rate
  lens_distortion_k1: number;       // radial distortion coefficient
  lens_distortion_k2: number;
  chromatic_aberration: number;     // pixels of color fringing
  rolling_shutter_skew_ms: number;  // rolling shutter time
}

export class CameraNoiseModel {
  constructor(private params: CameraNoiseParams) {}

  /**
   * Calculate effective image quality (0-100) based on conditions.
   */
  calculateImageQuality(angularRate_dps: number, lightLevel: number): number {
    let quality = 100;

    // Motion blur degrades quality
    const blurDegradation = (angularRate_dps / 360) * this.params.motion_blur_factor * 30;
    quality -= blurDegradation;

    // Low light increases noise
    const noiseDegradation = (1 - lightLevel) * this.params.exposure_noise * 20;
    quality -= noiseDegradation;

    // Rolling shutter artifact at high rotation rates
    if (angularRate_dps > 100) {
      quality -= (angularRate_dps - 100) / 50;
    }

    return Math.max(0, Math.min(100, quality));
  }
}

// --- Preset Loader ---

export type NoisePresetLevel = 'perfect' | 'typical' | 'noisy' | 'failing';

export function createIMUFromPreset(preset: Record<string, number>): IMUNoiseModel {
  return new IMUNoiseModel({
    gyro_noise_density: preset.gyro_noise_density ?? 0,
    gyro_bias_instability: preset.gyro_bias_instability ?? 0,
    gyro_random_walk: preset.gyro_random_walk ?? 0,
    accel_noise_density: preset.accel_noise_density ?? 0,
    accel_bias_instability: preset.accel_bias_instability ?? 0,
    accel_random_walk: preset.accel_random_walk ?? 0,
  });
}

export function createGPSFromPreset(preset: Record<string, number>): GPSNoiseModel {
  return new GPSNoiseModel({
    horizontal_accuracy_m: preset.horizontal_accuracy_m ?? 1,
    vertical_accuracy_m: preset.vertical_accuracy_m ?? 2,
    dropout_probability: preset.dropout_probability ?? 0,
    multipath_probability: preset.multipath_probability ?? 0,
    multipath_error_m: preset.multipath_error_m ?? 0,
    update_rate_hz: preset.update_rate_hz ?? 10,
    hdop_noise: preset.hdop_noise ?? 0.1,
    satellite_variation: preset.satellite_variation ?? 1,
  });
}

export function createBaroFromPreset(preset: Record<string, number>): BaroNoiseModel {
  return new BaroNoiseModel({
    altitude_noise_m: preset.altitude_noise_m ?? 0,
    drift_rate_m_per_min: preset.drift_rate_m_per_min ?? 0,
    temperature_sensitivity: preset.temperature_sensitivity ?? 0,
  });
}

export function createMagFromPreset(preset: Record<string, number>): MagNoiseModel {
  return new MagNoiseModel({
    hard_iron_offset: [
      preset.hard_iron_x ?? 0,
      preset.hard_iron_y ?? 0,
      preset.hard_iron_z ?? 0,
    ],
    soft_iron_scale: [
      preset.soft_iron_x ?? 1,
      preset.soft_iron_y ?? 1,
      preset.soft_iron_z ?? 1,
    ],
    noise_density: preset.noise_density ?? 0,
    interference_probability: preset.interference_probability ?? 0,
    interference_magnitude: preset.interference_magnitude ?? 0,
  });
}
