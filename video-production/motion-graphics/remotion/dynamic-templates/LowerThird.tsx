import React from 'react';
import {AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';

/*
 * Data-driven lower third (location / interviewee / shot info).
 * Motion reasons:
 *  - Accent bar grows first (scaleX) to lead the eye to where text will appear.
 *  - Title then subtitle slide in 40 px with a critically damped spring: reading order, no bounce.
 *  - Exit mirrors the entry over `outFrames` so the card never cuts out mid-read.
 * Only transform/opacity are animated (compositor-only).
 */
export type LowerThirdProps = {
  title: string;
  subtitle?: string;
  accent: string;
  /** frames the card holds fully visible before exiting */
  holdFrames: number;
  outFrames: number;
  position: 'left' | 'right';
};

const BASE_W = 1920;

export const LowerThird: React.FC<LowerThirdProps> = ({title, subtitle, accent, holdFrames, outFrames, position}) => {
  const frame = useCurrentFrame();
  const {fps, width} = useVideoConfig();
  const cfg = {damping: 200, stiffness: 120, mass: 1} as const; // critically damped
  const bar = spring({frame, fps, config: cfg, durationInFrames: Math.round(0.4 * fps)});
  const t1 = spring({frame: frame - Math.round(0.15 * fps), fps, config: cfg, durationInFrames: Math.round(0.5 * fps)});
  const t2 = spring({frame: frame - Math.round(0.3 * fps), fps, config: cfg, durationInFrames: Math.round(0.5 * fps)});
  const exitStart = Math.round(0.8 * fps) + holdFrames;
  const out = interpolate(frame, [exitStart, exitStart + outFrames], [1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const dir = position === 'left' ? -1 : 1;
  const scale = width / BASE_W;

  return (
    <AbsoluteFill>
      <div
        style={{
          position: 'absolute',
          bottom: 140 * scale,
          [position]: 96 * scale,
          transform: `scale(${scale})`,
          transformOrigin: position === 'left' ? '0 100%' : '100% 100%',
          opacity: out,
          color: '#fff',
          fontFamily: 'Inter, "Helvetica Neue", Arial, sans-serif',
          textAlign: position,
          textShadow: '0 2px 10px rgba(0,0,0,0.5)',
        }}
      >
        <div style={{fontSize: 64, fontWeight: 800, opacity: t1, transform: `translate3d(${dir * 40 * (1 - t1)}px,0,0)`}}>{title}</div>
        {subtitle ? (
          <div style={{fontSize: 30, fontWeight: 500, opacity: t2 * 0.9, transform: `translate3d(${dir * 40 * (1 - t2)}px,0,0)`}}>{subtitle}</div>
        ) : null}
        <div
          style={{
            height: 6,
            marginTop: 14,
            width: 220,
            background: accent,
            transform: `scaleX(${bar})`,
            transformOrigin: position === 'left' ? '0 50%' : '100% 50%',
            marginLeft: position === 'right' ? 'auto' : 0,
          }}
        />
      </div>
    </AbsoluteFill>
  );
};
