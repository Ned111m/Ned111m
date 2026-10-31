import React from 'react';
import {CalculateMetadataFunction, Composition, staticFile} from 'remotion';
import {TelemetryOverlay} from './telemetry-overlays/TelemetryOverlay';
import {parseFps, type TelemetryData, type TelemetryOverlayProps} from './telemetry-overlays/types';
import {LowerThird, type LowerThirdProps} from './dynamic-templates/LowerThird';

// Telemetry JSON lives in /public (passed by path) so each render worker fetches it once instead of
// receiving thousands of samples through serialized input props.
const telemetryMetadata: CalculateMetadataFunction<TelemetryOverlayProps> = async ({props, abortSignal}) => {
  let telemetry = props.telemetry;
  if (!telemetry && props.telemetrySrc) {
    const res = await fetch(staticFile(props.telemetrySrc), {signal: abortSignal});
    if (!res.ok) throw new Error(`telemetry fetch failed: ${res.status} ${props.telemetrySrc}`);
    telemetry = (await res.json()) as TelemetryData;
  }
  if (!telemetry) throw new Error('TelemetryOverlay needs telemetrySrc or telemetry');
  if (telemetry.version !== 1) throw new Error(`unsupported telemetry version ${String(telemetry.version)}`);
  return {
    durationInFrames: telemetry.durationInFrames,
    fps: parseFps(telemetry.fps),
    props: {...props, telemetry, telemetrySrc: undefined},
  };
};

const telemetryDefaults: TelemetryOverlayProps = {
  telemetrySrc: 'telemetry/sample.json',
  units: 'metric',
  accent: '#FFB000',
  showTrack: true,
  showCamera: false,
  locationLabel: 'Maglaj, BiH',
};

const lowerThirdDefaults: LowerThirdProps = {
  title: 'Maglaj',
  subtitle: 'Stari grad · Bosna i Hercegovina',
  accent: '#FFB000',
  holdFrames: 90,
  outFrames: 12,
  position: 'left',
};

export const RemotionRoot: React.FC = () => (
  <>
    {/* Transparent overlay: render as ProRes 4444 (alpha) and lay over the graded clip in Resolve. */}
    <Composition
      id="TelemetryOverlay"
      component={TelemetryOverlay}
      width={3840}
      height={2160}
      fps={25}
      durationInFrames={250}
      defaultProps={telemetryDefaults}
      calculateMetadata={telemetryMetadata}
    />
    <Composition id="LowerThird" component={LowerThird} width={3840} height={2160} fps={25} durationInFrames={150} defaultProps={lowerThirdDefaults} />
  </>
);
