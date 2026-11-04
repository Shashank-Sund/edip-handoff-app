import { AlertTriangle, CheckCheck, CircleAlert, CircleDot, Eye, FilePen, Loader2, PenLine, ShieldAlert } from 'lucide-react';
import { cn } from '@/lib/utils';
import type { Severity, WorkflowState } from '@/lib/types';

const SEV: Record<Severity, { label: string; cls: string; Icon: typeof CircleDot }> = {
  stable: { label: 'Stable', cls: 'text-sev-stable bg-sev-stable/10 ring-sev-stable/25', Icon: CircleDot },
  watcher: { label: 'Watcher', cls: 'text-sev-watcher bg-sev-watcher/10 ring-sev-watcher/30', Icon: Eye },
  unstable: { label: 'Unstable', cls: 'text-sev-unstable bg-sev-unstable/10 ring-sev-unstable/25', Icon: ShieldAlert },
};

export function SeverityBadge({ value, size = 'sm', className }: { value: Severity | null | undefined; size?: 'sm' | 'lg'; className?: string }) {
  if (!value) return <span className="text-xs text-muted-foreground">n/a</span>;
  const s = SEV[value];
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-full font-medium ring-1 ring-inset whitespace-nowrap',
        size === 'lg' ? 'px-3 py-1 text-sm [&>svg]:size-4' : 'px-2 py-0.5 text-xs [&>svg]:size-3.5',
        s.cls,
        className,
      )}
    >
      <s.Icon aria-hidden />
      {s.label}
    </span>
  );
}

const STATE: Record<WorkflowState, { label: string; cls: string; Icon: typeof CircleDot }> = {
  drafting: { label: 'Drafting', cls: 'text-state-draft bg-muted ring-border', Icon: Loader2 },
  draft: { label: 'Draft', cls: 'text-state-draft bg-muted ring-border', Icon: FilePen },
  signed: { label: 'Signed', cls: 'text-state-signed bg-state-signed/10 ring-state-signed/25', Icon: PenLine },
  changed: { label: 'Changed after signing', cls: 'text-flag bg-flag/12 ring-flag/30', Icon: AlertTriangle },
  acknowledged: { label: 'Acknowledged', cls: 'text-state-ack bg-state-ack/10 ring-state-ack/25', Icon: CheckCheck },
  transferred_unsigned: { label: 'Transferred without sign off', cls: 'text-sev-unstable bg-sev-unstable/10 ring-sev-unstable/25', Icon: CircleAlert },
};

export function StateBadge({ state, changed, compact, wrap }: { state: WorkflowState; changed?: boolean; compact?: boolean; wrap?: boolean }) {
  const s = STATE[state];
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className={cn('inline-flex items-center gap-1 px-2 py-0.5 text-xs font-medium ring-1 ring-inset [&>svg]:size-3.5 [&>svg]:shrink-0', wrap ? 'w-max max-w-[9.5rem] rounded-lg leading-tight' : 'rounded-full whitespace-nowrap', s.cls)}>
        <s.Icon aria-hidden />
        {!compact && s.label}
        {compact && <span className="sr-only">{s.label}</span>}
      </span>
      {changed && (
        <span className="inline-flex items-center gap-1 rounded-full bg-flag/12 px-2 py-0.5 text-xs font-medium text-flag ring-1 ring-inset ring-flag/30">
          <AlertTriangle className="size-3.5" aria-hidden />
          Updated
        </span>
      )}
    </span>
  );
}

/** A small ring that shows completeness; the number is always printed next to it. */
export function ProgressRing({ value, size = 28, stroke = 3.5, blocking }: { value: number; size?: number; stroke?: number; blocking?: number }) {
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const tone = blocking ? 'text-sev-watcher' : 'text-sev-stable';
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="-rotate-90 shrink-0" aria-hidden>
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" strokeWidth={stroke} className="stroke-muted" />
      <circle
        cx={size / 2}
        cy={size / 2}
        r={r}
        fill="none"
        strokeWidth={stroke}
        strokeLinecap="round"
        strokeDasharray={c}
        strokeDashoffset={c * (1 - Math.max(0, Math.min(1, value)))}
        className={cn('stroke-current transition-[stroke-dashoffset] duration-500', tone)}
      />
    </svg>
  );
}

export function Pill({ children, tone = 'neutral', className }: { children: React.ReactNode; tone?: 'neutral' | 'danger' | 'warn' | 'info'; className?: string }) {
  const tones = {
    neutral: 'bg-muted text-foreground ring-border',
    danger: 'bg-sev-unstable/10 text-sev-unstable ring-sev-unstable/25',
    warn: 'bg-sev-watcher/10 text-sev-watcher ring-sev-watcher/30',
    info: 'bg-state-signed/10 text-state-signed ring-state-signed/25',
  };
  return (
    <span className={cn('inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs font-medium ring-1 ring-inset whitespace-nowrap [&>svg]:size-3.5', tones[tone], className)}>
      {children}
    </span>
  );
}
