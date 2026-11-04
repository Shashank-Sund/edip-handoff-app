import { useState } from 'react';
import { CalendarClock, ChevronLeft, ChevronRight, Pause, Play, RotateCcw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { WithTooltip } from '@/components/ui/tooltip';
import { useApp } from '@/hooks/useApp';
import { api } from '@/lib/api';
import { clock, day, facilityDays, onDay, timeZoneName } from '@/lib/format';
import { HelpButton } from '@/components/guide/HelpButton';

const HOUR = 3600000;

/** Replay clock: the mock data is a reference week, so the app can be viewed at any moment of it. */
export function ClockControl() {
  const { config, at, setAt, playing, setPlaying, bump } = useApp();
  const [confirm, setConfirm] = useState(false);
  if (config.clock === 'live') {
    return (
      <span className="inline-flex items-center gap-2 rounded-full border px-3 py-1 text-sm">
        <span className="size-2 rounded-full bg-sev-stable motion-safe:animate-pulse" aria-hidden />
        Live <span className="tabular-nums text-muted-foreground">{clock(at)}</span>
      </span>
    );
  }
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
