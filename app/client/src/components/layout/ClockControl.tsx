import { CalendarClock, ChevronLeft, ChevronRight, Pause, Play, RotateCcw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { WithTooltip } from '@/components/ui/tooltip';
import { useApp } from '@/hooks/useApp';
import { api } from '@/lib/api';
import { clock, day } from '@/lib/format';
import { HelpButton } from '@/components/guide/HelpButton';

const HOUR = 3600000;

/** Replay clock: the mock data is a reference week, so the app can be viewed at any moment of it. */
export function ClockControl() {
  const { config, at, setAt, playing, setPlaying, bump } = useApp();
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
  const days: number[] = [];
  for (let t = start; t < end; t += 24 * HOUR) days.push(t);
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
              The board, handoffs and analytics show the hospital as it was at this time. Facility time (Eastern).
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
            {days.map((t) => (
              <Button
                key={t}
                variant={now >= t && now < t + 24 * HOUR ? 'secondary' : 'ghost'}
                size="sm"
                className="h-7 px-1 text-xs"
                onClick={() => setAt(new Date(t + (now - start) % (24 * HOUR)).toISOString())}
              >
                {day(new Date(t).toISOString()).replace(/,.*/, '')}
              </Button>
            ))}
          </div>
          <div className="flex items-center justify-between gap-2 border-t pt-3">
            <Button variant="ghost" size="sm" onClick={() => setAt(config.default_at)}>
              Busiest hour
            </Button>
            {config.mode === 'mock' && (
              <Button
                variant="ghost"
                size="sm"
                onClick={async () => {
                  await api.reset();
                  bump();
                }}
              >
                <RotateCcw />
                Clear my actions
              </Button>
            )}
          </div>
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
