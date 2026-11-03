import { createContext, useContext, useState, type ReactNode } from 'react';
import { FileText, FlaskConical, HeartPulse, Link2, Pill as PillIcon, ShieldCheck, User } from 'lucide-react';
import { Sheet, SheetContent } from '@/components/ui/sheet';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { dateTime } from '@/lib/format';
import { cn } from '@/lib/utils';

type Record_ = Record<string, unknown>;

interface EvidenceState {
  evidence: Record<string, Record_>;
  open: (src: string) => void;
}

const Ctx = createContext<EvidenceState>({ evidence: {}, open: () => {} });

/** Source ids look like note:900330947, obs:700121171:LACT:F, med:700120763:1, vital:<time>, enc:visit. */
export function sourceKind(src: string) {
  const k = src.split(':')[0];
  return (
    {
      note: { label: 'Note', Icon: FileText },
      obs: { label: 'Result', Icon: FlaskConical },
      vital: { label: 'Vital signs', Icon: HeartPulse },
      med: { label: 'Medication', Icon: PillIcon },
      allergy: { label: 'Allergy', Icon: ShieldCheck },
      flag: { label: 'Chart flag', Icon: ShieldCheck },
      homemed: { label: 'Home medication', Icon: PillIcon },
      problem: { label: 'Problem list', Icon: FileText },
    }[k] ?? { label: 'Encounter', Icon: User }
  );
}

function summary(src: string, r: Record_ | undefined): string {
  if (!r) return 'Source not in this draft';
  const kind = src.split(':')[0];
  if (kind === 'note') return `${r.type ?? 'Note'}${r.status ? `, ${String(r.status).toLowerCase()}` : ''}`;
  if (kind === 'obs') return `${r.name ?? 'Result'} ${r.value ?? ''} ${r.units ?? ''}`.trim();
  if (kind === 'med') return `${r.name ?? 'Medication'} ${r.dose ?? ''} ${r.unit ?? ''} ${r.route ?? ''}`.replace(/\s+/g, ' ').trim();
  if (kind === 'vital') return `Vitals at ${dateTime((r.taken_ts as string) ?? src.slice(6))}`;
  if (kind === 'allergy') return `${r.allergen}${r.reaction ? `: ${r.reaction}` : ''}`;
  return sourceKind(src).label;
}

const VITAL_LABEL: Record<string, string> = {
  hr: 'Heart rate', sbp: 'Systolic BP', dbp: 'Diastolic BP', map: 'MAP', rr: 'Resp. rate', spo2: 'SpO2', temp: 'Temperature',
  pain: 'Pain', gcs: 'GCS', o2_device: 'Oxygen', taken_ts: 'Taken',
};

function Detail({ src, r }: { src: string; r: Record_ | undefined }) {
  if (!r) return <p className="p-5 text-sm text-muted-foreground">This source is not part of the draft's evidence.</p>;
  const kind = src.split(':')[0];
  if (kind === 'note') {
    return (
      <div className="space-y-4 p-5">
        <dl className="grid grid-cols-2 gap-3 text-sm">
          <div><dt className="text-xs text-muted-foreground">Type</dt><dd className="font-medium">{String(r.type ?? 'Note')}</dd></div>
          <div><dt className="text-xs text-muted-foreground">Status</dt><dd className="font-medium">{String(r.status ?? 'n/a')}</dd></div>
          {'signed_ts' in r && <div className="col-span-2"><dt className="text-xs text-muted-foreground">Signed</dt><dd>{dateTime(r.signed_ts as string)}</dd></div>}
        </dl>
        {typeof r.text === 'string' && (
          <div className="whitespace-pre-wrap rounded-lg border bg-muted/30 p-4 text-sm leading-relaxed">{r.text}</div>
        )}
        {typeof r.text === 'string' && r.text.length >= 1800 && <p className="text-xs text-muted-foreground">Shown as sent to the model (first 1,800 characters).</p>}
      </div>
    );
  }
  const entries = Object.entries(r).filter(([, v]) => v !== null && v !== undefined && v !== '' && typeof v !== 'object');
  return (
    <dl className="divide-y px-5">
      {entries.map(([k, v]) => (
        <div key={k} className="flex items-baseline justify-between gap-6 py-2.5 text-sm">
          <dt className="text-muted-foreground">{VITAL_LABEL[k] ?? k.replace(/_/g, ' ').replace(/\bts\b/, 'time')}</dt>
          <dd className="text-right font-medium tabular-nums">{/_ts$/.test(k) ? dateTime(String(v)) : String(v)}</dd>
        </div>
      ))}
    </dl>
  );
}

export function EvidenceProvider({ evidence, children }: { evidence: Record<string, Record_>; children: ReactNode }) {
  const [src, setSrc] = useState<string | null>(null);
  const k = src ? sourceKind(src) : null;
  return (
    <Ctx.Provider value={{ evidence, open: setSrc }}>
      {children}
      <Sheet open={src !== null} onOpenChange={(o) => !o && setSrc(null)}>
        {src && k && (
          <SheetContent title={summary(src, evidence[src])} description={`${k.label} · source ${src}`}>
            <Detail src={src} r={evidence[src]} />
          </SheetContent>
        )}
      </Sheet>
    </Ctx.Provider>
  );
}

/** A numbered citation: hover for the source, click to open it. */
export function SourceChip({ src, n }: { src: string; n?: number }) {
  const { evidence, open } = useContext(Ctx);
  const known = src in evidence;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            open(src);
          }}
          className={cn(
            'mx-0.5 inline-grid h-[18px] min-w-[18px] place-items-center rounded px-1 align-[1px] text-[10px] font-semibold tabular-nums outline-none transition-colors focus-visible:ring-[3px] focus-visible:ring-ring/50',
            known ? 'bg-primary/10 text-primary hover:bg-primary/20' : 'bg-muted text-muted-foreground',
          )}
          aria-label={`Source${n ? ` ${n}` : ''}: ${summary(src, evidence[src])}`}
        >
          {n ?? <Link2 className="size-3" aria-hidden />}
        </button>
      </TooltipTrigger>
      <TooltipContent>{summary(src, evidence[src])}</TooltipContent>
    </Tooltip>
  );
}

export function SourceList({ sources }: { sources?: string[] }) {
  if (!sources?.length) return null;
  return (
    <span className="whitespace-nowrap">
      {sources.map((s, i) => (
        <SourceChip key={s} src={s} n={i + 1} />
      ))}
    </span>
  );
}

export function useEvidence() {
  return useContext(Ctx);
}
