/**
 * Time series on TradingView Lightweight Charts (Apache 2.0; attribution in the footer of How it works).
 * Thin lines, hairline grid, muted axis text, a crosshair, and a legend that shows the values under the
 * crosshair (or the latest values). Times are real UTC; labels are formatted in facility time.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import {
  AreaSeries,
  ColorType,
  CrosshairMode,
  LineSeries,
  LineStyle,
  TickMarkType,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type MouseEventParams,
  type SeriesType,
  type UTCTimestamp,
} from 'lightweight-charts';
import { useIsDark, useReducedMotion } from '@/hooks/useMedia';
import { resolveColor, withAlpha } from '@/lib/colors';
import { timeZone } from '@/lib/format';
import { cn } from '@/lib/utils';

export interface Line {
  key: string;
  label: string;
  color: string; // css color or var(--token)
  unit?: string;
  data: { t: string; v: number | null | undefined }[];
  pane?: number;
  scale?: 'left' | 'right';
  dashed?: boolean;
  area?: boolean;
  points?: boolean;
  format?: (v: number) => string;
}

const FONT = "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif";
/** A formatter in the facility time zone (from /api/config), built again if the zone changes. */
function fmt(o: Intl.DateTimeFormatOptions) {
  let zone = '', f: Intl.DateTimeFormat | null = null;
  return {
    format: (d: Date) => {
      if (!f || zone !== timeZone()) f = new Intl.DateTimeFormat('en-US', { timeZone: (zone = timeZone()), ...o });
      return f.format(d);
    },
  };
}
const HM = fmt({ hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
const WD = fmt({ weekday: 'short' });
const DN = fmt({ day: 'numeric' });
const DAY = { format: (d: Date) => `${WD.format(d)} ${DN.format(d)}` };
const FULL = fmt({ weekday: 'short', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });

const sec = (iso: string) => Math.floor(new Date(iso).getTime() / 1000) as UTCTimestamp;

export function TimeChart({ lines, height = 260, stretch, ariaLabel, className, markerTime }: {
  lines: Line[];
  height?: number;
  stretch?: number[];
  ariaLabel: string;
  className?: string;
  /** Draw a vertical marker at this time (the app clock). */
  markerTime?: string;
}) {
  const el = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<Map<string, ISeriesApi<SeriesType>>>(new Map());
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [hover, setHover] = useState<{ t: number; values: Record<string, number> } | null>(null);
  const isDark = useIsDark();
  const reduced = useReducedMotion();
  const clean = useMemo(
    () =>
      lines.map((l) => {
        const seen = new Map<number, number>();
        for (const p of l.data) if (p.v != null && Number.isFinite(p.v)) seen.set(sec(p.t), p.v);
        return { ...l, points: l.points, rows: [...seen.entries()].sort((a, b) => a[0] - b[0]).map(([time, value]) => ({ time: time as UTCTimestamp, value })) };
      }),
    [lines],
  );
  const signature = clean.map((l) => `${l.key}:${l.rows.length}:${l.rows.at(-1)?.time ?? ''}`).join('|');

  useEffect(() => {
    const node = el.current;
    if (!node) return;
    const c = createChart(node, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: 'transparent' }, fontFamily: FONT, fontSize: 11, attributionLogo: false, panes: { enableResize: false } },
      crosshair: { mode: CrosshairMode.Magnet },
      rightPriceScale: { visible: clean.some((l) => (l.scale ?? 'right') === 'right'), scaleMargins: { top: 0.12, bottom: 0.08 } },
      leftPriceScale: { visible: clean.some((l) => l.scale === 'left'), scaleMargins: { top: 0.12, bottom: 0.08 } },
      timeScale: {
        timeVisible: true,
        secondsVisible: false,
        fixLeftEdge: true,
        fixRightEdge: true,
        rightOffset: 2,
        tickMarkFormatter: (t: number, type: TickMarkType) => {
          const d = new Date(t * 1000);
          return type <= TickMarkType.DayOfMonth ? DAY.format(d) : HM.format(d);
        },
      },
      localization: { timeFormatter: (t: number) => FULL.format(new Date(t * 1000)).replace(',', '') },
      handleScroll: { vertTouchDrag: false },
      kineticScroll: { mouse: !reduced, touch: !reduced },
    });
    chart.current = c;
    const map = new Map<string, ISeriesApi<SeriesType>>();
    for (const l of clean) {
      const common = {
        lineWidth: 2 as const,
        priceLineVisible: false,
        lastValueVisible: false,
        priceScaleId: l.scale ?? 'right',
        lineStyle: l.dashed ? LineStyle.Dashed : LineStyle.Solid,
        // Axis ticks keep their precision (a rounded 96.5 would print a second "97"); the legend uses l.format.
        priceFormat: { type: 'custom' as const, formatter: (v: number) => (Number.isInteger(v) ? String(v) : v.toFixed(1)), minMove: 0.1 },
        crosshairMarkerRadius: 3.5,
      };
      const s = l.area
        ? c.addSeries(AreaSeries, { ...common }, l.pane ?? 0)
        : c.addSeries(LineSeries, { ...common, pointMarkersVisible: l.points ?? l.rows.length < 30, pointMarkersRadius: 2.5 }, l.pane ?? 0);
      s.setData(l.rows);
      map.set(l.key, s);
    }
    series.current = map;
    const panes = c.panes();
    stretch?.forEach((f, i) => panes[i]?.setStretchFactor(f));
    c.timeScale().fitContent();
    const onMove = (p: MouseEventParams) => {
      if (typeof p.time !== 'number' || !p.point || p.point.x < 0) {
        setHover(null);
        return;
      }
      const values: Record<string, number> = {};
      for (const [k, s] of map) {
        const d = p.seriesData.get(s) as { value?: number } | undefined;
        if (d?.value != null) values[k] = d.value;
      }
      setHover({ t: p.time, values });
    };
    c.subscribeCrosshairMove(onMove);
    return () => {
      c.unsubscribeCrosshairMove(onMove);
      c.remove();
      chart.current = null;
      series.current = new Map();
    };
    // Rebuild when the data shape changes (new points as the clock moves).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature, reduced]);

  // Theme colors from CSS tokens.
  useEffect(() => {
    const c = chart.current;
    if (!c) return;
    const text = resolveColor('var(--chart-axis)');
    const grid = resolveColor('var(--chart-grid)');
    const border = resolveColor('var(--border)');
    c.applyOptions({
      layout: { textColor: text, panes: { separatorColor: border, separatorHoverColor: border } },
      grid: { vertLines: { color: grid }, horzLines: { color: grid } },
      rightPriceScale: { borderColor: border },
      leftPriceScale: { borderColor: border },
      timeScale: { borderColor: border },
      crosshair: {
        vertLine: { color: withAlpha(text, 0.45), labelBackgroundColor: resolveColor('var(--foreground)') },
        horzLine: { color: withAlpha(text, 0.45), labelBackgroundColor: resolveColor('var(--foreground)') },
      },
    });
    for (const l of clean) {
      const s = series.current.get(l.key);
      const col = resolveColor(l.color);
      if (!s) continue;
      if (l.area) s.applyOptions({ lineColor: col, topColor: withAlpha(col, 0.22), bottomColor: withAlpha(col, 0.02) });
      else s.applyOptions({ color: col });
    }
  }, [isDark, signature, clean]);

  useEffect(() => {
    for (const [k, s] of series.current) s.applyOptions({ visible: !hidden.has(k) });
  }, [hidden, signature]);

  const latest = (l: (typeof clean)[number]) => l.rows.at(-1)?.value;
  const value = (l: (typeof clean)[number]) => (hover ? hover.values[l.key] : latest(l));
  const hasData = clean.some((l) => l.rows.length);
  return (
    <figure className={cn('flex flex-col gap-2', className)} aria-label={ariaLabel}>
      <figcaption className="flex min-h-5 flex-wrap items-center gap-x-1 gap-y-1 text-xs">
        {clean.map((l) => {
          const off = hidden.has(l.key);
          const v = value(l);
          return (
            <button
              key={l.key}
              type="button"
              aria-pressed={!off}
              onClick={() => setHidden((h) => {
                const n = new Set(h);
                if (n.has(l.key)) n.delete(l.key);
                else n.add(l.key);
                return n;
              })}
              className={cn('inline-flex items-center gap-1.5 rounded-md px-1.5 py-0.5 outline-none transition-opacity hover:bg-accent focus-visible:ring-[3px] focus-visible:ring-ring/50', off && 'opacity-40')}
            >
              <span className={cn('h-0.5 w-3 rounded-full', l.dashed && 'border-t-2 border-dashed bg-transparent')} style={{ background: l.dashed ? undefined : l.color, borderColor: l.color }} aria-hidden />
              <span className="text-muted-foreground">{l.label}</span>
              <span className="font-semibold tabular-nums">
                {v == null ? 'n/a' : (l.format ?? ((x: number) => String(Math.round(x * 10) / 10)))(v)}
                {v != null && l.unit ? <span className="ml-0.5 font-normal text-muted-foreground">{l.unit}</span> : null}
              </span>
            </button>
          );
        })}
        <span className="ml-auto text-muted-foreground tabular-nums">{hover ? FULL.format(new Date(hover.t * 1000)).replace(',', '') : markerTime ? 'Latest' : ''}</span>
      </figcaption>
      <div className="relative" style={{ height }}>
        <div ref={el} className="absolute inset-0" aria-hidden />
        {!hasData && <div className="absolute inset-0 grid place-items-center text-sm text-muted-foreground">No data yet</div>}
      </div>
    </figure>
  );
}
