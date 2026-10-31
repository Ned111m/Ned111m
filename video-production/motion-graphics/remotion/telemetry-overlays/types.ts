// Mirrors shared-config/workflow-schemas/telemetry-props.schema.json (version 1).
// Produced by dji_srt_to_props.py — samples[i].frame === i, so lookup is O(1).

export type TelemetrySample = {
  frame: number;
  t: number;
  lat: number | null;
  lon: number | null;
  relAlt: number | null;
  absAlt?: number | null;
  speedMs: number;
  vSpeedMs: number;
  headingDeg?: number | null;
  distanceM: number;
  iso?: number | null;
  shutter?: string | null;
  fnum?: number | null;
  ev?: number | null;
  ct?: number | null;
  focalLen?: number | null;
  colorMd?: string | null;
};

export type TelemetryBounds = {
  minLat: number | null;
  maxLat: number | null;
  minLon: number | null;
  maxLon: number | null;
  minAlt: number | null;
  maxAlt: number | null;
  maxSpeedMs: number;
  totalDistanceM: number;
};

export type TelemetryData = {
  version: 1;
  fps: string; // "25" | "30000/1001"
  durationInFrames: number;
  source: { file: string; device: string; startTime?: string | null; hasGps?: boolean };
  bounds: TelemetryBounds;
  samples: TelemetrySample[];
};

export type Units = 'metric' | 'imperial';

export type TelemetryOverlayProps = {
  /** Path inside /public, e.g. "telemetry/DJI_0042.json" (preferred: keeps props small per render worker). */
  telemetrySrc?: string;
  /** Inline data (filled by calculateMetadata when telemetrySrc is used). */
  telemetry?: TelemetryData;
  units: Units;
  accent: string;
  showTrack: boolean;
  showCamera: boolean;
  locationLabel?: string;
};

/** "30000/1001" -> 29.97002997 (exact rational -> float only at the Remotion boundary). */
export const parseFps = (fps: string): number => {
  const [n, d] = fps.split('/');
  return Number(n) / (d ? Number(d) : 1);
};
