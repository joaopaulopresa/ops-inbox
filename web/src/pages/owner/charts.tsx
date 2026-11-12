import { useState } from "react";

export interface Series {
  name: string;
  color: string;
  points: { x: number; y: number }[];
}

interface LineProps {
  title: string;
  series: Series[];
  marker?: number;
  markerLabel?: string;
  yMax: number;
  yFormat: (v: number) => string;
  xFormat: (v: number) => string;
  xLabel: string;
}

const W = 560;
const H = 220;
const PAD = { left: 48, right: 72, top: 14, bottom: 34 };

/** A small line chart with a crosshair tooltip and one vertical marker. */
export function LineChart({ title, series, marker, markerLabel, yMax, yFormat, xFormat, xLabel }: LineProps) {
  const [hover, setHover] = useState<number | null>(null);
  const xs = series[0]?.points.map((p) => p.x) ?? [];
  const x = (v: number) => PAD.left + v * (W - PAD.left - PAD.right);
  const y = (v: number) => PAD.top + (1 - v / yMax) * (H - PAD.top - PAD.bottom);
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((t) => t * yMax);

  const onMove = (event: React.MouseEvent<SVGSVGElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    const px = ((event.clientX - box.left) / box.width) * W;
    const value = (px - PAD.left) / (W - PAD.left - PAD.right);
    if (!xs.length) return;
    const nearest = xs.reduce((a, b) => (Math.abs(b - value) < Math.abs(a - value) ? b : a));
    setHover(nearest);
  };

  return (
    <figure className="chart">
      <figcaption>{title}</figcaption>
      {series.length > 1 && (
        <div className="legend">
          {series.map((s) => (
            <span key={s.name}>
              <i style={{ background: s.color }} /> {s.name}
            </span>
          ))}
        </div>
      )}
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={title} onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={PAD.left} x2={W - PAD.right} y1={y(t)} y2={y(t)} className="grid" />
            <text x={PAD.left - 8} y={y(t) + 4} textAnchor="end" className="tick">
              {yFormat(t)}
            </text>
          </g>
        ))}
        {[0, 0.2, 0.4, 0.6, 0.8, 1].map((t) => (
          <text key={t} x={x(t)} y={H - PAD.bottom + 18} textAnchor="middle" className="tick">
            {xFormat(t)}
          </text>
        ))}
        <text x={(PAD.left + W - PAD.right) / 2} y={H - 2} textAnchor="middle" className="axis-label">
          {xLabel}
        </text>
        {marker !== undefined && (
          <g>
            <line x1={x(marker)} x2={x(marker)} y1={PAD.top} y2={H - PAD.bottom} className="marker" />
            <text x={x(marker) + 6} y={PAD.top + 10} className="tick">
              {markerLabel}
            </text>
          </g>
        )}
        {series.map((s) => {
          const path = s.points.map((p, i) => `${i ? "L" : "M"}${x(p.x)},${y(Math.min(p.y, yMax))}`).join(" ");
          const last = s.points[s.points.length - 1];
          return (
            <g key={s.name}>
              <path d={path} fill="none" stroke={s.color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
              {last && (
                <text x={x(last.x) + 8} y={y(Math.min(last.y, yMax)) + 4} className="end-label">
                  {s.name}
                </text>
              )}
            </g>
          );
        })}
        {hover !== null && (
          <g>
            <line x1={x(hover)} x2={x(hover)} y1={PAD.top} y2={H - PAD.bottom} className="crosshair" />
            {series.map((s) => {
              const p = s.points.find((q) => q.x === hover);
              return p ? (
                <circle key={s.name} cx={x(p.x)} cy={y(Math.min(p.y, yMax))} r={4.5} fill={s.color} stroke="var(--surface)" strokeWidth={2} />
              ) : null;
            })}
          </g>
        )}
      </svg>
      {hover !== null && (
        <div className="tooltip">
          <strong>{xFormat(hover)}</strong>
          {series.map((s) => {
            const p = s.points.find((q) => q.x === hover);
            return (
              <span key={s.name}>
                <i style={{ background: s.color }} /> {s.name}: {p ? yFormat(p.y) : "n/a"}
              </span>
            );
          })}
        </div>
      )}
    </figure>
  );
}

/** Horizontal bars for a ranked list, values printed at the bar end. */
export function BarList({ title, items, format = String }: { title: string; items: { label: string; value: number }[]; format?: (v: number) => string }) {
  const max = Math.max(1, ...items.map((i) => i.value));
  return (
    <figure className="chart">
      <figcaption>{title}</figcaption>
      {items.length === 0 ? (
        <p className="muted">Nothing yet.</p>
      ) : (
        <ul className="bars">
          {items.map((item) => (
            <li key={item.label} title={`${item.label}: ${format(item.value)}`}>
              <span className="bar-label">{item.label}</span>
              <span className="bar-track">
                <span className="bar" style={{ width: `${(item.value / max) * 100}%` }} />
              </span>
              <span className="bar-value">{format(item.value)}</span>
            </li>
          ))}
        </ul>
      )}
    </figure>
  );
}

/** Columns per day. */
export function Columns({ title, items }: { title: string; items: { label: string; value: number }[] }) {
  const max = Math.max(1, ...items.map((i) => i.value));
  return (
    <figure className="chart">
      <figcaption>{title}</figcaption>
      {items.length === 0 ? (
        <p className="muted">No e-mails yet.</p>
      ) : (
        <div className="columns" role="img" aria-label={title}>
          {items.map((item) => (
            <span key={item.label} className="column" title={`${item.label}: ${item.value}`}>
              <span className="column-bar" style={{ height: `${(item.value / max) * 100}%` }} />
              <span className="column-label">{item.label}</span>
            </span>
          ))}
        </div>
      )}
    </figure>
  );
}
