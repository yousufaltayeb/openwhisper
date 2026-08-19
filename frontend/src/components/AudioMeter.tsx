import styles from "./AudioMeter.module.css";

interface AudioMeterProps {
  level: number;
  peak: number;
  active: boolean;
}

const segments = Array.from({ length: 18 }, (_, index) => index);

export function AudioMeter({ level, peak, active }: AudioMeterProps) {
  const normalized = Math.min(1, Math.max(0, level));
  const decibels = normalized > 0 ? Math.max(-60, 20 * Math.log10(normalized)) : -60;
  const activeSegments = Math.round(((decibels + 60) / 60) * segments.length);
  const normalizedPeak = Math.min(1, Math.max(0, peak));
  const peakDecibels = normalizedPeak > 0 ? Math.max(-60, 20 * Math.log10(normalizedPeak)) : -60;
  const clippingRisk = normalizedPeak > 0.86;

  return (
    <div className={styles.meterGroup}>
      <div className={styles.meterHeading}>
        <span>Input level</span>
        <output>{active ? `${decibels.toFixed(1)} dBFS` : "−∞ dBFS"}</output>
      </div>
      <div
        className={styles.meter}
        role="meter"
        aria-label="Microphone input level"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(normalized * 100)}
        aria-valuetext={
          active
            ? `${decibels.toFixed(1)} decibels full scale, peak ${peakDecibels.toFixed(1)}${clippingRisk ? ", clipping risk" : ""}`
            : "No input"
        }
      >
        <div className={styles.scale} aria-hidden="true">
          <span>0</span>
          <span>−12</span>
          <span>−24</span>
          <span>−36</span>
          <span>−48</span>
          <span>−60</span>
        </div>
        <div className={styles.segments} aria-hidden="true">
          {segments.map((segment) => (
            <i
              key={segment}
              data-active={active && segment >= segments.length - activeSegments}
              data-peak={clippingRisk && segment < 2}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
