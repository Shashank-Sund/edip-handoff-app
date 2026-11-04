import { useState } from 'react';
import { CalendarClock, ChevronLeft, ChevronRight, History, Pause, Play, RotateCcw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { WithTooltip } from '@/components/ui/tooltip';
import { NOW_SLACK_MS, useApp } from '@/hooks/useApp';
import { api } from '@/lib/api';
import { clock, dateTime, day, facilityDays, onDay, span, timeZoneName, type FacilityDay } from '@/lib/format';
import { HelpButton } from '@/components/guide/HelpButton';

const HOUR = 3600000;

/** The facility hours a clinician can look back to: on the hour, from the limit up to now (less the slack). */
function hoursOf(d: FacilityDay, min: number, max: number): { t: number; label: string }[] {
  const out: { t: number; label: string }[] = [];
  for (let t = d.start; t < d.end; t += HOUR) {
    if (t < min || t > max) continue;
    const label = clock(new Date(t).toISOString());
    // The hour a clock change repeats gets a second, distinct label.
    out.push({ t, label: out.some((x) => x.label === label) ? `${label}, again` : label });
  }
  return out;
}

/**
 * The live clock: the current time, and (EDIP_PAST_VIEW_HOURS) a day and hour picker to look at an earlier time,
 * read only. The Back to live bar is PastBanner, under the header.
 */
function LiveClock() {
  const { config, at, past, setPast } = useApp();
  const limit = (config.past_view_hours ?? 0) * HOUR;
  const label = (
    <span className="inline-flex items-center gap-2 rounded-full border px-3 py-1 text-sm">
      <span className="size-2 rounded-full bg-sev-stable motion-safe:animate-pulse" aria-hidden />
      Live <span className="tabular-nums text-muted-foreground">{clock(at)}</span>
    </span>
  );
  if (!limit) return label;
  const now = Date.now();
  const min = now - limit;
  const max = now - NOW_SLACK_MS;
  const shown = past ? new Date(past).getTime() : now;
  const days = facilityDays(new Date(min).toISOString(), new Date(now).toISOString());
  const today = days.find((d) => shown >= d.start && shown < d.end) ?? days[days.length - 1];
  const hours = hoursOf(today, min, max);
  const selected = past ? hours.filter((h) => h.t <= shown).at(-1) : undefined;
  // Pick a day: the same hour on it when allowed, else the nearest allowed hour.
  const pickDay = (d: FacilityDay) => {
    const options = hoursOf(d, min, max);
    if (!options.length) return;
    const want = new Date(onDay(new Date(shown).toISOString(), d)).getTime();
    const best = options.reduce((a, b) => (Math.abs(b.t - want) < Math.abs(a.t - want) ? b : a));
    setPast(new Date(best.t).toISOString());
  };
  return (
    <div className="flex items-center gap-1">
      {past ? (
        <WithTooltip label="Back 1 hour">
          <Button variant="ghost" size="icon-sm" onClick={() => setPast(new Date(shown - HOUR).toISOString())} disabled={shown - HOUR < min} aria-label="Back 1 hour">
            <ChevronLeft />
          </Button>
        </WithTooltip>
      ) : (
        label
      )}
      <Popover>
        <PopoverTrigger asChild>
          <Button variant="outline" size="sm" className={past ? 'min-w-0 gap-2 px-2.5 tabular-nums sm:min-w-44' : 'gap-2 px-2.5'}>
            <History className="text-muted-foreground" aria-hidden />
            {past ? (
              <>
                <span className="sr-only">Viewing an earlier time: </span>
                <span className="hidden sm:inline">{day(past)}</span>
                <span className="sr-only"> </span>
                <span className="font-semibold">{clock(past)}</span>
                <span className="sr-only">. Change</span>
              </>
            ) : (
              <span className="sr-only sm:not-sr-only">View an earlier time</span>
            )}
          </Button>
        </PopoverTrigger>
        <PopoverContent align="end" className="w-[min(92vw,22rem)] space-y-4" aria-label="View an earlier time">
          <div>
            <p className="text-sm font-medium">View an earlier time</p>
            <p className="mt-0.5 text-xs text-muted-foreground">
              See the board and handoffs as they were, up to {span(limit / HOUR)} back. It is read only, and the app records that you looked. Facility time ({timeZoneName()}).
            </p>
          </div>
          <div className="grid grid-cols-4 gap-1.5" role="group" aria-label="Day">
            {days.map((d) => {
              const on = !!past && d === today;
              return (
                <Button key={d.start} variant={on ? 'secondary' : 'ghost'} size="sm" className="h-7 px-1 text-xs" aria-pressed={on} disabled={!hoursOf(d, min, max).length} onClick={() => pickDay(d)}>
                  {d.label}
                </Button>
              );
            })}
          </div>
          <div className="flex items-center gap-2">
            <label htmlFor="past-hour" className="text-xs font-medium">
              Hour on {today.label}
            </label>
            <select
              id="past-hour"
              value={selected ? String(selected.t) : ''}
              onChange={(e) => e.target.value && setPast(new Date(Number(e.target.value)).toISOString())}
              className="h-8 flex-1 rounded-md border border-input bg-card px-2 text-sm tabular-nums shadow-xs outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/40 dark:bg-input/20"
            >
              {!selected && <option value="">Pick an hour</option>}
              {hours.map((h) => (
                <option key={h.t} value={String(h.t)}>
                  {h.label}
                </option>
              ))}
            </select>
          </div>
          {past && (
            <div className="flex justify-end border-t pt-3">
              <Button size="sm" onClick={() => setPast(null)}>
                Back to live
              </Button>
            </div>
          )}
        </PopoverContent>
      </Popover>
      {past && (
        <WithTooltip label="Forward 1 hour">
          <Button variant="ghost" size="icon-sm" onClick={() => setPast(new Date(shown + HOUR).toISOString())} aria-label="Forward 1 hour">
            <ChevronRight />
          </Button>
        </WithTooltip>
      )}
      <HelpButton topic="clock.earlier" label="About earlier times" className="hidden sm:inline-grid" />
    </div>
  );
}

/** Live, at an earlier time: says so under the header on every page, with the way back. */
export function PastBanner() {
  const { past, setPast } = useApp();
  if (!past) return null;
  return (
    <div role="region" aria-label="Earlier time" className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1.5 border-b border-flag/30 bg-flag/12 py-2 pl-4 pr-2 text-sm sm:pl-6 sm:pr-4">
      <p className="flex min-w-0 items-center gap-2 font-medium" role="status">
        <History className="size-4 shrink-0" aria-hidden />
        Viewing {dateTime(past)}, read only.
      </p>
      <div className="flex items-center gap-1">
        <HelpButton topic="clock.earlier" label="About earlier times" />
        <Button size="sm" onClick={() => setPast(null)}>
          Back to live
        </Button>
      </div>
    </div>
  );
}

/** Replay clock: the mock data is a reference week, so the app can be viewed at any moment of it. */
export function ClockControl() {
  const { config, at, setAt, playing, setPlaying, bump } = useApp();
  const [confirm, setConfirm] = useState(false);
  if (config.clock === 'live') return <LiveClock />;
  const start = new Date(config.window.start).getTime();
  const end = new Date(config.window.end).getTime();
  const now = new Date(at).getTime();
  const shift = (h: number) => setAt(new Date(now + h * HOUR).toISOString());
  // Calendar days at the facility, midnight to midnight in its time zone (23 or 25 hours on a clock change).
  const days = facilityDays(config.window.start, config.window.end);
  return (
    <div className="flex items-center gap-1">
      <WithTooltip label="Back 1 hour">
        <Button variant="ghost" size="icon-sm" onClick={() => shift(-1)} disabled={now <= start} aria-label="Back 1 hour">
          <ChevronLeft />
        </Button>
      </WithTooltip>
      <Popover>
        <PopoverTrigger asChild>
          <Button variant="outline" size="sm" className="min-w-0 gap-2 px-2.5 tabular-nums sm:min-w-44">
            <CalendarClock className="text-muted-foreground" aria-hidden />
            <span className="sr-only">Replay time </span>
            <span className="hidden sm:inline">{day(at)}</span>
            <span className="sr-only"> </span>
            <span className="font-semibold">{clock(at)}</span>
            <span className="sr-only">. Change</span>
          </Button>
        </PopoverTrigger>
        <PopoverContent align="end" className="w-[min(92vw,22rem)] space-y-4" aria-label="Replay the reference week">
          <div>
            <p className="text-sm font-medium">Replay the reference week</p>
            <p className="mt-0.5 text-xs text-muted-foreground">
              The board, handoffs and analytics show the hospital as it was at this time. Facility time ({timeZoneName()}).
            </p>
          </div>
          <input
            type="range"
            min={start}
            max={end}
            step={15 * 60000}
            value={now}
            onChange={(e) => setAt(new Date(Number(e.target.value)).toISOString())}
            className="w-full accent-[var(--primary)]"
            aria-label="Replay time"
          />
          <div className="grid grid-cols-4 gap-1.5">
            {days.map((d) => {
              const today = now >= d.start && now < d.end;
              return (
                <Button
                  key={d.start}
                  variant={today ? 'secondary' : 'ghost'}
                  size="sm"
                  className="h-7 px-1 text-xs"
                  aria-pressed={today}
                  onClick={() => setAt(onDay(at, d))}
                >
                  {d.label}
                </Button>
              );
            })}
          </div>
          <div className="flex items-center justify-between gap-2 border-t pt-3">
            <Button variant="ghost" size="sm" onClick={() => setAt(config.default_at)}>
              Busiest hour
            </Button>
            {config.mode === 'mock' && !confirm && (
              <Button variant="ghost" size="sm" onClick={() => setConfirm(true)}>
                <RotateCcw />
                Clear all demo actions
              </Button>
            )}
          </div>
          {config.mode === 'mock' && confirm && (
            <div role="alertdialog" aria-label="Clear all demo actions" className="space-y-2 rounded-lg border border-destructive/30 bg-destructive/5 p-3">
              <p className="text-xs">
                Clear every edit, attestation, sign off and read back in this demo{__STATIC_DEMO__ ? '' : ', for everyone using it'}? This cannot be undone.
              </p>
              <div className="flex justify-end gap-2">
                <Button variant="ghost" size="sm" onClick={() => setConfirm(false)} autoFocus>
                  Cancel
                </Button>
                <Button
                  variant="destructive"
                  size="sm"
                  onClick={async () => {
                    await api.reset();
                    setConfirm(false);
                    bump();
                  }}
                >
                  Clear all
                </Button>
              </div>
            </div>
          )}
        </PopoverContent>
      </Popover>
      <WithTooltip label="Forward 1 hour">
        <Button variant="ghost" size="icon-sm" onClick={() => shift(1)} disabled={now >= end} aria-label="Forward 1 hour">
          <ChevronRight />
        </Button>
      </WithTooltip>
      <WithTooltip label={playing ? 'Pause' : 'Play: 15 minutes every 1.5 seconds'}>
        <Button variant={playing ? 'secondary' : 'ghost'} size="icon-sm" onClick={() => setPlaying(!playing)} aria-pressed={playing} aria-label={playing ? 'Pause replay' : 'Play replay'}>
          {playing ? <Pause /> : <Play />}
        </Button>
      </WithTooltip>
      <HelpButton topic="clock" label="What is this clock?" className="hidden sm:inline-grid" />
    </div>
  );
}
