import { Fragment, useState, type ReactNode } from 'react';
import { AlertTriangle, Check, CircleAlert, Clock, Pencil, RotateCcw, Sparkles, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { Pill, SeverityBadge } from '@/components/common/badges';
import { SourceChip, SourceList } from './Evidence';
import { HelpButton } from '@/components/guide/HelpButton';
import { QuietExplain } from '@/components/guide/Explain';
import { EnteredTag, type AttestFn } from './AttestChoices';
import { codeTone, enteredText, goToRule } from '@/lib/entered';
import { LEVEL_LABEL, bed, clinicalReason, clock, dateTime } from '@/lib/format';
import { ExpiredRecheck } from './Panels';
import { cn } from '@/lib/utils';
import type { CaseTemplate, Element, Narrative, NarrativeItem, Rule } from '@/lib/types';

type Data = Record<string, unknown>;
const NOT_IN_FEEDS = new Set(['E19', 'E21', 'E22']);

/** Narrative text with the numbers the validator could not find in the cited sources highlighted. */
function FlaggedText({ text, flagged }: { text: string; flagged?: string[] }) {
  if (!flagged?.length) return <>{text}</>;
  const re = new RegExp(`(?<![\\w.])(${flagged.map((f) => f.replace('.', '\\.')).join('|')})(?![\\w]|\\.\\d)`, 'g');
  const parts = text.split(re);
  return (
    <>
      {parts.map((p, i) =>
        flagged.includes(p) && i % 2 === 1 ? (
          <Tooltip key={i}>
            <TooltipTrigger asChild>
              <mark tabIndex={0} className="rounded-sm bg-flag/15 px-0.5 text-inherit underline decoration-flag decoration-dotted underline-offset-4 outline-none focus-visible:ring-2 focus-visible:ring-ring/50">
                {p}
              </mark>
            </TooltipTrigger>
            <TooltipContent className="max-w-64">Not found in the cited sources. Check it before signing.</TooltipContent>
          </Tooltip>
        ) : (
          <Fragment key={i}>{p}</Fragment>
        ),
      )}
    </>
  );
}

function ModelMark({ n }: { n: Narrative }) {
  if (n.edited) {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
        <Pencil className="size-3" aria-hidden />
        Edited by {n.edited.by}
        {n.edited.model_changed && <span className="text-flag">, model text changed since</span>}
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
      <Sparkles className="size-3" aria-hidden />
      <QuietExplain topic="handoff.items" label="Drafted by the model" text="Written by the model from the chart. Read it, check the numbered sources, and press Edit if it is wrong.">
        Drafted by the model
      </QuietExplain>
    </span>
  );
}

function Severity({ n }: { n: Narrative }) {
  const raised = n.floor && n.model_value && n.value !== n.model_value;
  // The rule floor writes "<Level> by rule: <reasons>." ahead of the model's own text; show them apart.
  const text = n.text ?? '';
  const prefix = n.floor?.reasons.length ? `${n.floor.value[0].toUpperCase()}${n.floor.value.slice(1)} by rule: ${n.floor.reasons.join('; ')}.` : null;
  const own = (prefix && text.startsWith(prefix) ? text.slice(prefix.length) : text.replace(/^(Stable|Watcher|Unstable) by rule: [^.]*\.(?=\s|$)/, '')).trim();
  return (
    <div className="space-y-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <SeverityBadge value={n.value} size="lg" />
        <SourceList sources={n.sources} />
      </div>
      {n.floor && n.floor.reasons.length > 0 && (
        <div className="rounded-lg border border-dashed px-3 py-2 text-sm">
          <span className="font-medium">
            <QuietExplain topic="handoff.severity" label="Rule floor" text="The lowest severity the chart allows, set by fixed rules. The model can make the patient sicker than the floor, never less sick.">
              {raised ? 'Raised by rule' : 'Rule floor'}
            </QuietExplain>
            :{' '}
          </span>
          {n.floor.reasons.map(clinicalReason).join('; ')}
          {raised && <span className="text-muted-foreground">. The model called it {n.model_value}; rules can raise severity, never lower it.</span>}
        </div>
      )}
      {own && (
        <p className="text-sm leading-relaxed">
          <span className="text-muted-foreground">Model assessment: </span>
          <FlaggedText text={own} flagged={n.unsupported_numbers} />
        </p>
      )}
    </div>
  );
}

function Items({ items }: { items: NarrativeItem[] }) {
  return (
    <ol className="divide-y rounded-lg border">
      {items.map((it, i) => (
        <li key={i} className="flex flex-col gap-1 px-3 py-2 text-sm sm:flex-row sm:items-start sm:gap-4">
          <span className="min-w-0 flex-1 leading-relaxed">
            <FlaggedText text={it.text} flagged={it.unsupported_numbers} />
            <SourceList sources={it.sources} />
          </span>
          <span className="flex shrink-0 gap-3 text-xs text-muted-foreground sm:w-56 sm:justify-end">
            {it.owner && <span className="truncate">{it.owner}</span>}
            {it.due && (
              <span className="inline-flex items-center gap-1 whitespace-nowrap">
                <Clock className="size-3" aria-hidden />
                {it.due}
              </span>
            )}
          </span>
        </li>
      ))}
    </ol>
  );
}

function NarrativeView({ e }: { e: Element }) {
  const n = e.narrative ?? {};
  if (e.id === 'E07') return <Severity n={n} />;
  if (n.items?.length) return <Items items={n.items} />;
  if (!n.text) return <Empty>Not documented in the chart yet.</Empty>;
  return (
    <p className="text-sm leading-relaxed">
      <FlaggedText text={n.text} flagged={n.unsupported_numbers} />
      <SourceList sources={n.sources} />
    </p>
  );
}

function Empty({ children }: { children: ReactNode }) {
  return <p className="text-sm text-muted-foreground">{children}</p>;
}

function KV({ items }: { items: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-3">
      {items
        .filter(([, v]) => v !== null && v !== undefined && v !== '')
        .map(([k, v]) => (
          <div key={k} className="min-w-0">
            <dt className="text-xs text-muted-foreground">{k}</dt>
            <dd className="truncate text-sm font-medium tabular-nums">{v}</dd>
          </div>
        ))}
    </dl>
  );
}

const VITALS: [string, string, string][] = [
  ['hr', 'HR', 'bpm'], ['sbp', 'BP', ''], ['rr', 'RR', '/min'], ['spo2', 'SpO2', '%'], ['temp', 'Temp', '°C'], ['map', 'MAP', ''], ['gcs', 'GCS', ''], ['pain', 'Pain', '/10'],
];

/** A value the ED clinician entered for an item the chart does not have yet, with its amber tag. */
function Entered({ e }: { e: Element }) {
  const list = e.entered!;
  // The same colors as the patient box: red for allergies and any code status but full code, amber for isolation.
  const tone = (v: string) => (e.id === 'E09' ? 'danger' : e.id === 'E10' ? 'warn' : codeTone(v));
  return (
    <div className="flex flex-wrap items-center gap-2">
      <ul className="contents">
        {list.map((x) => (
          <li key={x.value} className="inline-flex">
            <Pill tone={tone(x.value)} className="text-sm">{enteredText(x)}</Pill>
          </li>
        ))}
      </ul>
      <EnteredTag entered={list} />
    </div>
  );
}

function Deterministic({ e, wf }: { e: Element; wf: CaseTemplate['workflow'] }) {
  const d = e.data as Data | Data[] | null | undefined;
  if (e.entered?.length && (e.id === 'E08' || e.id === 'E09' || e.id === 'E10') && !(Array.isArray(d) ? d.length : d)) return <Entered e={e} />;
  const one = (d ?? {}) as Data;
  const list = (Array.isArray(d) ? d : []) as Data[];
  const src = (x: Data) => (typeof x.src === 'string' ? <SourceChip src={x.src} /> : null);
  switch (e.id) {
    case 'E01':
      return <KV items={[['Name', one.name as string], ['Age, sex', `${one.age} ${one.sex}`], ['MRN', one.mrn as string]]} />;
    case 'E02':
      return wf.signed ? (
        <KV items={[['Sender', wf.signed.by], ['Callback', wf.signed.callback], ['Signed', dateTime(wf.signed.at)]]} />
      ) : (
        <Empty>Added when the ED clinician signs.</Empty>
      );
    case 'E29':
      return wf.acknowledged ? (
        <div className="space-y-1.5 text-sm">
          <p className="leading-relaxed">"{wf.acknowledged.readback}"</p>
          <p className="text-xs text-muted-foreground">
            {wf.acknowledged.by}, {dateTime(wf.acknowledged.at)}
          </p>
          {wf.acknowledged.questions && <p className="text-sm text-muted-foreground">Questions: {wf.acknowledged.questions}</p>}
        </div>
      ) : (
        <Empty>Written by the receiving clinician when they accept the handoff.</Empty>
      );
    case 'E31':
      return <Empty>Saved with time and version on every change.</Empty>;
    case 'E03':
      return <KV items={[['Service', one.service as string], ['Admitting provider', (one.admitting_name as string) || (one.admitting_id ? `Provider ${one.admitting_id}` : null)]]} />;
    case 'E04':
      return (
        <KV
          items={[
            ['Now', bed(one.unit as string, one.room as string, one.bed as string)],
            ['Destination', one.pending_unit ? bed(one.pending_unit as string, one.pending_room as string, one.pending_bed as string) : 'Bed pending'],
            ['Level of care', one.level_of_care ? LEVEL_LABEL[one.level_of_care as string] ?? (one.level_of_care as string) : null],
          ]}
        />
      );
    case 'E28':
      return <KV items={[['Bed', one.pending_unit ? bed(one.pending_unit as string, one.pending_room as string, one.pending_bed as string) : 'Not assigned'], ['Status', one.status as string]]} />;
    case 'E05':
      return <KV items={[['Chief complaint', one.chief_complaint as string], ['ESI', one.esi as number], ['Arrived', dateTime(one.arrival_ts as string)]]} />;
    case 'E08':
    case 'E10':
      return d ? (
        <p className="text-sm font-medium">
          {String(one.value)} {src(one)}
          {typeof one.recorded_ts === 'string' && <span className="ml-1 font-normal text-muted-foreground">recorded {dateTime(one.recorded_ts)}</span>}
        </p>
      ) : (
        <Empty>{e.id === 'E10' ? 'No isolation documented.' : 'Not documented.'}</Empty>
      );
    case 'E09':
      return list.length ? (
        <ul className="flex flex-wrap gap-1.5">
          {list.map((a, i) => (
            <li key={i}>
              <Pill tone="danger">
                {String(a.allergen)}
                {a.reaction ? `: ${String(a.reaction).toLowerCase()}` : ''}
              </Pill>
            </li>
          ))}
        </ul>
      ) : (
        <Empty>No allergies documented.</Empty>
      );
    case 'E12':
      return list.length ? (
        <ul className="flex flex-wrap gap-1.5">
          {list.map((p, i) => (
            <li key={i}>
              <Pill>{String(p.display)}</Pill>
            </li>
          ))}
        </ul>
      ) : (
        <Empty>No problems on the list.</Empty>
      );
    case 'E13':
      return <Collapsible items={list.map((m) => String(m.name))} empty="No home medications on file." />;
    case 'E14': {
      if (!d) return <Empty>No vital signs yet.</Empty>;
      return (
        <div className="space-y-2">
          <div className="grid grid-cols-4 gap-2 sm:grid-cols-8">
            {VITALS.filter(([k]) => one[k] != null).map(([k, label, unit]) => (
              <div key={k} className="rounded-lg bg-muted/50 px-2.5 py-2">
                <div className="text-[11px] text-muted-foreground">{label}</div>
                <div className="text-sm font-semibold tabular-nums">
                  {k === 'sbp' ? `${one.sbp}/${one.dbp ?? '?'}` : String(one[k])}
                  <span className="ml-0.5 text-[11px] font-normal text-muted-foreground">{unit}</span>
                </div>
              </div>
            ))}
          </div>
          <p className="text-xs text-muted-foreground">
            Taken {clock(one.taken_ts as string)}
            {one.o2_device ? `, ${one.o2_device}` : ''} {src(one)}
          </p>
        </div>
      );
    }
    case 'E15':
    case 'E17':
      return list.length ? (
        <div className="overflow-x-auto rounded-lg border">
          <table className="w-full text-sm">
            <tbody className="divide-y">
              {list.map((r, i) => (
                <tr key={i} className="align-top">
                  <td className="py-2 pl-3 pr-2 font-medium">{String(r.name)}</td>
                  <td className="px-2 py-2 tabular-nums">
                    <span className={cn('font-semibold', r.flag === 'HH' || r.flag === 'LL' ? 'text-sev-unstable' : r.flag ? 'text-sev-watcher' : '')}>{String(r.value)}</span>{' '}
                    <span className="text-xs text-muted-foreground">{(r.units as string) ?? ''}</span>
                    {r.flag ? <span className={cn('ml-1.5 text-xs font-semibold', r.flag === 'HH' || r.flag === 'LL' ? 'text-sev-unstable' : 'text-sev-watcher')}>{String(r.flag)}</span> : null}
                    {r.comment ? <div className="mt-0.5 text-xs text-muted-foreground">{String(r.comment)}</div> : null}
                  </td>
                  <td className="whitespace-nowrap py-2 pl-2 pr-3 text-right text-xs text-muted-foreground tabular-nums">
                    {clock(r.resulted_ts as string)} {src(r)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <Empty>{e.id === 'E15' ? 'No abnormal results.' : 'No imaging or ECG.'}</Empty>
      );
    case 'E16':
      return list.length ? (
        <ul className="divide-y rounded-lg border">
          {list.map((p, i) => (
            <li key={i} className="flex items-center justify-between gap-3 px-3 py-2 text-sm">
              <span className="font-medium">{String(p.name)}</span>
              <span className="text-xs text-muted-foreground">
                {String(p.status)} · owner {String(p.owner)}
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <Empty>No results pending.</Empty>
      );
    case 'E18':
      return list.length ? (
        <ul className="divide-y rounded-lg border">
          {list.map((m, i) => (
            <li key={i} className="flex items-start justify-between gap-3 px-3 py-2 text-sm">
              <span className="min-w-0">
                <span className="font-medium">{String(m.name)}</span>
                <span className="block text-xs text-muted-foreground">
                  {[m.dose && `${m.dose} ${m.unit ?? ''}`, m.route, m.rate, m.action].filter(Boolean).join(' · ')}
                </span>
              </span>
              <span className="shrink-0 text-xs text-muted-foreground tabular-nums">
                {clock(m.taken_ts as string)} {src(m)}
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <Empty>No medications given in the ED.</Empty>
      );
    case 'E23':
      return list.length ? <Collapsible items={list.map((c) => `${c.type}: ${c.status}`)} empty="" /> : <Empty>No consults.</Empty>;
    case 'E27':
      return d ? <KV items={[['Language', one.language as string], ['Interpreter', one.interpreter === 'Y' ? 'Needed' : one.interpreter ? 'Not needed' : null]]} /> : <Empty>Not documented.</Empty>;
    case 'E30': {
      const last = one.last_analgesic as Data | undefined;
      return d ? (
        <KV items={[['Pain score', one.score != null ? `${one.score}/10` : null], ['Last analgesic', last ? `${last.name} at ${clock(last.taken_ts as string)}` : 'None given']]} />
      ) : (
        <Empty>No pain score.</Empty>
      );
    }
    default:
      return d ? <pre className="overflow-x-auto text-xs">{JSON.stringify(d, null, 1)}</pre> : <Empty>Not documented.</Empty>;
  }
}

function Collapsible({ items, empty }: { items: string[]; empty: string }) {
  const [all, setAll] = useState(false);
  if (!items.length) return <Empty>{empty}</Empty>;
  const shown = all ? items : items.slice(0, 6);
  return (
    <div>
      <ul className="grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
        {shown.map((t, i) => (
          <li key={i} className="truncate" title={t}>
            {t}
          </li>
        ))}
      </ul>
      {items.length > 6 && (
        <button type="button" onClick={() => setAll(!all)} className="mt-1.5 text-xs font-medium text-primary hover:underline">
          {all ? 'Show fewer' : `Show all ${items.length}`}
        </button>
      )}
    </div>
  );
}

/**
 * What an item still needs, or how it was resolved. The choices themselves are in the Sign off panel only: the
 * sender gets a link there.
 */
export function RuleBadges({ rules, canResolve }: { rules: Rule[]; canResolve?: boolean }) {
  const open = rules.filter((r) => r.status !== 'pass' && r.status !== 'deferred');
  if (!open.length) return null;
  return (
    <div className="mt-3 space-y-2">
      {open.map((r) => {
        if (r.resolution) {
          if (r.resolution.entered) return null; // the item shows the value and its tag
          return (
            <p key={r.rule} className="flex items-center gap-1.5 text-xs text-sev-stable">
              <Check className="size-3.5" aria-hidden />
              {r.resolution.kind === 'attested' ? `Attested: ${r.resolution.option}` : 'Resolved by edit'} ({r.resolution.by})
            </p>
          );
        }
        const blocking = r.level !== 'soft';
        return (
          <div key={r.rule} className={cn('flex flex-wrap items-center gap-2 rounded-lg px-3 py-2 text-xs', blocking ? 'bg-sev-watcher/10' : 'bg-muted/60')}>
            {blocking ? <AlertTriangle className="size-3.5 text-sev-watcher" aria-hidden /> : <CircleAlert className="size-3.5 text-muted-foreground" aria-hidden />}
            <span className={cn('font-medium', blocking ? 'text-foreground' : 'text-muted-foreground')}>
              {r.message || `${r.label}: not documented`}
              {!blocking && ' (does not block)'}
            </span>
            {r.status === 'missing' && blocking && <span className="text-muted-foreground">Write it with Edit to resolve.</span>}
            {canResolve && (r.attest_options?.length ?? 0) > 0 && (
              <button type="button" onClick={() => goToRule(r.rule)} className="rounded-sm font-medium text-primary underline-offset-4 outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring/60">
                Resolve in Sign off
                <span className="sr-only">: {r.label}</span>
              </button>
            )}
            {r.attest_expired && <ExpiredRecheck r={r} />}
          </div>
        );
      })}
    </div>
  );
}

/** Inline editor for a narrative element: plain text, or one line per action item ("text | owner | due"). */
function Editor({ e, onSave, onCancel, busy }: { e: Element; onSave: (body: { text?: string; items?: NarrativeItem[] }) => void; onCancel: () => void; busy?: boolean }) {
  const n = e.narrative ?? {};
  const isItems = Array.isArray(n.items) || e.id === 'E24';
  const initial = isItems ? (n.items ?? []).map((i) => [i.text, i.owner ?? '', i.due ?? ''].join(' | ')).join('\n') : n.text ?? '';
  const [v, setV] = useState(initial);
  const items = v
    .split('\n')
    .map((l) => l.split('|').map((x) => x.trim()))
    .filter((p) => p[0])
    .map(([text, owner, due]) => ({ text, owner: owner || null, due: due || null }));
  const empty = isItems ? items.length === 0 : !v.trim();
  return (
    <div className="space-y-2">
      <textarea
        value={v}
        onChange={(x) => setV(x.target.value)}
        rows={isItems ? Math.max(3, v.split('\n').length + 1) : 4}
        className="w-full rounded-md border border-input bg-card px-3 py-2 text-sm leading-relaxed shadow-xs outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/40 dark:bg-input/20"
        aria-label={`Edit ${e.label}`}
        autoFocus
      />
      {isItems && <p className="text-xs text-muted-foreground">One action per line: what | owner | when.</p>}
      {empty && <p className="text-xs text-sev-watcher">Write the text, or use Restore to bring back the model's text.</p>}
      <div className="flex gap-2">
        <Button size="sm" disabled={busy || empty} onClick={() => onSave(isItems ? { items } : { text: v.trim() })}>
          Save
        </Button>
        <Button size="sm" variant="ghost" onClick={onCancel}>
          <X />
          Cancel
        </Button>
      </div>
    </div>
  );
}

export function ElementView({ e, tpl, canEdit, locked, busy, onEdit }: {
  e: Element;
  tpl: CaseTemplate;
  canEdit: boolean;
  /** Read only (live, at an earlier time): Edit and Restore show, turned off; #read-only-why says why. */
  locked?: boolean;
  busy?: boolean;
  onEdit: (element: string, body: { text?: string; items?: NarrativeItem[]; restore?: boolean }) => Promise<boolean>;
  /** Unused here: the choices are in the Sign off panel. Kept so callers can pass one handler for both. */
  onAttest?: AttestFn;
}) {
  const [editing, setEditing] = useState(false);
  const changed = tpl.changes.elements.some((c) => c.id === e.id);
  const editable = canEdit && e.kind === 'narrative' && e.id !== 'E07';
  if (NOT_IN_FEEDS.has(e.id)) {
    return (
      <div id={`el-${e.id}`} className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-0.5 px-4 py-2.5 sm:px-5">
        <h4 className="text-sm font-medium text-muted-foreground">{e.label}</h4>
        <p className="text-xs text-muted-foreground">Not in the reference feeds; a site that charts it can map it here.</p>
      </div>
    );
  }
  return (
    <div id={`el-${e.id}`} className={cn('group relative scroll-mt-24 px-4 py-4 sm:px-5', changed && 'before:absolute before:inset-y-3 before:left-0 before:w-0.5 before:rounded-full before:bg-flag')}>
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <h4 className="text-sm font-semibold">{e.label}</h4>
          {e.id === 'E07' && <HelpButton topic="handoff.severity" label="About severity" className="size-6" />}
          {changed && (
            <QuietExplain topic="questions.changes" label="Changed" text={tpl.changes.since === 'signed' ? 'Different from the version that was signed.' : 'Different from the previous version.'} className="no-underline">
              <Pill tone="warn" className="underline decoration-dotted underline-offset-[3px]">Changed</Pill>
            </QuietExplain>
          )}
        </div>
        <div className="flex items-center gap-2">
          {e.kind === 'narrative' && <ModelMark n={e.narrative ?? {}} />}
          {e.kind === 'narrative' && e.id === 'E11' && <HelpButton topic="handoff.items" label="Chips and highlights" className="size-6" />}
          {editable && !editing && e.narrative?.edited && (
            <Button size="sm" variant="ghost" className="h-7 px-2 text-xs" disabled={busy || locked} aria-describedby={locked ? 'read-only-why' : undefined} onClick={() => onEdit(e.id, { restore: true })} aria-label={`Restore the model's text for ${e.label}`}>
              <RotateCcw className="size-3.5" />
              Restore
            </Button>
          )}
          {editable && !editing && (
            <Button size="sm" variant="ghost" className="h-7 px-2 text-xs" disabled={locked} aria-describedby={locked ? 'read-only-why' : undefined} onClick={() => setEditing(true)} aria-label={`Edit ${e.label}`}>
              <Pencil className="size-3.5" />
              Edit
            </Button>
          )}
        </div>
      </div>
      {editing && !locked ? (
        <Editor e={e} busy={busy} onCancel={() => setEditing(false)} onSave={async (body) => (await onEdit(e.id, body)) && setEditing(false)} />
      ) : e.kind === 'narrative' ? (
        <NarrativeView e={e} />
      ) : (
        <Deterministic e={e} wf={tpl.workflow} />
      )}
      <RuleBadges rules={e.rules} canResolve={canEdit} />
    </div>
  );
}
