import { Eye, Sparkles } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Explain } from '@/components/guide/Explain';
import { sinceLabel } from '@/lib/since';
import { ago } from '@/lib/format';
import type { SinceCount } from '@/lib/types';

/**
 * What changed since you last looked at a board row's patient: "New · 3 changes", or "Not opened yet" when you never
 * opened the patient; nothing when nothing is new. plain: no explanation (inside a link).
 */
export function SinceMark({ since, at, plain }: { since?: SinceCount; at: string; plain?: boolean }) {
  if (!since) return null;
  const label = sinceLabel(since);
  if (!label && !since.first_time) return null;
  const chip = label ? (
    <span className="inline-flex items-center gap-1 whitespace-nowrap rounded-full bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary ring-1 ring-inset ring-primary/20">
      <Sparkles className="size-3" aria-hidden />
      {label}
    </span>
  ) : (
    <span className="whitespace-nowrap text-xs text-muted-foreground">Not opened yet</span>
  );
  if (plain) return chip;
  const text = label
    ? `${since.count === 1 ? 'One change' : `${since.count} changes`} by others since you last looked, ${ago(since.last_look, at)}: new drafts, sign offs, edits, tasks, owners. Open the patient to see them, or Mark as seen.`
    : 'You have not opened this patient yet, so there is nothing to compare with. Open the patient, or Mark as seen.';
  return (
    // The row opens the handoff on click; the explanation must not.
    <span onClick={(e) => e.stopPropagation()}>
      <Explain topic="since.board" text={text}>
        {chip}
      </Explain>
    </span>
  );
}

/** Mark as seen: records a look at the patient without opening it, so the marker goes. */
export function MarkSeenButton({ name, busy, disabled, onMark }: { name: string; busy: boolean; disabled: boolean; onMark: () => void }) {
  return (
    <Button
      size="sm"
      variant="ghost"
      className="h-6 gap-1 px-1.5 text-xs text-muted-foreground hover:text-foreground"
      disabled={disabled || busy}
      aria-label={`Mark as seen: ${name}`}
      onClick={(e) => {
        e.stopPropagation();
        e.preventDefault();
        onMark();
      }}
    >
      <Eye className="size-3.5" aria-hidden />
      Mark as seen
    </Button>
  );
}
