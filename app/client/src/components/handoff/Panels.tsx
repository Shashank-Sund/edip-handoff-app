import { useState } from 'react';
import { AlertTriangle, ArrowRight, Check, CheckCheck, GitCompareArrows, History, Lock, PenLine } from 'lucide-react';
import { LineChart, Line as RLine, ResponsiveContainer, YAxis } from 'recharts';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { ProgressRing, StateBadge } from '@/components/common/badges';
import { TimeChart } from '@/components/charts/TimeChart';
import { HelpButton } from '@/components/guide/HelpButton';
import { callbackOk, canChange, readbackProblem } from '@/lib/checks';
import { clinicalReason, clock, dateTime, pct, triggerLabel } from '@/lib/format';
import { cn } from '@/lib/utils';
import type { CaseDetail, CaseTemplate, Draft, Persona, Rule } from '@/lib/types';

function Section({ title, description, icon, children, className, action }: { title: string; description?: string; icon?: React.ReactNode; children: React.ReactNode; className?: string; action?: React.ReactNode }) {
  return (
    <Card className={className}>
      <CardHeader className="flex-row items-start justify-between gap-3 space-y-0 pb-3 sm:pb-3">
        <div className="min-w-0">
          <CardTitle className="flex items-center gap-2 text-sm [&>svg]:size-4 [&>svg]:text-muted-foreground">
            {icon}
            {title}
          </CardTitle>
          {description && <CardDescription className="mt-1 text-xs">{description}</CardDescription>}
        </div>
        {action}
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}

function Blockers({ draft, onAttest, busy, canEdit }: { draft: Draft; onAttest: (r: Rule, o: string) => void; busy?: boolean; canEdit: boolean }) {
  const blocking = draft.rules.filter((r) => draft.completeness.blocking.includes(r.rule));
  const resolved = draft.rules.filter((r) => r.resolution);
  if (!blocking.length) {
    return (
      <p className="flex items-center gap-2 rounded-lg bg-sev-stable/10 px-3 py-2 text-sm text-sev-stable">
        <Check className="size-4" aria-hidden />
        Every required item is documented or attested.
      </p>
    );
  }
  return (
    <ul className="space-y-2">
      {blocking.map((r) => (
        <li key={r.rule} className="rounded-lg border px-3 py-2.5">
          <div className="flex items-start justify-between gap-2">
            <a href={`#el-${r.element}`} className="text-sm font-medium hover:underline">
              {r.label}
            </a>
            <span className={cn('text-[11px] font-medium uppercase tracking-wide', r.level === 'stale' ? 'text-sev-watcher' : 'text-sev-unstable')}>{r.level === 'stale' ? 'Stale' : 'Required'}</span>
          </div>
          <p className="mt-0.5 text-xs text-muted-foreground">{r.message}</p>
          {r.attest_expired && <ExpiredRecheck r={r} />}
          {canEdit && (r.attest_options?.length ?? 0) > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {r.attest_options!.map((o) => (
                <Button key={o} size="sm" variant="outline" className="h-7 px-2 text-xs" disabled={busy} onClick={() => onAttest(r, o)}>
                  <Check className="size-3" />
                  {o}
                </Button>
              ))}
            </div>
          )}
          {canEdit && r.status === 'missing' && <p className="mt-1.5 text-xs text-muted-foreground">Use Edit on the item to write it.</p>}
        </li>
      ))}
      {resolved.length > 0 && <li className="px-1 text-xs text-muted-foreground">{resolved.length} resolved by attestation or edit.</li>}
    </ul>
  );
}

/** A bedside recheck counts only as long as the vital signs limit for the severity. */
export function ExpiredRecheck({ r }: { r: Rule }) {
  const x = r.attest_expired!;
  return (
    <p className="mt-1 text-xs text-sev-watcher">
      Rechecked by {x.by} at {clock(x.at)}. For a {x.severity} patient a recheck counts for {x.limit_min} minutes, so it expired at {clock(x.until)}. Recheck again.
    </p>
  );
}

export function SignPanel({ tpl, persona, template, phase, busy, onAttest, onSign }: {
  tpl: CaseTemplate;
  persona: Persona;
  template: string;
  phase: string;
  busy?: boolean;
  onAttest: (r: Rule, o: string) => void;
  onSign: (callback: string) => void;
}) {
  const [callback, setCallback] = useState('');
  const { draft, workflow: wf } = tpl;
  const canSign = persona.can_sign.includes(template);
  const canAck = persona.can_ack.includes(template);
  const final = !canChange(tpl, phase);
  const blocking = draft.completeness.blocking.length;
  const needsResign = wf.state === 'changed';
  const unsigned = wf.state === 'draft' || wf.state === 'transferred_unsigned';
  const why = wf.edited_since_signed && wf.changed_since_signed
    ? 'It was edited after signing and a newer draft with different content arrived.'
    : wf.edited_since_signed ? 'An edit or attestation was made after signing.' : 'A newer draft with different content arrived after signing.';
  const cbBad = callback.trim() !== '' && !callbackOk(callback);
  return (
    <Section
      title={canAck ? 'Handoff status' : 'Sign off'}
      icon={<PenLine />}
      description={`Version ${tpl.row.version}, drafted ${dateTime(tpl.row.as_of)}`}
      action={
        <span className="flex items-center gap-1">
          <StateBadge state={wf.state} changed={wf.changed_since_acknowledged} />
          <HelpButton topic={canAck ? 'handoff.ack' : 'handoff.sign'} label="How sign off works" />
        </span>
      }
    >
      <div className="space-y-4">
        <div className="flex items-center gap-3">
          <ProgressRing value={draft.completeness.score} size={44} stroke={4.5} blocking={blocking} />
          <div className="leading-tight">
            <div className="text-lg font-semibold tabular-nums">{pct(draft.completeness.score)} complete</div>
            <div className="text-xs text-muted-foreground">{blocking ? `${blocking} item${blocking > 1 ? 's' : ''} to resolve before signing` : 'Nothing blocks sign off'}</div>
          </div>
        </div>
        {wf.signed && (
          <div className="rounded-lg bg-muted/50 px-3 py-2 text-sm">
            <div className="font-medium">Signed by {wf.signed.by}</div>
            <div className="text-xs text-muted-foreground">
              {dateTime(wf.signed.at)} · version {wf.signed.version} · callback {wf.signed.callback}
            </div>
          </div>
        )}
        {needsResign && (
          <p className="flex items-start gap-2 rounded-lg bg-flag/12 px-3 py-2 text-sm">
            <AlertTriangle className="mt-0.5 size-4 shrink-0 text-flag" aria-hidden />
            <span>
              <span className="font-medium">Changed after signing{canSign ? ', sign again' : ''}.</span> {why} {canSign ? 'Review what changed and sign again; the receiver cannot accept until you do.' : 'The receiver cannot accept until the ED signs again.'}
            </span>
          </p>
        )}
        {wf.state === 'transferred_unsigned' && (
          <p className="flex items-start gap-2 rounded-lg bg-sev-unstable/10 px-3 py-2 text-sm">
            <AlertTriangle className="mt-0.5 size-4 shrink-0 text-sev-unstable" aria-hidden />
            <span>
              <span className="font-medium">Transferred without sign off.</span> The patient left the ED before this handoff was signed.{canSign ? ' You can still complete and sign it.' : ''}
            </span>
          </p>
        )}
        <Blockers draft={draft} onAttest={onAttest} busy={busy} canEdit={canSign && !final} />
        {canSign && !final && (unsigned || needsResign) && (
          <form
            className="space-y-2 border-t pt-4"
            onSubmit={(e) => {
              e.preventDefault();
              onSign(callback);
            }}
          >
            <label className="block text-xs font-medium" htmlFor="callback">
              Your callback number
            </label>
            <div className="flex gap-2">
              <Input id="callback" value={callback} onChange={(e) => setCallback(e.target.value)} placeholder="x4471" inputMode="tel" className="flex-1" aria-describedby={cbBad ? 'callback-help' : undefined} aria-invalid={cbBad || undefined} />
              <Button type="submit" disabled={busy || blocking > 0 || !callback.trim() || cbBad}>
                <PenLine />
                {needsResign ? 'Sign again' : 'Sign'}
              </Button>
            </div>
            {cbBad && <p id="callback-help" className="text-xs text-sev-watcher">At least 3 digits, or an extension like x4471.</p>}
            <p className="text-xs text-muted-foreground">Signing as {persona.name}. The receiver sees your name and callback.</p>
          </form>
        )}
        {canSign && final && (
          <p className="flex items-center gap-2 text-xs text-muted-foreground">
            <Lock className="size-3.5" aria-hidden />
            The patient left the ED; this handoff is final.
          </p>
        )}
        {!canSign && !canAck && <p className="text-xs text-muted-foreground">Your role does not sign this template.</p>}
      </div>
    </Section>
  );
}

export function AckPanel({ tpl, busy, onAck }: { tpl: CaseTemplate; busy?: boolean; onAck: (readback: string, questions: string) => void }) {
  const [readback, setReadback] = useState('');
  const [questions, setQuestions] = useState('');
  const wf = tpl.workflow;
  if (wf.state === 'draft' || wf.state === 'drafting' || wf.state === 'transferred_unsigned') {
    return (
      <Section title="Acknowledge" icon={<CheckCheck />} action={<HelpButton topic="handoff.ack" label="How accepting works" />}>
        <p className="text-sm text-muted-foreground">
          {wf.state === 'transferred_unsigned' ? 'The patient was transferred without sign off. Waiting for the ED clinician to sign; you can read the draft now.' : 'Waiting for the ED clinician to sign. You can read the draft now; it may still change.'}
        </p>
      </Section>
    );
  }
  if (wf.state === 'changed') {
    return (
      <Section title="Acknowledge" icon={<CheckCheck />} action={<HelpButton topic="handoff.ack" label="How accepting works" />}>
        <p className="text-sm text-muted-foreground">Waiting for the ED to sign again: the handoff changed after it was signed. What changed shows the difference.</p>
      </Section>
    );
  }
  if (wf.state === 'acknowledged' && !wf.changed_since_acknowledged) {
    return (
      <Section title="Acknowledged" icon={<CheckCheck />}>
        <p className="text-sm">"{wf.acknowledged?.readback}"</p>
        <p className="mt-1 text-xs text-muted-foreground">
          {wf.acknowledged?.by}, {dateTime(wf.acknowledged?.at)}
        </p>
      </Section>
    );
  }
  const problem = readbackProblem(readback) ?? (questions.trim().length > 2000 ? 'Keep the questions under 2,000 characters.' : null);
  return (
    <Section title={wf.changed_since_acknowledged ? 'Acknowledge the update' : 'Acknowledge'} icon={<CheckCheck />} description="Read back in your own words: the patient, the key risk and what you will do first." action={<HelpButton topic="handoff.ack" label="How accepting works" />}>
      <form
        className="space-y-2"
        onSubmit={(e) => {
          e.preventDefault();
          onAck(readback, questions);
        }}
      >
        <textarea
          value={readback}
          onChange={(e) => setReadback(e.target.value)}
          rows={4}
          placeholder="Example: 72 F with sepsis from a urinary source, watcher; lactate trending down. I will repeat vitals on arrival and follow the pending blood cultures."
          className="w-full rounded-md border border-input bg-card px-3 py-2 text-sm leading-relaxed shadow-xs outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/40 dark:bg-input/20"
          aria-label="Read back"
        />
        <Input value={questions} onChange={(e) => setQuestions(e.target.value)} placeholder="Questions for the sender (optional)" aria-label="Questions for the sender" />
        <Button type="submit" className="w-full" disabled={busy || problem !== null} aria-describedby={problem ? 'accept-why' : undefined}>
          <CheckCheck />
          Accept handoff
        </Button>
        {problem && (
          <p id="accept-why" className="text-xs text-muted-foreground">
            {problem}
          </p>
        )}
      </form>
    </Section>
  );
}

function show(v: unknown): string {
  if (v == null) return 'none';
  if (typeof v === 'object' && v && 'value' in v && 'text' in v) {
    // Illness severity: "<Level> by rule: <reasons>." ahead of the model's text, in clinical words.
    const t = String((v as { text?: string }).text ?? '').replace(/^((?:Stable|Watcher|Unstable) by rule: )(.*?)\.(?= |$)/, (_, a: string, r: string) => a + r.split('; ').map(clinicalReason).join('; ') + '.');
    return [(v as { value?: string }).value, t].filter(Boolean).join(': ');
  }
  if (typeof v === 'string') return v;
  if (typeof v === 'object' && v && 'text' in v) return [(v as { value?: string }).value, (v as { text?: string }).text].filter(Boolean).join(': ');
  if (Array.isArray(v)) return v.map((x) => (typeof x === 'object' && x ? Object.values(x).filter((y) => y != null && typeof y !== 'object').join(' ') : String(x))).join('; ');
  if (typeof v === 'object') return Object.entries(v as Record<string, unknown>).filter(([, x]) => x != null && typeof x !== 'object').map(([k, x]) => `${k.replace(/_/g, ' ')} ${x}`).join(', ');
  return String(v);
}

export function ChangesCard({ tpl }: { tpl: CaseTemplate }) {
  const c = tpl.changes;
  if (!c.version) return null;
  const chart = c.elements.filter((e) => e.kind !== 'narrative');
  const text = c.elements.filter((e) => e.kind === 'narrative');
  return (
    <Section title="What changed" icon={<GitCompareArrows />} action={<HelpButton topic="handoff.context" />} description={`Since ${c.since === 'signed' ? 'the signed' : 'the previous'} version ${c.version}`}>
      {c.elements.length === 0 ? (
        <p className="text-sm text-muted-foreground">No differences in content.</p>
      ) : (
        <div className="space-y-4">
          {chart.length > 0 && (
            <div>
              <p className="mb-1.5 text-xs font-medium text-muted-foreground">Updated from the chart</p>
              <div className="flex flex-wrap gap-1.5">
                {chart.map((e) => (
                  <a key={e.id} href={`#el-${e.id}`} className="rounded-md bg-muted px-2 py-0.5 text-xs font-medium hover:bg-accent">
                    {e.label}
                  </a>
                ))}
              </div>
            </div>
          )}
          {text.length > 0 && (
            <div>
              <p className="mb-1.5 text-xs font-medium text-muted-foreground">{c.since === 'signed' ? 'Text changed since signing' : 'Redrafted by the model'}</p>
              <ul className="space-y-3">
                {text.slice(0, 5).map((e) => (
                  <li key={e.id} className="text-sm">
                    <a href={`#el-${e.id}`} className="font-medium hover:underline">
                      {e.label}
                    </a>
                    <div className="mt-1 space-y-1 text-xs leading-relaxed">
                      {e.before != null && <p className="line-clamp-2 text-muted-foreground line-through decoration-muted-foreground/40">{show(e.before)}</p>}
                      <p className="line-clamp-3">{show(e.after)}</p>
                    </div>
                  </li>
                ))}
                {text.length > 5 && <li className="text-xs text-muted-foreground">and {text.length - 5} more</li>}
              </ul>
            </div>
          )}
        </div>
      )}
    </Section>
  );
}

export function VitalsCard({ draft }: { draft: Draft }) {
  const v = draft.trend?.vitals ?? [];
  const pick = (k: keyof (typeof v)[number]) => v.map((x) => ({ t: x.taken_ts, v: x[k] as number | undefined }));
  return (
    <Section title="Vital signs" icon={<History />} action={<HelpButton topic="handoff.context" label="How to read the chart" />} description={`${v.length} set${v.length === 1 ? '' : 's'} in the ED${v.length ? `, latest ${clock(v.at(-1)!.taken_ts)}` : ''}`}>
      <TimeChart
        ariaLabel="Vital signs over the ED stay"
        height={240}
        stretch={[3, 2]}
        lines={[
          { key: 'hr', label: 'HR', color: 'var(--series-8)', data: pick('hr'), format: (x) => String(Math.round(x)) },
          { key: 'sbp', label: 'SBP', color: 'var(--series-1)', data: pick('sbp'), format: (x) => String(Math.round(x)) },
          { key: 'dbp', label: 'DBP', color: 'var(--series-1)', dashed: true, data: pick('dbp'), format: (x) => String(Math.round(x)) },
          { key: 'spo2', label: 'SpO2', color: 'var(--series-3)', unit: '%', data: pick('spo2'), pane: 1, format: (x) => String(Math.round(x)) },
          { key: 'rr', label: 'RR', color: 'var(--series-4)', data: pick('rr'), pane: 1, scale: 'left', format: (x) => String(Math.round(x)) },
        ]}
      />
    </Section>
  );
}

export function LabsCard({ draft }: { draft: Draft }) {
  const labs = Object.entries(draft.trend?.labs ?? {}).sort((a, b) => b[1].length - a[1].length);
  if (!labs.length) return null;
  return (
    <Section title="Lab trends" icon={<History />} action={<HelpButton topic="handoff.context" />} description="Results that were ever abnormal, final values">
      <ul className="grid grid-cols-2 gap-2.5">
        {labs.slice(0, 8).map(([name, pts]) => {
          const last = pts.at(-1)!;
          const crit = last.flag === 'HH' || last.flag === 'LL';
          const color = crit ? 'var(--sev-unstable)' : last.flag ? 'var(--sev-watcher)' : 'var(--series-1)';
          return (
            <li key={name} className="rounded-lg border px-3 py-2">
              <div className="truncate text-xs text-muted-foreground" title={name}>
                {name}
              </div>
              <div className="flex items-end justify-between gap-2">
                <div className="text-sm font-semibold tabular-nums" style={{ color: last.flag ? color : undefined }}>
                  {last.value}
                  <span className="ml-1 text-[11px] font-normal text-muted-foreground">{last.units}</span>
                </div>
                {pts.length > 1 && (
                  <div className="h-7 w-16" aria-hidden>
                    <ResponsiveContainer width="100%" height="100%">
                      <LineChart data={pts}>
                        <YAxis hide domain={['dataMin', 'dataMax']} />
                        <RLine type="monotone" dataKey="value" stroke={color} strokeWidth={1.75} dot={false} isAnimationActive={false} />
                      </LineChart>
                    </ResponsiveContainer>
                  </div>
                )}
              </div>
              {pts.length > 1 && (
                <div className="text-[11px] text-muted-foreground tabular-nums">
                  {pts[0].value} <ArrowRight className="inline size-3" aria-hidden /> {last.value} over {pts.length} results
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </Section>
  );
}

const ACTION: Record<string, string> = { edit: 'Edited', attest: 'Attested', sign: 'Signed', ack: 'Acknowledged' };

/** "Contingency plan" for E25, "Vital signs" for the rule N08: the names clinicians read, not ids. */
function itemName(draft: Draft, id: string | undefined): string | undefined {
  if (!id) return undefined;
  const rule = draft.rules.find((r) => r.rule === id);
  if (rule) return rule.label;
  for (const s of draft.sections) for (const e of s.elements) if (e.id === id) return e.label;
  return id;
}

export function HistoryCard({ tpl, activity, template }: { tpl: CaseTemplate; activity: CaseDetail['activity']; template: string }) {
  const acts = activity.filter((a) => a.template === template);
  const events = [
    ...tpl.versions.map((v) => ({ at: v.as_of, title: `Version ${v.version}`, detail: `${triggerLabel(v.trigger)}${v.status === 'addendum' ? ', addendum' : v.status === 'final' ? ', final' : ''}`, kind: 'version' })),
    ...acts.map((a) => ({
      at: a.at,
      title: `${a.restore ? 'Model text restored' : ACTION[a.kind] ?? a.kind} by ${a.by}`,
      detail: a.kind === 'sign' || a.kind === 'ack' ? `version ${a.version}` : itemName(tpl.draft, a.detail) ?? `version ${a.version}`,
      kind: a.kind,
    })),
  ].sort((a, b) => (a.at < b.at ? 1 : -1));
  return (
    <Section title="History" icon={<History />} action={<HelpButton topic="handoff.context" />} description={`${tpl.versions.length} version${tpl.versions.length > 1 ? 's' : ''} drafted by ${tpl.row.model}`}>
      <ol className="relative space-y-3 border-l pl-4">
        {events.slice(0, 12).map((e, i) => (
          <li key={i} className="relative">
            <span className={cn('absolute -left-[21px] top-1 size-2.5 rounded-full ring-4 ring-card', e.kind === 'version' ? 'bg-muted-foreground/50' : e.kind === 'sign' ? 'bg-state-signed' : e.kind === 'ack' ? 'bg-state-ack' : 'bg-flag')} aria-hidden />
            <div className="text-sm font-medium">{e.title}</div>
            <div className="text-xs text-muted-foreground">
              {dateTime(e.at)} · {e.detail}
            </div>
          </li>
        ))}
      </ol>
    </Section>
  );
}
