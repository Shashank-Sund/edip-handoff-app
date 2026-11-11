import { Sparkles } from 'lucide-react';
import { HelpButton } from '@/components/guide/HelpButton';
import { sinceTarget, sinceText, sinceTitle, sinceWhen } from '@/lib/since';
import type { Since, SinceItem } from '@/lib/types';

const FIRST = 6;

/**
 * What changed since you last looked, at the top of the handoff page: the changes by others between your previous
 * look and the time on the clock, newest first. Each goes to where it is: the item on its template tab, the sign off,
 * the tasks, the owners or the patient. Nothing when nothing changed or this is your first look.
 */
export function SinceBanner({ since, onGo }: { since: Since | null; onGo: (template: string | undefined, anchor: string) => void }) {
  if (!since || since.first_time || !since.items.length) return null;
  const go = (x: SinceItem) => {
    const t = sinceTarget(x);
    onGo(t.template, t.anchor);
  };
  const rest = since.items.length - FIRST;
  return (
    <section aria-labelledby="since-title" className="rounded-xl border border-primary/25 bg-primary/5 px-4 py-3 text-sm sm:px-5">
      <div className="flex items-center justify-between gap-2">
        <h2 id="since-title" className="flex items-center gap-2 font-semibold">
          <Sparkles className="size-4 text-primary" aria-hidden />
          {sinceTitle(since)}
        </h2>
        <HelpButton topic="since.banner" label="About Since you last looked" className="size-6" />
      </div>
      <ul className="mt-2 space-y-1">
        {since.items.slice(0, rest > 1 ? FIRST : since.items.length).map((x, i) => (
          <li key={i} className="flex items-baseline gap-2">
            <span className="w-11 shrink-0 text-xs tabular-nums text-muted-foreground">{sinceWhen(x)}</span>
            <button type="button" onClick={() => go(x)} className="min-w-0 rounded-sm text-left underline-offset-4 outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring/60">
              {sinceText(x)}
            </button>
          </li>
        ))}
      </ul>
      {rest > 1 && <More items={since.items.slice(FIRST)} go={go} />}
    </section>
  );
}

function More({ items, go }: { items: SinceItem[]; go: (x: SinceItem) => void }) {
  return (
    <details className="mt-1">
      <summary className="cursor-pointer text-xs font-medium text-primary outline-none focus-visible:ring-2 focus-visible:ring-ring/60">
        {items.length} more
      </summary>
      <ul className="mt-1 space-y-1">
        {items.map((x, i) => (
          <li key={i} className="flex items-baseline gap-2">
            <span className="w-11 shrink-0 text-xs tabular-nums text-muted-foreground">{sinceWhen(x)}</span>
            <button type="button" onClick={() => go(x)} className="min-w-0 rounded-sm text-left underline-offset-4 outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring/60">
              {sinceText(x)}
            </button>
          </li>
        ))}
      </ul>
    </details>
  );
}
