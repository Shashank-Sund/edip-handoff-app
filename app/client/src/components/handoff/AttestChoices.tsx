import { useId, useState } from 'react';
import { Check, Pencil, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Pill } from '@/components/common/badges';
import { Explain } from '@/components/guide/Explain';
import type { Element, Rule } from '@/lib/types';

/** Words typed for a choice such as Other (the isolation type) or Add an allergy (the allergen and reaction). */
export interface Entry {
  entry: string;
  entry_note?: string;
}
export type AttestFn = (r: Rule, option: string, entry?: Entry) => Promise<boolean> | void;

const MAX = 2000;

/**
 * The fixed choices of a rule, one tap each. A choice that needs words (Other, Add an allergy) opens a short form
 * first. The first choice confirms there is nothing to add; any other enters a value on the item.
 */
export function AttestChoices({ r, onAttest, busy, onDone }: { r: Rule; onAttest: AttestFn; busy?: boolean; onDone?: () => void }) {
  const [typing, setTyping] = useState<string | null>(null);
  const [value, setValue] = useState('');
  const [note, setNote] = useState('');
  const id = useId();
  const options = r.attest_options ?? [];
  const typed = r.attest_typed ?? {};
  if (!options.length) return null;
  const field = typing ? typed[typing] : null;
  const tooLong = value.trim().length > MAX || note.trim().length > MAX;
  const choose = async (o: string) => {
    const ok = await onAttest(r, o);
    if (ok !== false) onDone?.();
  };
  return (
    <div className="mt-2 space-y-2">
      <div className="flex flex-wrap gap-1.5" role="group" aria-label={`Choices for ${r.label}`}>
        {options.map((o) =>
          typed[o] ? (
            <Button key={o} size="sm" variant={typing === o ? 'secondary' : 'outline'} className="h-7 px-2 text-xs" disabled={busy} aria-expanded={typing === o} onClick={() => setTyping(typing === o ? null : o)}>
              <Pencil className="size-3" />
              {o}
            </Button>
          ) : (
            <Button key={o} size="sm" variant="outline" className="h-7 px-2 text-xs" disabled={busy} data-choice onClick={() => choose(o)}>
              <Check className="size-3" />
              {o}
            </Button>
          ),
        )}
      </div>
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
              onDone?.();
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

/** The amber tag on a value the ED clinician entered: it is not in the chart yet. */
export function EnteredTag({ entered }: { entered: NonNullable<Element['entered']> }) {
  return (
    <Explain
      topic="handoff.entered"
      label="Not yet in the chart"
      text="Entered by the ED clinician during sign off. When the chart has a value, the chart value shows here and this one stays in History."
    >
      <Pill tone="warn" className="whitespace-normal text-left underline decoration-dotted underline-offset-[3px]">
        Entered by {entered.by}, not yet in the chart
      </Pill>
    </Explain>
  );
}

/** The value as the item shows it: "Penicillin: hives" for an allergy with a reaction. */
export function enteredText(entered: NonNullable<Element['entered']>): string {
  return entered.note ? `${entered.value}: ${entered.note}` : entered.value;
}
