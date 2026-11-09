import { Fragment, useEffect, useMemo, useState } from 'react';
import { BedDouble, BellRing, ChevronRight, ClipboardCheck, Clock3, Search, ShieldAlert, UserPlus, Users, X } from 'lucide-react';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Layout } from '@/components/layout/Layout';
import { Pill, ProgressRing, SeverityBadge, StateBadge } from '@/components/common/badges';
import { Owners } from '@/components/common/Owners';
import { TaskCount } from '@/components/common/TaskCount';
import { Stat } from '@/components/common/Stat';
import { HelpButton } from '@/components/guide/HelpButton';
import { Explain } from '@/components/guide/Explain';
import { EmptyState, ErrorState, PageSkeleton } from '@/components/common/states';
import { useApp } from '@/hooks/useApp';
import { useLoad } from '@/hooks/useLoad';
import { ApiError, api } from '@/lib/api';
import { LEVEL_LABEL, ago, bed, clock, duration, pct, triggerLabel } from '@/lib/format';
import { edRole, isMine, isUnassigned } from '@/lib/owners';
import { linkProps, navigate } from '@/lib/router';
import { cn } from '@/lib/utils';
import type { Board as BoardData, CaseSummary, EdRole, ShiftInfo } from '@/lib/types';

type View = 'all' | 'boarding' | 'arrived';
/** ED roles: My patients (yours, with the Unassigned at the top) or Everyone. */
type Scope = 'mine' | 'everyone';
interface Group {
  id: string;
  title?: string;
  hint?: string;
  rows: CaseSummary[];
}

/** One or two sentences per column, true to how the board computes it; the guide has the rest. */
function columnHelp(tplName: string): [string, string][] {
  return [
    ['Patient', 'Name, age, sex and medical record number, then who owns the patient: the ED nurse (RN) and the ED physician (MD), or Unassigned. Click a row to open the handoff.'],
    ['Severity', 'How sick the patient is: stable, watcher or unstable. Rules set the lowest level the chart allows, and the model can raise it, never lower it.'],
    ['Reason for visit', 'The chief complaint. Underneath, from the chart: a code status other than full code, isolation, and allergies.'],
    ['Destination', 'The unit and bed the patient is going to, where they are now in the ED, and the level of care.'],
    ['Boarding', 'Time since the admit decision while the patient is still in the ED. Amber from 4 hours, red from 6.'],
    ['Handoffs', 'Where the nurse and the physician handoff stand: Draft, Signed, Acknowledged, Changed after signing, or Transferred without sign off. Under them, the open tasks and how many are overdue.'],
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

function Patient({ row, take }: { row: CaseSummary; take?: React.ReactNode }) {
  return (
    <div className="min-w-0 leading-tight">
      <a {...linkProps(`/handoff/${row.csn}`)} className="whitespace-nowrap font-medium outline-none hover:underline focus-visible:underline">
        {row.patient.name}
      </a>
      <div className="mt-0.5 whitespace-nowrap text-xs text-muted-foreground tabular-nums">
        {row.patient.age} {row.patient.sex} · MRN {row.patient.mrn}
      </div>
      <div className="mt-1 space-y-1.5">
        <Owners owners={row.owners} stacked />
        {take}
      </div>
    </div>
  );
}

/** Take a patient from the Unassigned group, without opening the handoff. */
function TakeButton({ row, busy, disabled, onTake }: { row: CaseSummary; busy: boolean; disabled: boolean; onTake: (csn: string) => void }) {
  return (
    <Button
      size="sm"
      variant="outline"
      className="h-6 gap-1 px-2 text-xs"
      disabled={disabled || busy}
      aria-label={`Take ${row.patient.name}`}
      onClick={(e) => {
        e.stopPropagation();
        e.preventDefault();
        onTake(row.csn);
      }}
    >
      <UserPlus className="size-3.5" aria-hidden />
      Take
    </Button>
  );
}

/** In the half hour before a shift change: how many of your patients still need a signed handoff. */
function ShiftBanner({ shift }: { shift: ShiftInfo }) {
  const when = clock(shift.at);
  const n = shift.unsigned;
  const text = shift.mine === 0
    ? `Shift change at ${when}. You have no patients on the board.`
    : n === 0
      ? `Shift change at ${when}: every one of your patients has a signed handoff.`
      : `Shift change at ${when}: ${n} of your patients ${n === 1 ? 'has a handoff that needs' : 'have handoffs that need'} your signature.`;
  return (
    <div role="status" className="flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-xl border border-sev-watcher/30 bg-sev-watcher/10 px-4 py-2.5 text-sm">
      <BellRing className="size-4 shrink-0 text-sev-watcher" aria-hidden />
      <span className="min-w-56 flex-1 font-medium">{text}</span>
      <a {...linkProps('/takeover')} className="text-sm font-medium text-primary underline-offset-4 hover:underline">
        Take over from a colleague
      </a>
      <HelpButton topic="board.shift" label="About shift change" className="size-6" />
    </div>
  );
}

/** The groups the board shows: for My patients the Unassigned first, then yours; otherwise one group. */
function groups(rows: CaseSummary[], scope: Scope, role: EdRole | null): Group[] {
  if (scope === 'everyone' || !role) return [{ id: 'all', rows }];
  return [
    { id: 'unassigned', title: 'Unassigned', hint: 'No one owns these for your role. Take the ones you are caring for.', rows: rows.filter((r) => r.phase === 'boarding' && isUnassigned(r, role)) },
    { id: 'mine', title: 'Your patients', rows: rows.filter((r) => isMine(r, role)) },
  ];
}

function GroupHead({ g, colSpan }: { g: Group; colSpan?: number }) {
  if (!g.title) return null;
  const body = (
    <div className="flex flex-wrap items-baseline gap-x-2">
      <span className="font-semibold text-foreground">{g.title}</span>
      <span className="tabular-nums">{g.rows.length}</span>
      {g.hint && g.rows.length > 0 && <span className="font-normal">{g.hint}</span>}
      {g.id === 'unassigned' && <HelpButton topic="board.owners" label="About owners" className="size-5 self-center" />}
    </div>
  );
  return colSpan ? (
    <tr className="bg-muted/30">
      <th scope="colgroup" colSpan={colSpan} className="px-4 py-2 text-left text-xs font-medium text-muted-foreground lg:px-5">
        {body}
      </th>
    </tr>
  ) : (
    <li className="bg-muted/30 px-4 py-2 text-xs font-medium text-muted-foreground">{body}</li>
  );
}

function ScopeTabs({ scope, onScope, data }: { scope: Scope; onScope: (s: Scope) => void; data: BoardData }) {
  return (
    <div className="flex items-center gap-1">
      <div role="tablist" aria-label="Whose patients" className="inline-flex rounded-lg bg-muted p-0.5">
        {(['mine', 'everyone'] as Scope[]).map((s) => (
          <button
            key={s}
            role="tab"
            aria-selected={scope === s}
            onClick={() => onScope(s)}
            className={cn('rounded-md px-3 py-1 text-sm font-medium transition-colors outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50', scope === s ? 'bg-card text-foreground shadow-xs' : 'text-muted-foreground hover:text-foreground')}
          >
            {s === 'mine' ? 'My patients' : 'Everyone'}
            <span className="ml-1.5 text-xs tabular-nums text-muted-foreground">{s === 'mine' ? data.kpis.mine ?? 0 : data.rows.length}</span>
          </button>
        ))}
      </div>
      <HelpButton topic="board.tabs" label="About My patients and Everyone" className="size-6" />
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
  const { at, apiAt, past, persona, version, bump, config } = useApp();
  const { data, error, retry } = useLoad(() => api.board(apiAt), [at, persona.id, version]);
  const role = edRole(persona);
  const [picked, setScope] = useState<Scope | null>(null);
  const scope: Scope = role ? picked ?? 'mine' : 'everyone';
  useEffect(() => setScope(null), [persona.id]);
  const [taking, setTaking] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ tone: 'error' | 'ok'; text: string } | null>(null);
  useEffect(() => {
    if (!notice) return;
    const id = window.setTimeout(() => setNotice(null), 5000);
    return () => window.clearTimeout(id);
  }, [notice]);
  const take = async (csn: string) => {
    if (past) return; // read only; the server refuses it too
    setTaking(csn);
    try {
      const c = await api.own(csn, { kind: 'claim' }, apiAt);
      setNotice({ tone: 'ok', text: `${c.summary.patient.name} is now yours.` });
      bump();
    } catch (e) {
      setNotice({ tone: 'error', text: e instanceof ApiError ? e.message : 'Something went wrong.' });
    } finally {
      setTaking(null);
    }
  };
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
  const shown = useMemo(() => groups(rows, scope, role), [rows, scope, role]);
  const count = shown.reduce((n, g) => n + g.rows.length, 0);
  const unitScope = persona.unit ? `${persona.facility} ${persona.unit}` : 'All units';
  const title = persona.role === 'unit_nurse' ? `Incoming to ${persona.facility} ${persona.unit}` : 'ED boarding board';

  if (error && !data) return <Layout title={title}><ErrorState message={error} onRetry={retry} /></Layout>;
  if (!data) return <Layout title={title}><PageSkeleton /></Layout>;
  const k = data.kpis;
  const ack = persona.can_ack.length > 0;
  const sign = persona.can_sign.length > 0;
  const tplName = config.templates.find((t) => t.id === data.template)?.name ?? data.template;
  return (
    <Layout title={title} crumbs={`${unitScope} · ${persona.name}`} help="board">
      <div className="space-y-5 sm:space-y-6">
        {data.shift?.warning && <ShiftBanner shift={data.shift} />}
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
              {role && <ScopeTabs scope={scope} onScope={setScope} data={data} />}
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

          {notice && (
            <div role={notice.tone === 'error' ? 'alert' : 'status'} className={cn('border-b px-4 py-2 text-sm', notice.tone === 'error' ? 'bg-destructive/10 text-destructive' : 'bg-sev-stable/10 text-sev-stable')}>
              {notice.text}
            </div>
          )}
          {count === 0 ? (
            scope === 'mine' && rows.length ? (
              <EmptyState title="No patients are yours yet">
                No boarding patient is unassigned for your role. Open <b>Everyone</b> to see every patient, or <a {...linkProps('/takeover')} className="font-medium text-primary hover:underline">take over from a colleague</a>.
              </EmptyState>
            ) : (
              <EmptyState title={data.rows.length ? 'No patients match these filters' : 'No handoffs at this time'}>
                {data.rows.length ? 'Clear the search or choose another filter.' : 'Move the replay clock to a busier hour, or wait for the next admit decision.'}
              </EmptyState>
            )
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
                  {shown.map((g) => (
                  <tbody key={g.id} className="divide-y border-t first:border-t-0">
                    <GroupHead g={g} colSpan={8} />
                    {g.rows.map((r) => (
                      <tr key={r.csn} className="cursor-pointer align-middle transition-colors hover:bg-muted/40" onClick={() => navigate(`/handoff/${r.csn}`)}>
                        <td className="py-3 pl-4 pr-3 lg:pl-5">
                          <Patient row={r} take={g.id === 'unassigned' && <TakeButton row={r} busy={taking === r.csn} disabled={past !== null || taking !== null} onTake={take} />} />
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
                          <div className="mt-1.5">
                            <TaskCount counts={r.tasks} />
                          </div>
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
                  ))}
                </table>
              </div>
              {/* The phone shows cards, which have no column titles: one ? explains what a card shows. */}
              <div className="flex items-center gap-1 border-b px-4 py-2 text-xs font-medium text-muted-foreground md:hidden">
                {count} patient{count === 1 ? '' : 's'}
                <Explain
                  icon
                  label="What a card shows"
                  topic="board.columns"
                  side="bottom"
                  text={`Each card: name and reason for visit, severity, who owns the patient (RN and MD), destination and where the patient is now, boarding time, completeness of your role's template (${tplName}) with the items still open, where the nurse and physician handoffs stand, and the open tasks (how many are overdue).`}
                />
              </div>
              <ul className="divide-y md:hidden">
                {shown.map((g) => (
                  <Fragment key={g.id}>
                  <GroupHead g={g} />
                  {g.rows.map((r) => (
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
                      <Owners owners={r.owners} />
                      <Destination row={r} />
                      <div className="flex items-center justify-between gap-3">
                        {r.phase === 'arrived' ? <span className="text-sm">Arrived after {duration(r.minutes_boarding)}</span> : <BoardingTime minutes={r.minutes_boarding} />}
                        <Completeness row={r} template={data.template} label={tplName} />
                      </div>
                      <div className="flex items-end justify-between gap-3">
                        <Handoffs row={r} />
                        <TaskCount counts={r.tasks} plain />
                      </div>
                    </a>
                    {/* Outside the card's link: a button cannot sit inside a link. */}
                    {g.id === 'unassigned' && (
                      <div className="-mt-1 px-4 pb-3">
                        <TakeButton row={r} busy={taking === r.csn} disabled={past !== null || taking !== null} onTake={take} />
                      </div>
                    )}
                  </li>
                  ))}
                  </Fragment>
                ))}
              </ul>
            </>
          )}
        </Card>
      </div>
    </Layout>
  );
}
