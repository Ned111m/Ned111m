import React, {useMemo} from 'react';
import {AbsoluteFill, Easing, interpolate, useCurrentFrame, useVideoConfig} from 'remotion';
import type {TelemetryData, TelemetryOverlayProps, TelemetrySample, Units} from './types';

/*
 * Data-driven drone telemetry HUD.
 * Design reasons (no decorative motion):
 *  - 12-frame opacity in/out: avoids a hard pop when the overlay is cut onto footage.
 *  - Numbers use tabular-nums: digit widths stay fixed so values don't jitter frame-to-frame.
 *  - Route mini-map: gives geographic context; progress is drawn with stroke-dashoffset, the
 *    position dot moves with transform: translate3d (compositor-only).
 *  - Layout is authored at 1920x1080 and scaled with one transform: identical at 1080p / 1440p / 4K (16:9).
 *  - Title-safe margin 5%.
 */

const BASE_W = 1920;
const FADE = 12;
const MAP = 260; // px, mini-map box
const MAP_PAD = 14;

const fmtSpeed = (ms: number, u: Units) => (u === 'metric' ? `${(ms * 3.6).toFixed(0)}` : `${(ms * 2.236936).toFixed(0)}`);
const fmtAlt = (m: number | null, u: Units) => (m === null ? '—' : u === 'metric' ? m.toFixed(0) : (m * 3.28084).toFixed(0));
const fmtDist = (m: number, u: Units) =>
  u === 'metric' ? (m >= 1000 ? `${(m / 1000).toFixed(2)} km` : `${m.toFixed(0)} m`) : `${(m / 1609.344).toFixed(2)} mi`;
const fmtCoord = (v: number | null, pos: string, neg: string) =>
  v === null ? '—' : `${Math.abs(v).toFixed(5)}° ${v >= 0 ? pos : neg}`;

type Track = {d: string; length: number; cum: Float32Array; pts: Float32Array};

/** Equirectangular local projection (exact enough for < 50 km flights) fitted into the map box. */
const buildTrack = (data: TelemetryData): Track | null => {
  const {minLat, maxLat, minLon, maxLon} = data.bounds;
  if (minLat === null || maxLat === null || minLon === null || maxLon === null) return null;
  const lat0 = (minLat + maxLat) / 2;
  const kx = Math.cos((lat0 * Math.PI) / 180) * 111320;
  const ky = 110574;
  const wM = Math.max((maxLon - minLon) * kx, 1);
  const hM = Math.max((maxLat - minLat) * ky, 1);
  const inner = MAP - 2 * MAP_PAD;
  const s = inner / Math.max(wM, hM);
  const ox = MAP_PAD + (inner - wM * s) / 2;
  const oy = MAP_PAD + (inner - hM * s) / 2;
  const n = data.samples.length;
  const pts = new Float32Array(n * 2);
  const cum = new Float32Array(n);
  let lastX = NaN;
  let lastY = NaN;
  let len = 0;
  let d = '';
  for (let i = 0; i < n; i++) {
    const smp = data.samples[i] as TelemetrySample;
    if (smp.lat === null || smp.lon === null) {
      pts[i * 2] = lastX;
      pts[i * 2 + 1] = lastY;
      cum[i] = len;
      continue;
    }
    const x = ox + (smp.lon - minLon) * kx * s;
    const y = oy + (maxLat - smp.lat) * ky * s; // north up
    if (Number.isNaN(lastX)) d += `M${x.toFixed(2)} ${y.toFixed(2)}`;
    else {
      len += Math.hypot(x - lastX, y - lastY);
      d += `L${x.toFixed(2)} ${y.toFixed(2)}`;
    }
    pts[i * 2] = x;
    pts[i * 2 + 1] = y;
    cum[i] = len;
    lastX = x;
    lastY = y;
  }
  return d ? {d, length: len, cum, pts} : null;
};

const Readout: React.FC<{label: string; value: string; unit: string; accent: string}> = ({label, value, unit, accent}) => (
  <div style={{display: 'flex', flexDirection: 'column', minWidth: 150}}>
    <span style={{fontSize: 18, letterSpacing: 3, opacity: 0.75, textTransform: 'uppercase'}}>{label}</span>
    <span style={{fontSize: 56, fontWeight: 700, lineHeight: 1.05, fontVariantNumeric: 'tabular-nums'}}>
      {value}
      <span style={{fontSize: 22, fontWeight: 500, marginLeft: 6, color: accent}}>{unit}</span>
    </span>
  </div>
);

export const TelemetryOverlay: React.FC<TelemetryOverlayProps> = ({telemetry, units, accent, showTrack, showCamera, locationLabel}) => {
  const frame = useCurrentFrame();
  const {width, durationInFrames} = useVideoConfig();
  const track = useMemo(() => (telemetry && showTrack ? buildTrack(telemetry) : null), [telemetry, showTrack]);

  if (!telemetry || telemetry.samples.length === 0) return null;
  const idx = Math.min(Math.max(frame, 0), telemetry.samples.length - 1);
  const s = telemetry.samples[idx] as TelemetrySample;

  const opacity = interpolate(frame, [0, FADE, durationInFrames - FADE, durationInFrames], [0, 1, 1, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.bezier(0.33, 0, 0.67, 1),
  });
  const scale = width / BASE_W;
  const climb = s.vSpeedMs;
  const climbArrow = Math.abs(climb) < 0.2 ? '•' : climb > 0 ? '▲' : '▼';

  const dashOffset = track ? track.length - (track.cum[idx] ?? 0) : 0;
  const dotX = track ? track.pts[idx * 2] ?? 0 : 0;
  const dotY = track ? track.pts[idx * 2 + 1] ?? 0 : 0;

  return (
    <AbsoluteFill style={{opacity, willChange: 'opacity'}}>
      <div
        style={{
          position: 'absolute',
          left: 0,
          top: 0,
          width: BASE_W,
          height: BASE_W * (9 / 16),
          transform: `scale(${scale})`,
          transformOrigin: '0 0',
          color: '#fff',
          fontFamily: 'Inter, "Helvetica Neue", Arial, sans-serif',
          textShadow: '0 2px 8px rgba(0,0,0,0.55)',
        }}
      >
        {/* bottom-left readouts */}
        <div style={{position: 'absolute', left: 96, bottom: 54, display: 'flex', gap: 48, alignItems: 'flex-end'}}>
          <Readout label="Alt" value={fmtAlt(s.relAlt, units)} unit={units === 'metric' ? 'm' : 'ft'} accent={accent} />
          <Readout label="Speed" value={fmtSpeed(s.speedMs, units)} unit={units === 'metric' ? 'km/h' : 'mph'} accent={accent} />
          <Readout
            label="Climb"
            value={`${climbArrow} ${Math.abs(units === 'metric' ? climb : climb * 3.28084).toFixed(1)}`}
            unit={units === 'metric' ? 'm/s' : 'ft/s'}
            accent={accent}
          />
          <div style={{display: 'flex', flexDirection: 'column', fontSize: 20, lineHeight: 1.5, fontVariantNumeric: 'tabular-nums', opacity: 0.9}}>
            {locationLabel ? <span style={{fontSize: 24, fontWeight: 700, color: accent}}>{locationLabel}</span> : null}
            <span>{fmtCoord(s.lat, 'N', 'S')}</span>
            <span>{fmtCoord(s.lon, 'E', 'W')}</span>
            <span>{fmtDist(s.distanceM, units)} flown</span>
          </div>
        </div>

        {/* top-right camera block */}
        {showCamera ? (
          <div style={{position: 'absolute', right: 96, top: 54, fontSize: 20, letterSpacing: 2, fontVariantNumeric: 'tabular-nums', opacity: 0.85, textAlign: 'right'}}>
            {[s.iso != null ? `ISO ${s.iso}` : null, s.shutter ? `${s.shutter}s` : null, s.fnum != null ? `f/${s.fnum}` : null, s.ev != null ? `EV ${s.ev}` : null]
              .filter(Boolean)
              .join('  ·  ')}
          </div>
        ) : null}

        {/* bottom-right route map */}
        {track ? (
          <svg width={MAP} height={MAP} style={{position: 'absolute', right: 96, bottom: 54, overflow: 'visible'}}>
            <rect x={0} y={0} width={MAP} height={MAP} rx={18} fill="rgba(0,0,0,0.35)" />
            <path d={track.d} fill="none" stroke="rgba(255,255,255,0.28)" strokeWidth={3} strokeLinejoin="round" strokeLinecap="round" />
            <path
              d={track.d}
              fill="none"
              stroke={accent}
              strokeWidth={4}
              strokeLinejoin="round"
              strokeLinecap="round"
              strokeDasharray={track.length}
              strokeDashoffset={dashOffset}
            />
            <g style={{transform: `translate3d(${dotX}px, ${dotY}px, 0)`}}>
              <circle r={9} fill={accent} stroke="#fff" strokeWidth={3} />
            </g>
          </svg>
        ) : null}
      </div>
    </AbsoluteFill>
  );
};
