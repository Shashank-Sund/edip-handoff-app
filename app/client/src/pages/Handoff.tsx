import { useEffect, useState } from 'react';
import { ArrowLeft, ArrowRight, BedDouble, ChevronDown, Clock3, Lock, ShieldAlert } from 'lucide-react';
import { Card } from '@/components/ui/card';
import { SectionBadge } from '@/components/common/SectionBadge';
import { Button } from '@/components/ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Layout } from '@/components/layout/Layout';
import { Pill, ProgressRing, SeverityBadge, StateBadge } from '@/components/common/badges';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { Explain } from '@/components/guide/Explain';
import type { AttestFn } from '@/components/handoff/AttestChoices';
import { ErrorState, PageSkeleton } from '@/components/common/states';
import { EvidenceProvider } from '@/components/handoff/Evidence';
import { HelpButton } from '@/components/guide/HelpButton';
import { ElementView } from '@/components/handoff/ElementView';
import { AckPanel, ChangesCard, HistoryCard, LabsCard, SignPanel, VitalsCard } from '@/components/handoff/Panels';
import { OwnerLine } from '@/components/handoff/OwnerLine';
import { TasksPanel } from '@/components/handoff/TasksPanel';
import { useApp } from '@/hooks/useApp';
import { useLoad } from '@/hooks/useLoad';
import { ApiError, api, type OwnerBody } from '@/lib/api';
import { LEVEL_LABEL, bed, dateTime, duration, triggerLabel } from '@/lib/format';
import { linkProps } from '@/lib/router';
import { READ_ONLY, canChange, signOffStatus } from '@/lib/checks';
import { cn } from '@/lib/utils';
import type { CaseDetail, CaseSummary, CaseTemplate, EnteredFlag, NarrativeItem, Persona } from '@/lib/types';
import { TAB_HELP, TEMPLATE_NAME, TEMPLATE_SHORT } from '@/lib/labels';
import { useMediaQuery } from '@/hooks/useMedia';

const ORDER = ['sbar_nurse', 'ipass_physician', 'custom_sea58'];

/** Below the wide layout the Sign off panel comes after the handoff: this bar says where it stands and goes there. */
function SignOffBar({ tpl, persona, template, phase }: { tpl: CaseTemplate; persona: Persona; template: string; phase: string }) {
  const [hidden, setHidden] = useState(false);
  useEffect(() => {
    const el = document.getElementById('signoff');
    if (!el || typeof IntersectionObserver === 'undefined') return;
    const io = new IntersectionObserver(([x]) => setHidden(x.isIntersecting), { threshold: 0.15 });
    io.observe(el);
    return () => io.disconnect();
  }, [template]);
  const n = tpl.draft.completeness.blocking.length;
  return (
    <div className={cn('sticky bottom-3 z-10 xl:hidden', hidden && 'pointer-events-none opacity-0')} aria-hidden={hidden || undefined}>
      <button
        type="button"
        tabIndex={hidden ? -1 : undefined}
        onClick={() => {
          const el = document.getElementById('signoff');
          el?.scrollIntoView({ behavior: 'smooth', block: 'start' });
          el?.focus({ preventScroll: true });
        }}
        className="flex w-full items-center justify-between gap-3 rounded-xl border bg-card/95 px-4 py-2.5 text-left text-sm font-medium shadow-lg outline-none backdrop-blur transition-opacity focus-visible:ring-[3px] focus-visible:ring-ring/50 supports-[backdrop-filter]:bg-card/85"
      >
        <span className="flex min-w-0 items-center gap-2.5">
          <ProgressRing value={tpl.draft.completeness.score} size={22} stroke={3} blocking={n} />
          <span className="truncate">{signOffStatus(tpl, persona, template, phase)}</span>
        </span>
        <span className="flex shrink-0 items-center gap-1 text-xs text-muted-foreground">
          Go to sign off
          <ChevronDown className="size-4" aria-hidden />
        </span>
      </button>
    </div>
  );
}

/** A value entered in the sign off, in the patient box: the value, marked as not in the chart yet. */
function EnteredPill({ x, tone, side, icon }: { x: EnteredFlag; tone: 'danger' | 'warn'; side: 'top' | 'bottom'; icon?: React.ReactNode }) {
  return (
    <Explain
      topic="handoff.entered"
      side={side}
      label={`${x.value}, entered`}
      text={`Entered by ${x.by} on the ${TEMPLATE_NAME[x.template] ?? x.template} during sign off${x.note ? ` (${x.note})` : ''}. It is not in the chart yet.`}
    >
      <Pill tone={tone} className="underline decoration-dotted underline-offset-[3px]">
        {icon}
        {x.value} · entered, not in chart
      </Pill>
    </Explain>
  );
}

function Banner({ s, sender }: { s: CaseSummary; sender: boolean }) {
  const d = s.destination;
  const phone = useMediaQuery('(max-width: 639px)');
  const side = phone ? 'bottom' : 'top';
  const e = s.entered;
  // The sender records it in the sign off; the receiver asks the ED or checks the chart.
  const codeHelp = sender ? 'The chart has no code status. Confirm it with the patient or family and record it in the sign off.' : 'The chart has no code status. Ask the ED, or check the chart.';
  const allergyHelp = sender
    ? 'The chart lists no allergies. That is not the same as no known allergies: ask, then confirm it or add the allergy in the sign off.'
    : 'The chart lists no allergies. That is not the same as no known allergies. Ask the ED, or check the chart.';
  return (
    <Card className="p-4 sm:p-5">
      <div className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
        <div className="min-w-0 space-y-1">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
            <h2 className="text-xl font-semibold tracking-tight">{s.patient.name}</h2>
            <SeverityBadge value={s.severity} size="lg" />
          </div>
          <p className="text-sm text-muted-foreground tabular-nums">
            {s.patient.age} {s.patient.sex} · MRN {s.patient.mrn} · ESI {s.esi ?? 'n/a'} · {s.chief_complaint}
          </p>
          <div className="flex flex-wrap gap-1.5 pt-1.5">
            {s.code_status ? (
              <Pill tone={s.code_status !== 'Full Code' ? 'danger' : 'neutral'}>{s.code_status}</Pill>
            ) : e?.code_status ? (
              <EnteredPill x={e.code_status} tone="danger" side={side} />
            ) : (
              <Explain topic="handoff.resolve" side={side} label="Code status not documented" text={codeHelp}>
                <Pill className="underline decoration-dotted underline-offset-[3px]">Code status not documented</Pill>
              </Explain>
            )}
            {s.allergies.length ? (
              s.allergies.map((a) => <Pill key={a} tone="danger">{a}</Pill>)
            ) : e?.allergies.length ? (
              e.allergies.map((a) => <EnteredPill key={a.value} x={a} tone="danger" side={side} />)
            ) : (
              <Explain topic="handoff.resolve" side={side} label="No allergies documented" text={allergyHelp}>
                <Pill className="underline decoration-dotted underline-offset-[3px]">No allergies documented</Pill>
              </Explain>
            )}
            {s.isolation ? (
              <Pill tone="warn">
                <ShieldAlert />
                {s.isolation}
              </Pill>
            ) : (
              e?.isolation && <EnteredPill x={e.isolation} tone="warn" side={side} icon={<ShieldAlert />} />
            )}
          </div>
        </div>
        <dl className="grid shrink-0 grid-cols-2 gap-x-6 gap-y-3 text-sm sm:grid-cols-3 xl:text-right">
          <div>
            <dt className="text-xs text-muted-foreground">{s.phase === 'arrived' ? 'Was in' : 'Now in'}</dt>
            <dd className="font-medium">{s.phase === 'arrived' ? 'ED' : bed('ED', s.location.room, s.location.bed)}</dd>
          </div>
          <div>
            <dt className="flex items-center gap-1 text-xs text-muted-foreground xl:justify-end">
              <ArrowRight className="size-3" aria-hidden />
              {s.phase === 'arrived' ? 'Arrived on' : 'Going to'}
            </dt>
            <dd className="font-medium">
              {d.pending_unit ? `${s.facility} ${bed(d.pending_unit, d.pending_room, d.pending_bed)}` : 'Bed pending'}
              {d.level_of_care && <>{' '}<span className="font-normal text-muted-foreground">{LEVEL_LABEL[d.level_of_care] ?? d.level_of_care}</span></>}
            </dd>
          </div>
          <div className="col-span-2 sm:col-span-1">
            <dt className="flex items-center gap-1 text-xs text-muted-foreground xl:justify-end">
              <Clock3 className="size-3" aria-hidden />
              <Explain topic="board.columns" label="Boarding" text="Time since the admit decision while the patient is still in the ED, waiting for a bed.">
                {s.phase === 'arrived' ? 'Boarded' : 'Boarding'}
              </Explain>
            </dt>
            <dd className="font-medium tabular-nums">
              {duration(s.minutes_boarding)}
              <span className="ml-1 font-normal text-muted-foreground">since {dateTime(s.admit_decision_ts).replace(/^\w+ /, '')}</span>
            </dd>
          </div>
        </dl>
      </div>
    </Card>
  );
}

export default function Handoff({ csn }: { csn: string }) {
  const { at, apiAt, past, persona, version, bump, config } = useApp();
  const { data, error, setData, retry } = useLoad(() => api.case(csn, apiAt), [csn, at, persona.id, version]);
  const [tab, setTab] = useState(persona.default_template);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ tone: 'error' | 'ok'; text: string } | null>(null);
  useEffect(() => setTab(persona.default_template), [persona.default_template]);
  useEffect(() => {
    if (!notice) return;
    const id = window.setTimeout(() => setNotice(null), 5000);
    return () => window.clearTimeout(id);
  }, [notice]);

  const run = async (kind: 'edit' | 'attest' | 'sign' | 'ack', body: unknown, ok: string) => {
    if (past) return false; // read only; the server refuses it too
    setBusy(true);
    try {
      const next: CaseDetail = await api.act(csn, tab, kind, body, apiAt);
      setData(next);
      setNotice({ tone: 'ok', text: ok });
      bump();
      return true;
    } catch (e) {
      setNotice({ tone: 'error', text: e instanceof ApiError ? e.message : 'Something went wrong.' });
      return false;
    } finally {
      setBusy(false);
    }
  };

  // Take, Assign to a colleague, Release: who owns the patient for the caller's ED role.
  const runOwner = async (body: OwnerBody, ok: string) => {
    if (past) return; // read only; the server refuses it too
    setBusy(true);
    try {
      setData(await api.own(csn, body, apiAt));
      setNotice({ tone: 'ok', text: ok });
      bump();
    } catch (e) {
      setNotice({ tone: 'error', text: e instanceof ApiError ? e.message : 'Something went wrong.' });
    } finally {
      setBusy(false);
    }
  };

  const back = (
    <a {...linkProps('/')} className="inline-flex items-center gap-1 hover:text-foreground">
      <ArrowLeft className="size-3" aria-hidden />
      Board
    </a>
  );
  if (error && !data) {
    return (
      <Layout title="Handoff" crumbs={back}>
        <ErrorState message={error} onRetry={retry} />
        <div className="mt-4 text-center">
          <Button variant="outline" asChild>
            <a {...linkProps('/')}>Back to the board</a>
          </Button>
        </div>
      </Layout>
    );
  }
  if (!data) return <Layout title="Handoff" crumbs={back}><PageSkeleton /></Layout>;
  const s = data.summary;
  const tabs = ORDER.filter((t) => data.templates[t]);
  const current = data.templates[tab] ? tab : tabs[0];
  const tpl = data.templates[current];
  const canEdit = persona.can_sign.includes(current) && canChange(tpl, s.phase);
  const canAck = persona.can_ack.includes(current);
  // Live, at an earlier time: read only. The controls stay in place, turned off, with the reason next to them.
  const locked = past !== null;
  const onAttest: AttestFn = (r, option, entry) =>
    run('attest', { rule: r.rule, option, ...entry }, entry?.remove ? `${r.label}: ${entry.entry} removed` : option === r.attest_options?.[0] ? `${r.label}: attested` : `${r.label}: ${entry?.entry.trim() || option} entered`);
  const onEdit = (element: string, body: { text?: string; items?: NarrativeItem[]; restore?: boolean }) =>
    run('edit', { element, ...body }, body.restore ? 'Model text restored' : 'Saved');

  return (
    <Layout title={s.patient.name} crumbs={back} help="handoff">
      <EvidenceProvider evidence={tpl.draft.evidence ?? {}}>
        <div className="space-y-5">
          <Banner s={s} sender={persona.can_sign.length > 0} />
          <OwnerLine s={s} persona={persona} at={at} locked={past !== null} busy={busy} onOwn={runOwner} />
          {locked && (persona.can_sign.length > 0 || persona.can_ack.length > 0) && (
            <p id="read-only-why" className="flex items-start gap-2 rounded-lg border bg-muted/50 px-4 py-2.5 text-sm">
              <Lock className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
              {READ_ONLY}
            </p>
          )}
          {notice && (
            <div role={notice.tone === 'error' ? 'alert' : 'status'} className={notice.tone === 'error' ? 'rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-2.5 text-sm text-destructive' : 'rounded-lg border border-sev-stable/30 bg-sev-stable/10 px-4 py-2.5 text-sm text-sev-stable'}>
              {notice.text}
            </div>
          )}
          <TasksPanel csn={csn} />
          <Tabs value={current} onValueChange={setTab}>
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <TabsList aria-label="Handoff template" className="relative w-full sm:w-fit">
                {tabs.map((t) => {
                  const info = config.templates.find((x) => x.id === t);
                  const w = data.templates[t].workflow;
                  const trigger = (
                    <TabsTrigger key={t} value={t} className="relative flex-1 gap-2 px-2 sm:flex-none sm:px-3">
                      <span className="xl:hidden">{TEMPLATE_SHORT[t] ?? info?.name}</span>
                      <span className="hidden xl:inline">{info?.name ?? t}</span>
                      <span className="hidden text-xs font-normal text-muted-foreground xl:inline">{info?.audience}</span>
                      <StateBadge state={w.state} compact />
                    </TabsTrigger>
                  );
                  return TAB_HELP[t] ? (
                    <Tooltip key={t}>
                      <TooltipTrigger asChild>{trigger}</TooltipTrigger>
                      <TooltipContent className="max-w-64 font-normal">{TAB_HELP[t]}</TooltipContent>
                    </Tooltip>
                  ) : (
                    trigger
                  );
                })}
              </TabsList>
              <HelpButton topic="handoff.tabs" label="About the tabs" className="-ml-2 hidden sm:inline-grid" />
              <p className="flex items-center gap-1 text-xs text-muted-foreground">
                <HelpButton topic="handoff.tabs" label="About the tabs" className="size-6 sm:hidden" />
                Version {tpl.row.version}, drafted {dateTime(tpl.row.as_of)} after {triggerLabel(tpl.row.trigger).toLowerCase()}
                {tpl.row.status === 'addendum' && <span className="ml-1 font-medium text-flag">addendum</span>}
              </p>
            </div>
            {tabs.map((t) => (
              <TabsContent key={t} value={t} className="mt-4">
                {t === current && (
                  <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_380px]">
                    <div className="min-w-0 space-y-5">
                      {tpl.draft.sections.map((sec) => (
                        <Card key={sec.id} className="overflow-hidden">
                          <div className="flex items-center gap-2.5 border-b bg-muted/30 px-4 py-2.5 sm:px-5">
                            <SectionBadge id={sec.id} label={sec.label} />
                            <h3 className="text-sm font-semibold">{sec.label}</h3>
                          </div>
                          <div className="divide-y">
                            {sec.elements.map((e) => (
                              <ElementView key={e.id} e={e} tpl={tpl} canEdit={canEdit} locked={locked} busy={busy} onEdit={onEdit} onAttest={onAttest} />
                            ))}
                          </div>
                        </Card>
                      ))}
                    </div>
                    {/* At xl the right column scrolls on its own, so Sign off stays in reach on a long handoff. */}
                    <aside className="scroll-subtle min-w-0 space-y-5 xl:sticky xl:top-20 xl:-mx-1 xl:max-h-[calc(100dvh-6rem)] xl:self-start xl:overflow-y-auto xl:overscroll-contain xl:px-1 xl:pb-1" aria-label="Sign off and context">
                      <SignPanel tpl={tpl} persona={persona} template={current} phase={s.phase} busy={busy} locked={locked} onAttest={onAttest} onSign={(callback) => run('sign', { callback }, 'Signed. The receiving team can now accept it.')} />
                      {canAck && <AckPanel tpl={tpl} busy={busy} locked={locked} onAck={(readback, questions) => run('ack', { readback, questions }, 'Handoff accepted')} />}
                      <ChangesCard tpl={tpl} />
                      <VitalsCard draft={tpl.draft} />
                      <LabsCard draft={tpl.draft} />
                      <HistoryCard tpl={tpl} activity={data.activity} template={current} />
                    </aside>
                  </div>
                )}
              </TabsContent>
            ))}
          </Tabs>
          <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <BedDouble className="size-3.5" aria-hidden />
            Structured items come straight from the chart. Narrative items are drafted by the model; numbered chips open the sources they cite, and highlighted numbers were not found in those sources.
          </p>
          <SignOffBar tpl={tpl} persona={persona} template={current} phase={s.phase} />
        </div>
      </EvidenceProvider>
    </Layout>
  );
}
