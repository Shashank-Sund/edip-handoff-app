import { useEffect, useState } from 'react';
import { ArrowLeft, ArrowRight, BedDouble, Clock3, ShieldAlert } from 'lucide-react';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Layout } from '@/components/layout/Layout';
import { Pill, SeverityBadge, StateBadge } from '@/components/common/badges';
import { ErrorState, PageSkeleton } from '@/components/common/states';
import { EvidenceProvider } from '@/components/handoff/Evidence';
import { ElementView } from '@/components/handoff/ElementView';
import { AckPanel, ChangesCard, HistoryCard, LabsCard, SignPanel, VitalsCard } from '@/components/handoff/Panels';
import { useApp } from '@/hooks/useApp';
import { useLoad } from '@/hooks/useLoad';
import { ApiError, api } from '@/lib/api';
import { LEVEL_LABEL, bed, dateTime, duration, triggerLabel } from '@/lib/format';
import { linkProps } from '@/lib/router';
import type { CaseDetail, CaseSummary, NarrativeItem, Rule } from '@/lib/types';

const ORDER = ['sbar_nurse', 'ipass_physician', 'custom_sea58'];

function Banner({ s }: { s: CaseSummary }) {
  const d = s.destination;
  return (
    <Card className="p-4 sm:p-5">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
        <div className="min-w-0 space-y-1">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
            <h2 className="text-xl font-semibold tracking-tight">{s.patient.name}</h2>
            <SeverityBadge value={s.severity} size="lg" />
          </div>
          <p className="text-sm text-muted-foreground tabular-nums">
            {s.patient.age} {s.patient.sex} · MRN {s.patient.mrn} · ESI {s.esi ?? 'n/a'} · {s.chief_complaint}
          </p>
          <div className="flex flex-wrap gap-1.5 pt-1.5">
            <Pill tone={s.code_status && s.code_status !== 'Full Code' ? 'danger' : 'neutral'}>{s.code_status ?? 'Code status not documented'}</Pill>
            {s.allergies.length ? s.allergies.map((a) => <Pill key={a} tone="danger">{a}</Pill>) : <Pill>No allergies documented</Pill>}
            {s.isolation && (
              <Pill tone="warn">
                <ShieldAlert />
                {s.isolation}
              </Pill>
            )}
          </div>
        </div>
        <dl className="grid shrink-0 grid-cols-2 gap-x-6 gap-y-3 text-sm sm:grid-cols-3 lg:text-right">
          <div>
            <dt className="text-xs text-muted-foreground">{s.phase === 'arrived' ? 'Was in' : 'Now in'}</dt>
            <dd className="font-medium">{s.phase === 'arrived' ? 'ED' : bed('ED', s.location.room, s.location.bed)}</dd>
          </div>
          <div>
            <dt className="flex items-center gap-1 text-xs text-muted-foreground lg:justify-end">
              <ArrowRight className="size-3" aria-hidden />
              {s.phase === 'arrived' ? 'Arrived on' : 'Going to'}
            </dt>
            <dd className="font-medium">
              {d.pending_unit ? `${s.facility} ${bed(d.pending_unit, d.pending_room, d.pending_bed)}` : 'Bed pending'}
              {d.level_of_care && <span className="ml-1 font-normal text-muted-foreground">{LEVEL_LABEL[d.level_of_care] ?? d.level_of_care}</span>}
            </dd>
          </div>
          <div className="col-span-2 sm:col-span-1">
            <dt className="flex items-center gap-1 text-xs text-muted-foreground lg:justify-end">
              <Clock3 className="size-3" aria-hidden />
              {s.phase === 'arrived' ? 'Boarded' : 'Boarding'}
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
  const { at, persona, version, bump, config } = useApp();
  const { data, error, setData } = useLoad(() => api.case(csn, at), [csn, at, persona.id, version]);
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
    setBusy(true);
    try {
      const next: CaseDetail = await api.act(csn, tab, kind, body, at);
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

  const back = (
    <a {...linkProps('/')} className="inline-flex items-center gap-1 hover:text-foreground">
      <ArrowLeft className="size-3" aria-hidden />
      Board
    </a>
  );
  if (error && !data) {
    return (
      <Layout title="Handoff" crumbs={back}>
        <ErrorState message={error} />
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
  const canEdit = persona.can_sign.includes(current) && (s.phase === 'boarding' || tpl.row.status === 'addendum');
  const canAck = persona.can_ack.includes(current);
  const onAttest = (r: Rule, option: string) => run('attest', { rule: r.rule, option }, `${r.label}: attested`);
  const onEdit = (element: string, body: { text?: string; items?: NarrativeItem[] }) => run('edit', { element, ...body }, 'Saved');

  return (
    <Layout title={s.patient.name} crumbs={back}>
      <EvidenceProvider evidence={tpl.draft.evidence ?? {}}>
        <div className="space-y-5">
          <Banner s={s} />
          {notice && (
            <div role={notice.tone === 'error' ? 'alert' : 'status'} className={notice.tone === 'error' ? 'rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-2.5 text-sm text-destructive' : 'rounded-lg border border-sev-stable/30 bg-sev-stable/10 px-4 py-2.5 text-sm text-sev-stable'}>
              {notice.text}
            </div>
          )}
          <Tabs value={current} onValueChange={setTab}>
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <TabsList aria-label="Handoff template" className="relative w-full sm:w-fit">
                {tabs.map((t) => {
                  const info = config.templates.find((x) => x.id === t);
                  const w = data.templates[t].workflow;
                  return (
                    <TabsTrigger key={t} value={t} className="relative flex-1 gap-2 px-2 sm:flex-none sm:px-3">
                      <span className="sm:hidden">{t === 'custom_sea58' ? 'Minimum' : info?.name}</span>
                      <span className="hidden sm:inline">{info?.name ?? t}</span>
                      <span className="hidden text-xs font-normal text-muted-foreground md:inline">{info?.audience}</span>
                      <StateBadge state={w.state} compact />
                    </TabsTrigger>
                  );
                })}
              </TabsList>
              <p className="text-xs text-muted-foreground">
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
                            <span className="grid size-6 place-items-center rounded-md bg-primary/10 text-[11px] font-bold text-primary">{sec.id}</span>
                            <h3 className="text-sm font-semibold">{sec.label}</h3>
                          </div>
                          <div className="divide-y">
                            {sec.elements.map((e) => (
                              <ElementView key={e.id} e={e} tpl={tpl} canEdit={canEdit} busy={busy} onEdit={onEdit} onAttest={onAttest} />
                            ))}
                          </div>
                        </Card>
                      ))}
                    </div>
                    <aside className="min-w-0 space-y-5 xl:sticky xl:top-20 xl:self-start" aria-label="Sign off and context">
                      <SignPanel tpl={tpl} persona={persona} template={current} phase={s.phase} busy={busy} onAttest={onAttest} onSign={(callback) => run('sign', { callback }, 'Signed. The receiving unit can now acknowledge.')} />
                      {canAck && <AckPanel tpl={tpl} busy={busy} onAck={(readback, questions) => run('ack', { readback, questions }, 'Handoff accepted')} />}
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
        </div>
      </EvidenceProvider>
    </Layout>
  );
}
