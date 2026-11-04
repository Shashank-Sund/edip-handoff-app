import { useMemo, useState } from 'react';
import { BedDouble, ChevronRight, ClipboardCheck, Clock3, Search, ShieldAlert, Users, X } from 'lucide-react';
import { Card } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Layout } from '@/components/layout/Layout';
import { Pill, ProgressRing, SeverityBadge, StateBadge } from '@/components/common/badges';
import { Stat } from '@/components/common/Stat';
import { HelpButton } from '@/components/guide/HelpButton';
import { Explain } from '@/components/guide/Explain';
import { EmptyState, ErrorState, PageSkeleton } from '@/components/common/states';
import { useApp } from '@/hooks/useApp';
import { useLoad } from '@/hooks/useLoad';
import { api } from '@/lib/api';
import { LEVEL_LABEL, ago, bed, duration, pct, triggerLabel } from '@/lib/format';
import { linkProps, navigate } from '@/lib/router';
import { cn } from '@/lib/utils';
import type { CaseSummary } from '@/lib/types';

type View = 'all' | 'boarding' | 'arrived';

/** One or two sentences per column, true to how the board computes it; the guide has the rest. */
function columnHelp(tplName: string): [string, string][] {
  return [
    ['Patient', 'Name, age, sex and medical record number. Click a row to open the handoff.'],
    ['Severity', 'How sick the patient is: stable, watcher or unstable. Rules set the lowest level the chart allows, and the model can raise it, never lower it.'],
    ['Reason for visit', 'The chief complaint. Underneath, from the chart: a code status other than full code, isolation, and allergies.'],
    ['Destination', 'The unit and bed the patient is going to, where they are now in the ED, and the level of care.'],
    ['Boarding', 'Time since the admit decision while the patient is still in the ED. Amber from 4 hours, red from 6.'],
    ['Handoffs', 'Where the nurse and the physician handoff stand: Draft, Signed, Acknowledged, Changed after signing, or Transferred without sign off.'],
    ['Completeness', `For your role's template (${tplName}): the share of its checks that are met or resolved. Open counts the required items still to resolve before signing.`],
    ['Last update', 'When the latest draft was written, and what changed in the chart to trigger it.'],
  ];
}

function Head({ label, help, className }: { label: string; help: string; className?: string }) {
  return (
    <th scope="col" className={cn('px-3 py-2.5 font-medium', className)}>
      {label}
      <span className="whitespace-nowrap">
        &nbsp;
        <Explain icon label={label} topic="board.columns" text={help} />
      </span>
    </th>
  );
}
const TEMPLATE_SHORT: Record<string, string> = { sbar_nurse: 'Nurse', ipass_physician: 'Physician' };

function BoardingTime({ minutes }: { minutes: number }) {
  const tone = minutes >= 360 ? 'bg-sev-unstable' : minutes >= 240 ? 'bg-sev-watcher' : 'bg-primary/70';
  return (
    <div className="min-w-24">
      <div className="text-sm font-medium tabular-nums">{duration(minutes)}</div>
      <div className="mt-1 h-1 w-full rounded-full bg-muted" aria-hidden>
        <div className={cn('h-1 rounded-full', tone)} style={{ width: `${Math.min(100, (minutes / 480) * 100)}%` }} />
      </div>
    </div>
  );
}

function Handoffs({ row }: { row: CaseSummary }) {
  return (
    <div className="flex flex-col gap-1">
      {(['sbar_nurse', 'ipass_physician'] as const).map((t) => {
        const s = row.templates[t];
        if (!s) return null;
        return (
          <div key={t} className="flex flex-col items-start gap-0.5">
            <span className="text-[11px] leading-none text-muted-foreground">{TEMPLATE_SHORT[t]}</span>
            <StateBadge state={s.workflow.state} changed={s.workflow.changed_since_acknowledged} wrap />
          </div>
        );
      })}
    </div>
  );
}

function Completeness({ row, template, label }: { row: CaseSummary; template: string; label?: string }) {
  const s = row.templates[template];
  if (!s) return null;
  const n = s.blocking.length;
  return (
    <div className="flex items-center gap-2.5">
      <ProgressRing value={s.score} blocking={n} />
      <div className="leading-tight">
        <div className="text-sm font-medium tabular-nums">
          {label && <span className="mr-1 text-xs font-normal text-muted-foreground">{label}</span>}
          {pct(s.score)}
        </div>
        <div className={cn('whitespace-nowrap text-xs', n ? 'text-sev-watcher' : 'text-muted-foreground')}>{n ? `${n} open` : 'Ready'}</div>
      </div>
    </div>
  );
}

function Destination({ row }: { row: CaseSummary }) {
  const d = row.destination;
  const level = d.level_of_care ? LEVEL_LABEL[d.level_of_care] ?? d.level_of_care : null;
  const from = row.phase === 'arrived' ? 'from the ED' : `from ${bed('ED', row.location.room, row.location.bed)}`;
  return (
    <div className="leading-tight">
      <div className="whitespace-nowrap text-sm font-medium">
        {d.pending_unit ? `${row.facility} ${bed(d.pending_unit, d.pending_room, d.pending_bed)}` : `${row.facility}, bed pending`}
      </div>
      <div className="mt-0.5 whitespace-nowrap text-xs text-muted-foreground">{[from, level].filter(Boolean).join(' · ')}</div>
    </div>
  );
}

function Patient({ row }: { row: CaseSummary }) {
  return (
    <div className="min-w-0 leading-tight">
      <a {...linkProps(`/handoff/${row.csn}`)} className="whitespace-nowrap font-medium outline-none hover:underline focus-visible:underline">
        {row.patient.name}
      </a>
      <div className="mt-0.5 whitespace-nowrap text-xs text-muted-foreground tabular-nums">
        {row.patient.age} {row.patient.sex} · MRN {row.patient.mrn}
      </div>
    </div>
  );
}

function Safety({ row }: { row: CaseSummary }) {
  // Values entered in the sign off show with a marker until the chart has them.
  const e = row.entered;
  const code = row.code_status ?? (e?.code_status ? `${e.code_status.value} · entered` : null);
  const isolation = row.isolation ?? (e?.isolation ? `${e.isolation.value} · entered` : null);
  const allergies = row.allergies.length ? row.allergies : (e?.allergies ?? []).map((a) => a.value);
  const mark = !row.allergies.length && allergies.length ? ' · entered' : '';
  return (
    <div className="flex flex-wrap gap-1">
      {code && code !== 'Full Code' && <Pill tone="danger">{code}</Pill>}
      {isolation && <Pill tone="warn">{isolation}</Pill>}
      {allergies.length > 0 && <Pill>{(allergies.length === 1 ? allergies[0] : `${allergies.length} allergies`) + mark}</Pill>}
    </div>
  );
}

export default function Board() {
  const { at, apiAt, persona, version, config } = useApp();
  const { data, error, retry } = useLoad(() => api.board(apiAt), [at, persona.id, version]);
  const [view, setView] = useState<View>('all');
  const [unsigned, setUnsigned] = useState(false);
  const [facility, setFacility] = useState<string>('all');
  const [q, setQ] = useState('');
  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return (data?.rows ?? []).filter(
      (r) =>
        (view === 'all' || r.phase === view) &&
        (!unsigned || (r.phase === 'arrived' && r.templates[data?.template ?? '']?.workflow.state === 'transferred_unsigned')) &&
        (facility === 'all' || r.facility === facility) &&
        (!needle || [r.patient.name, r.patient.mrn, r.chief_complaint, r.destination.pending_unit, r.service].some((v) => v?.toLowerCase().includes(needle))),
    );
  }, [data, view, facility, q, unsigned]);
  const scope = persona.unit ? `${persona.facility} ${persona.unit}` : 'All units';
  const title = persona.role === 'unit_nurse' ? `Incoming to ${persona.facility} ${persona.unit}` : 'ED boarding board';

  if (error && !data) return <Layout title={title}><ErrorState message={error} onRetry={retry} /></Layout>;
  if (!data) return <Layout title={title}><PageSkeleton /></Layout>;
  const k = data.kpis;
  const ack = persona.can_ack.length > 0;
  const sign = persona.can_sign.length > 0;
  const tplName = config.templates.find((t) => t.id === data.template)?.name ?? data.template;
  return (
    <Layout title={title} crumbs={`${scope} · ${persona.name}`} help="board">
      <div className="space-y-5 sm:space-y-6">
        <section aria-label="Summary" className={cn('grid grid-cols-2 gap-3 sm:gap-4', ack || sign ? 'lg:grid-cols-4' : 'lg:grid-cols-3')}>
          <Stat label="Boarding now" value={k.boarding} icon={<BedDouble />} hint={k.median_boarding_min != null ? `Median ${duration(k.median_boarding_min)} since admit decision` : 'No one is boarding'} />
          <Stat label="Unstable" value={k.unstable} tone="danger" icon={<ShieldAlert />} hint="Rule floor or model call" />
          {ack && <Stat label="To acknowledge" value={k.to_acknowledge ?? 0} tone="warn" icon={<ClipboardCheck />} hint={`${tplName} handoffs signed by the ED, waiting for your read back`} />}
          {sign && !ack && (
            <Stat label="To sign" value={k.to_sign ?? 0} icon={<ClipboardCheck />} hint={`${tplName} drafts and changed after signing; ${k.blocking ?? 0} with items to resolve first`}>
              {(k.left_unsigned ?? 0) > 0 && (
                <button
                  type="button"
                  onClick={() => {
                    setView('arrived');
                    setUnsigned(true);
                  }}
                  className="mt-1 w-fit rounded-sm text-left text-xs font-medium text-sev-unstable underline decoration-sev-unstable/40 underline-offset-4 outline-none hover:decoration-sev-unstable focus-visible:ring-2 focus-visible:ring-ring/60"
                >
                  {k.left_unsigned} left without sign off
                  <ChevronRight className="ml-0.5 inline size-3.5 align-[-3px]" aria-hidden />
                </button>
              )}
            </Stat>
          )}
          <Stat label="Arrived on unit" value={k.arrived} icon={<Users />} hint="Transferred in the last 12 hours" />
        </section>

        <Card className="overflow-hidden">
          <div className="flex flex-col gap-3 border-b p-3 sm:flex-row sm:items-center sm:justify-between sm:p-4">
            <div className="flex flex-wrap items-center gap-2">
              <div role="tablist" aria-label="Filter by status" className="inline-flex rounded-lg bg-muted p-0.5">
                {(['all', 'boarding', 'arrived'] as View[]).map((v) => (
                  <button
                    key={v}
                    role="tab"
                    aria-selected={view === v}
                    onClick={() => {
                      setView(v);
                      if (v !== 'arrived') setUnsigned(false);
                    }}
                    className={cn('rounded-md px-3 py-1 text-sm font-medium capitalize transition-colors outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50', view === v ? 'bg-card text-foreground shadow-xs' : 'text-muted-foreground hover:text-foreground')}
                  >
                    {v}
                    <span className="ml-1.5 text-xs tabular-nums text-muted-foreground">
                      {v === 'all' ? data.rows.length : data.rows.filter((r) => r.phase === v).length}
                    </span>
                  </button>
                ))}
              </div>
              {unsigned && (
                <span className="inline-flex items-center gap-1 rounded-full bg-sev-unstable/10 py-1 pl-2.5 pr-1 text-xs font-medium text-sev-unstable ring-1 ring-inset ring-sev-unstable/25">
                  Left without sign off
                  <button type="button" onClick={() => setUnsigned(false)} className="grid size-5 place-items-center rounded-full outline-none hover:bg-sev-unstable/15 focus-visible:ring-2 focus-visible:ring-ring/60" aria-label="Clear the Left without sign off filter">
                    <X className="size-3.5" aria-hidden />
                  </button>
                </span>
              )}
              {!persona.unit && (
                <div role="group" aria-label="Facility" className="inline-flex rounded-lg bg-muted p-0.5">
                  {['all', ...config.facilities.map((f) => f.code)].map((f) => (
                    <button
                      key={f}
                      aria-pressed={facility === f}
                      onClick={() => setFacility(f)}
                      className={cn('rounded-md px-3 py-1 text-sm font-medium transition-colors outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50', facility === f ? 'bg-card text-foreground shadow-xs' : 'text-muted-foreground hover:text-foreground')}
                    >
                      {f === 'all' ? 'Both hospitals' : f}
                    </button>
                  ))}
                </div>
              )}
            </div>
            <div className="flex items-center gap-1.5">
            <div className="relative flex-1 sm:w-72">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
              <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search name, MRN, unit" className="pl-8" aria-label="Search patients" />
            </div>
            <HelpButton topic="board.columns" label="How to read the board" />
            </div>
          </div>

          {rows.length === 0 ? (
            <EmptyState title={data.rows.length ? 'No patients match these filters' : 'No handoffs at this time'}>
              {data.rows.length ? 'Clear the search or choose another filter.' : 'Move the replay clock to a busier hour, or wait for the next admit decision.'}
            </EmptyState>
          ) : (
            <>
              <div className="hidden overflow-x-auto md:block">
                <table className="w-full text-sm">
                  <thead className="bg-muted/40 text-left text-xs font-medium text-muted-foreground">
                    <tr>
                      {columnHelp(tplName).map(([label, help], i, all) => (
                        <Head
                          key={label}
                          label={label === 'Completeness' ? `Completeness (${tplName})` : label}
                          help={help}
                          className={i === 0 ? 'pl-4 pr-3 lg:pl-5' : i === all.length - 1 ? 'pl-3 pr-4 lg:pr-5' : undefined}
                        />
                      ))}
                    </tr>
                  </thead>
                  <tbody className="divide-y">
                    {rows.map((r) => (
                      <tr key={r.csn} className="cursor-pointer align-middle transition-colors hover:bg-muted/40" onClick={() => navigate(`/handoff/${r.csn}`)}>
                        <td className="py-3 pl-4 pr-3 lg:pl-5">
                          <Patient row={r} />
                        </td>
                        <td className="px-3 py-3">
                          <SeverityBadge value={r.severity} />
                        </td>
                        <td className="max-w-52 px-3 py-3">
                          <div className="truncate">{r.chief_complaint}</div>
                          <div className="mt-1">
                            <Safety row={r} />
                          </div>
                        </td>
                        <td className="px-3 py-3">
                          <Destination row={r} />
                        </td>
                        <td className="px-3 py-3">
                          {r.phase === 'arrived' ? (
                            <div className="leading-tight">
                              <div className="text-sm font-medium">Arrived</div>
                              <div className="text-xs text-muted-foreground">after {duration(r.minutes_boarding)}</div>
                            </div>
                          ) : (
                            <BoardingTime minutes={r.minutes_boarding} />
                          )}
                        </td>
                        <td className="px-3 py-3">
                          <Handoffs row={r} />
                        </td>
                        <td className="px-3 py-3">
                          <Completeness row={r} template={data.template} />
                        </td>
                        <td className="py-3 pl-3 pr-4 lg:pr-5">
                          <div className="leading-tight">
                            <div className="flex items-center gap-1 whitespace-nowrap text-sm">
                              <Clock3 className="size-3.5 text-muted-foreground" aria-hidden />
                              {ago(r.last_update.as_of, at)}
                            </div>
                            <div className="whitespace-nowrap text-xs text-muted-foreground">{triggerLabel(r.last_update.trigger)}</div>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {/* The phone shows cards, which have no column titles: one ? explains what a card shows. */}
              <div className="flex items-center gap-1 border-b px-4 py-2 text-xs font-medium text-muted-foreground md:hidden">
                {rows.length} patient{rows.length === 1 ? '' : 's'}
                <Explain
                  icon
                  label="What a card shows"
                  topic="board.columns"
                  side="bottom"
                  text={`Each card: name and reason for visit, severity, destination and where the patient is now, boarding time, completeness of your role's template (${tplName}) with the items still open, and where the nurse and physician handoffs stand.`}
                />
              </div>
              <ul className="divide-y md:hidden">
                {rows.map((r) => (
                  <li key={r.csn}>
                    <a {...linkProps(`/handoff/${r.csn}`)} className="block space-y-3 p-4 outline-none hover:bg-muted/40 focus-visible:bg-muted/40">
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <div className="truncate font-medium">{r.patient.name}</div>
                          <div className="text-xs text-muted-foreground">
                            {r.patient.age} {r.patient.sex} · {r.chief_complaint}
                          </div>
                        </div>
                        <SeverityBadge value={r.severity} />
                      </div>
                      <Destination row={r} />
                      <div className="flex items-center justify-between gap-3">
                        {r.phase === 'arrived' ? <span className="text-sm">Arrived after {duration(r.minutes_boarding)}</span> : <BoardingTime minutes={r.minutes_boarding} />}
                        <Completeness row={r} template={data.template} label={tplName} />
                      </div>
                      <Handoffs row={r} />
                    </a>
                  </li>
                ))}
              </ul>
            </>
          )}
        </Card>
      </div>
    </Layout>
  );
}
