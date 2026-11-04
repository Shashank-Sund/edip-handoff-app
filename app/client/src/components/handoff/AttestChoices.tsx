import { useId, useRef, useState, type KeyboardEvent } from 'react';
import { Circle, CircleCheck, Pencil, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Pill } from '@/components/common/badges';
import { Explain } from '@/components/guide/Explain';
import { TEMPLATE_NAME } from '@/lib/labels';
import { enteredText } from '@/lib/entered';
import { cn } from '@/lib/utils';
import type { EnteredValue, Rule } from '@/lib/types';

/** Words typed for a choice such as Other (the isolation type) or Add an allergy (the allergen and reaction). */
export interface Entry {
  entry: string;
  entry_note?: string;
  /** Take an entered allergy off the list. */
  remove?: boolean;
}
export type AttestFn = (r: Rule, option: string, entry?: Entry) => Promise<boolean | void> | void;

const MAX = 2000;
const ALLERGY = 'Add an allergy';

/**
 * The fixed choices of a rule as one choice group: the first choice, which confirms there is nothing to add, set
 * apart ("None"), then the values to enter ("Or enter"). A choice that needs words (Other, Add an allergy) opens a
 * short form, filled with the current value when there is one. Only the chosen value has a check.
 */
export function ChoiceGroup({ r, entered = [], onAttest, busy }: { r: Rule; entered?: EnteredValue[]; onAttest: AttestFn; busy?: boolean }) {
  const options = r.attest_options ?? [];
  const typed = r.attest_typed ?? {};
  const allergies = r.element === 'E09';
  const current = entered.length ? entered[entered.length - 1] : null;
  const chosen = r.resolution?.kind === 'attested' ? (current?.option ?? r.resolution.option ?? null) : null;
  const [typing, setTyping] = useState<string | null>(null);
  const [value, setValue] = useState('');
  const [note, setNote] = useState('');
  const id = useId();
  const group = useRef<HTMLDivElement>(null);
  if (!options.length) return null;
  const [none, ...rest] = options;
  const field = typing ? typed[typing] : null;
  const tooLong = value.trim().length > MAX || note.trim().length > MAX;
  const open = (o: string) => {
    if (typing === o) return setTyping(null);
    // Changing a single typed value (Other): start from what is there now. A new allergy starts empty.
    const same = !allergies && current?.option === o;
    setValue(same ? current.value : '');
    setNote(same ? current.note ?? '' : '');
    setTyping(o);
  };
  const pick = (o: string) => (typed[o] ? open(o) : onAttest(r, o));
  // Arrow keys move between the choices (they do not choose: a choice is recorded at once); Enter or Space chooses.
  const onKey = (e: KeyboardEvent<HTMLButtonElement>) => {
    const keys = ['ArrowRight', 'ArrowDown', 'ArrowLeft', 'ArrowUp', 'Home', 'End'];
    if (!keys.includes(e.key)) return;
    e.preventDefault();
    const all = [...(group.current?.querySelectorAll<HTMLButtonElement>('[role="radio"]') ?? [])];
    const i = all.indexOf(e.currentTarget);
    const next = e.key === 'Home' ? 0 : e.key === 'End' ? all.length - 1 : (i + (e.key === 'ArrowRight' || e.key === 'ArrowDown' ? 1 : -1) + all.length) % all.length;
    all[next]?.focus();
  };
  const focusable = chosen ?? none;
  const radio = (o: string) => {
    const on = chosen === o;
    return (
      <button
        key={o}
        type="button"
        role="radio"
        aria-checked={on}
        tabIndex={o === focusable ? 0 : -1}
        disabled={busy}
        data-choice={typed[o] ? undefined : ''}
        onKeyDown={onKey}
        onClick={() => pick(o)}
        className={cn(
          'inline-flex h-7 items-center gap-1.5 rounded-md border px-2 text-xs font-medium outline-none transition-colors focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:opacity-60',
          on ? 'border-sev-stable/40 bg-sev-stable/10 text-foreground' : 'bg-card hover:bg-accent',
          typing === o && 'bg-secondary',
        )}
      >
        {on ? <CircleCheck className="size-3.5 text-sev-stable" aria-hidden /> : typed[o] ? <Pencil className="size-3 text-muted-foreground" aria-hidden /> : <Circle className="size-3.5 text-muted-foreground" aria-hidden />}
        {o}
      </button>
    );
  };
  return (
    <div className="mt-2 space-y-2">
      <div ref={group} role="radiogroup" aria-label={`${r.label}: choose one`} className="space-y-1.5">
        <div className="flex flex-wrap items-center gap-1.5">
          {rest.length > 0 && <span className="w-14 shrink-0 text-[11px] text-muted-foreground">None:</span>}
          {radio(none)}
        </div>
        {rest.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="w-14 shrink-0 text-[11px] text-muted-foreground">Or enter:</span>
            {rest.map(radio)}
          </div>
        )}
      </div>
      {allergies && entered.length > 0 && (
        <ul className="flex flex-wrap gap-1.5" aria-label="Allergies entered">
          {entered.map((x) => (
            <li key={x.value}>
              <span className="inline-flex items-center gap-1 rounded-md bg-sev-unstable/10 py-0.5 pl-2 pr-0.5 text-xs font-medium text-sev-unstable ring-1 ring-inset ring-sev-unstable/25">
                {enteredText(x)}
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => onAttest(r, ALLERGY, { entry: x.value, remove: true })}
                  className="grid size-5 place-items-center rounded outline-none hover:bg-sev-unstable/15 focus-visible:ring-2 focus-visible:ring-ring/60"
                  aria-label={`Remove ${x.value}`}
                >
                  <X className="size-3" aria-hidden />
                </button>
              </span>
            </li>
          ))}
        </ul>
      )}
      {typing && field && (
        <form
          className="space-y-2 rounded-lg border bg-muted/30 p-2.5"
          onSubmit={async (e) => {
            e.preventDefault();
            const ok = await onAttest(r, typing, field.note ? { entry: value, entry_note: note } : { entry: value });
            if (ok !== false) {
              setTyping(null);
              setValue('');
              setNote('');
            }
          }}
        >
          <div className={field.note ? 'grid gap-2 sm:grid-cols-2' : 'grid gap-2'}>
            <div className="space-y-1">
              <label htmlFor={`${id}-v`} className="block text-xs font-medium">{field.label}</label>
              <Input id={`${id}-v`} value={value} onChange={(e) => setValue(e.target.value)} className="h-8 text-sm" autoFocus />
            </div>
            {field.note && (
              <div className="space-y-1">
                <label htmlFor={`${id}-n`} className="block text-xs font-medium">{field.note}</label>
                <Input id={`${id}-n`} value={note} onChange={(e) => setNote(e.target.value)} className="h-8 text-sm" />
              </div>
            )}
          </div>
          {tooLong && <p className="text-xs text-sev-watcher">Keep it under 2,000 characters.</p>}
          <div className="flex gap-2">
            <Button type="submit" size="sm" className="h-7 text-xs" disabled={busy || !value.trim() || tooLong}>
              Save
            </Button>
            <Button type="button" size="sm" variant="ghost" className="h-7 text-xs" onClick={() => setTyping(null)}>
              <X />
              Cancel
            </Button>
          </div>
        </form>
      )}
    </div>
  );
}

/** The amber tag on values the ED entered: they are not in the chart yet. */
export function EnteredTag({ entered }: { entered: EnteredValue[] }) {
  const by = [...new Set(entered.map((x) => x.by))].join(' and ');
  const where = [...new Set(entered.map((x) => TEMPLATE_NAME[x.template] ?? x.template))].join(' and ');
  return (
    <Explain
      topic="handoff.entered"
      label="Not yet in the chart"
      text={`Entered by ${by} on the ${where} during sign off. When the chart has a value, the chart value shows here and this one stays in History.`}
    >
      <Pill tone="warn" className="whitespace-normal text-left underline decoration-dotted underline-offset-[3px]">
        Entered by {by}, not yet in the chart
      </Pill>
    </Explain>
  );
}
