/** Small look alikes of the app's controls and badges, and layout helpers, for the guide text. */
import type { ReactNode } from 'react';
import { AlertTriangle, Check, CircleAlert, Sparkles } from 'lucide-react';
import { ProgressRing } from '@/components/common/badges';
import { linkProps } from '@/lib/router';

// ------------------------------------------------------------------------------------ small visual cues
// Look alikes of the real controls, so a reader can match the words to the screen. They are not interactive.

/** A control as it looks on screen: an outlined button or key with an optional icon. */
export function Key({ children, label }: { children: ReactNode; label?: string }) {
  return (
    <span className="inline-flex items-center gap-1 whitespace-nowrap rounded-md border bg-card px-1.5 py-0.5 align-middle text-xs font-medium text-foreground shadow-xs [&>svg]:size-3.5">
      {children}
      {label && <span className="sr-only">{label}</span>}
    </span>
  );
}

/** The blue primary button (Sign, Accept handoff). */
export function Primary({ children }: { children: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1 whitespace-nowrap rounded-md bg-primary px-2 py-0.5 align-middle text-xs font-medium text-primary-foreground [&>svg]:size-3.5">
      {children}
    </span>
  );
}

/** The REQUIRED and STALE labels in the Sign off panel. */
export function Tag({ tone, children }: { tone: 'danger' | 'warn'; children: ReactNode }) {
  return <span className={`align-middle text-[11px] font-medium uppercase tracking-wide ${tone === 'danger' ? 'text-sev-unstable' : 'text-sev-watcher'}`}>{children}</span>;
}

/** The amber message under an item that must be resolved. */
export function AmberNote({ children }: { children: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-md bg-sev-watcher/10 px-2 py-0.5 align-middle text-xs font-medium">
      <AlertTriangle className="size-3.5 text-sev-watcher" aria-hidden />
      {children}
    </span>
  );
}

/** The grey reminder that does not block. */
export function GreyNote({ children }: { children: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-md bg-muted/60 px-2 py-0.5 align-middle text-xs font-medium text-muted-foreground">
      <CircleAlert className="size-3.5" aria-hidden />
      {children}
    </span>
  );
}

export function ModelMark() {
  return (
    <span className="inline-flex items-center gap-1 whitespace-nowrap align-middle text-xs text-muted-foreground">
      <Sparkles className="size-3" aria-hidden />
      Drafted by the model
    </span>
  );
}

export function Chip({ n }: { n: number }) {
  return <span className="inline-grid size-[18px] place-items-center rounded bg-primary/10 align-middle text-[10px] font-semibold text-primary">{n}</span>;
}

export function Highlight({ children }: { children: ReactNode }) {
  return <mark className="rounded-sm bg-flag/15 px-0.5 text-inherit underline decoration-flag decoration-dotted underline-offset-4">{children}</mark>;
}

export function Ring({ value, blocking, label }: { value: number; blocking?: number; label: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap align-middle">
      <ProgressRing value={value} size={18} stroke={3} blocking={blocking} />
      {label}
    </span>
  );
}

/** A link to another part of the guide. Works on the guide page and from the ? side panel. */
export function GuideLink({ to, children }: { to: string; children: ReactNode }) {
  return (
    <a {...linkProps(`/guide#${to}`)} className="font-medium text-primary underline-offset-4 hover:underline">
      {children}
    </a>
  );
}

/** A link to another page of the app, such as the Templates page. */
export function PageLink({ to, children }: { to: string; children: ReactNode }) {
  return (
    <a {...linkProps(to)} className="font-medium text-primary underline-offset-4 hover:underline">
      {children}
    </a>
  );
}

// ------------------------------------------------------------------------------------ layout helpers

export function Terms({ items }: { items: [ReactNode, ReactNode][] }) {
  return (
    <dl className="divide-y rounded-lg border">
      {items.map(([k, v], i) => (
        <div key={i} className="grid gap-1 px-3 py-2.5 sm:grid-cols-[10.5rem_1fr] sm:gap-4">
          <dt className="font-medium">{k}</dt>
          <dd className="text-muted-foreground">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Checks({ items }: { items: ReactNode[] }) {
  return (
    <ul className="space-y-1.5">
      {items.map((t, i) => (
        <li key={i} className="flex gap-2">
          <Check className="mt-0.5 size-4 shrink-0 text-sev-stable" aria-hidden />
          <span>{t}</span>
        </li>
      ))}
    </ul>
  );
}

export function Note({ children }: { children: ReactNode }) {
  return <p className="rounded-lg bg-primary/5 px-3 py-2.5 text-[13px] text-foreground ring-1 ring-inset ring-primary/15">{children}</p>;
}

/** One step of the walkthrough: what you will see, and what to do. */
export function SeeDo({ see, act }: { see: ReactNode; act: ReactNode }) {
  return (
    <dl className="grid gap-2 sm:grid-cols-2 sm:gap-3">
      <div className="rounded-lg bg-muted/50 px-3 py-2.5">
        <dt className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">You will see</dt>
        <dd className="mt-1">{see}</dd>
      </div>
      <div className="rounded-lg bg-primary/5 px-3 py-2.5 ring-1 ring-inset ring-primary/15">
        <dt className="text-xs font-semibold uppercase tracking-wide text-primary">Do this</dt>
        <dd className="mt-1">{act}</dd>
      </div>
    </dl>
  );
}

/** One row of the color key: a sample, then what it means. */
export function ColorRow({ swatch, title, children }: { swatch: string; title: string; children: ReactNode }) {
  return (
    <div className="flex gap-3 px-3 py-3">
      <span className={`mt-1 size-3 shrink-0 rounded-full ${swatch}`} aria-hidden />
      <div className="min-w-0 space-y-1.5">
        <div className="font-medium">{title}</div>
        <div className="flex flex-wrap items-center gap-1.5 text-muted-foreground">{children}</div>
      </div>
    </div>
  );
}

const ROLE_ROWS: { side: 'ED' | 'Unit'; role: string; does: ReactNode }[] = [
  { side: 'ED', role: 'ED nurse', does: <>Takes their patients, checks and <b>signs</b> the nurse handoff (SBAR).</> },
  { side: 'ED', role: 'ED physician', does: <>Takes their patients, checks and <b>signs</b> the physician handoff (I-PASS).</> },
  { side: 'Unit', role: 'Receiving nurse', does: <>Reads the nurse handoff and <b>accepts</b> it with a read back.</> },
  { side: 'Unit', role: 'Admitting physician', does: <>Reads the physician handoff and <b>accepts</b> it with a read back.</> },
];

export function WhoDoesWhat() {
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {(['ED', 'Unit'] as const).map((side) => (
        <div key={side} className="rounded-lg border p-3">
          <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">{side === 'ED' ? 'In the ED: you send' : 'On the unit: you receive'}</div>
          <ul className="space-y-2">
            {ROLE_ROWS.filter((r) => r.side === side).map((r) => (
              <li key={r.role}>
                <div className="font-medium">{r.role}</div>
                <div className="text-muted-foreground">{r.does}</div>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

