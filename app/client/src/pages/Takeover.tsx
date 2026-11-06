import { useEffect, useMemo, useState } from 'react';
import { Check, Handshake, Lock } from 'lucide-react';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Layout } from '@/components/layout/Layout';
import { Pill, SeverityBadge, StateBadge } from '@/components/common/badges';
import { EmptyState, ErrorState, PageSkeleton } from '@/components/common/states';
import { HelpButton } from '@/components/guide/HelpButton';
import { useApp } from '@/hooks/useApp';
import { useLoad } from '@/hooks/useLoad';
import { ApiError, api } from '@/lib/api';
import { READ_ONLY } from '@/lib/checks';
import { bed, clock, duration } from '@/lib/format';
import { TEMPLATE_NAME } from '@/lib/labels';
import { edRole, shortName } from '@/lib/owners';
import { linkProps } from '@/lib/router';
import { cn } from '@/lib/utils';
import type { Takeover as TakeoverData, TakeoverRow } from '@/lib/types';

/** Where the handoff for your role stands, in the words Take over uses. */
function Handoff({ r }: { r: TakeoverRow }) {
  return (
    <div className="space-y-1">
      <StateBadge state={r.state} />
      <div className="text-xs text-muted-foreground">
        {r.state === 'draft' && (r.blocking ? `${r.blocking} item${r.blocking === 1 ? '' : 's'} to resolve before signing` : 'Ready to sign')}
        {(r.state === 'signed' || r.state === 'changed') && r.signed && `Signed by ${shortName(r.signed.by)} at ${clock(r.signed.at)}`}
        {r.state === 'acknowledged' && r.acknowledged && `Accepted by ${shortName(r.acknowledged.by)}`}
      </div>
    </div>
  );
}

function PatientCell({ r }: { r: TakeoverRow }) {
  const d = r.destination;
  return (
    <div className="min-w-0 leading-tight">
      <div className="flex flex-wrap items-center gap-2">
        <a {...linkProps(`/handoff/${r.csn}`)} className="font-medium hover:underline" onClick={(e) => e.stopPropagation()}>
          {r.patient.name}
        </a>
        <SeverityBadge value={r.severity} />
      </div>
      <div className="mt-0.5 text-xs text-muted-foreground tabular-nums">
        {r.patient.age} {r.patient.sex} · MRN {r.patient.mrn} · {r.chief_complaint}
      </div>
      <div className="mt-0.5 text-xs text-muted-foreground">
        {d.pending_unit ? `To ${r.facility} ${bed(d.pending_unit, d.pending_room, d.pending_bed)}` : `${r.facility}, bed pending`} · boarding {duration(r.minutes_boarding)}
      </div>
    </div>
  );
}

function Confirm({ items }: { items: string[] }) {
  if (!items.length) return <span className="text-xs text-muted-foreground">All confirmed</span>;
  return (
    <div className="flex flex-wrap gap-1">
      {items.map((x) => (
        <Pill key={x} tone="warn">{x}</Pill>
      ))}
    </div>
  );
}

function Changed({ items }: { items: string[] }) {
  if (!items.length) return <span className="text-xs text-muted-foreground">Nothing</span>;
  return <span className="text-sm">{items.join(', ')}</span>;
}

export default function Takeover() {
  const { apiAt, at, past, persona, version, bump } = useApp();
  const role = edRole(persona);
  const [colleague, setColleague] = useState<string | null>(null);
  useEffect(() => setColleague(null), [persona.id]);
  const { data, error, setData, retry } = useLoad<TakeoverData | null>(
    () => (role ? api.takeover(colleague, apiAt) : Promise.resolve(null)),
    [at, persona.id, colleague, version],
  );
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<{ from: string; names: string[] } | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  // Every patient of the colleague is ticked at first: most handovers take them all.
  const rowsKey = (data?.rows ?? []).map((r) => r.csn).join(',');
  useEffect(() => setPicked(new Set(rowsKey ? rowsKey.split(',') : [])), [rowsKey]);
  useEffect(() => {
    setDone(null);
    setProblem(null);
  }, [colleague, persona.id]);
  const chosen = data?.colleague ?? null;
  const rows = useMemo(() => data?.rows ?? [], [data]);
  const locked = past !== null;
  const title = 'Take over';

  if (!role) {
    return (
      <Layout title={title} crumbs="Shift change" help="takeover">
        <EmptyState title="Take over is for ED nurses and physicians">
          At a shift change, the ED nurse or physician coming on takes the patients of the one going off. Your role does not own patients.
        </EmptyState>
      </Layout>
    );
  }
  if (error && !data) return <Layout title={title} crumbs="Shift change" help="takeover"><ErrorState message={error} onRetry={retry} /></Layout>;
  if (!data) return <Layout title={title} crumbs="Shift change" help="takeover"><PageSkeleton /></Layout>;

  const confirm = async () => {
    if (past || !chosen) return; // read only; the server refuses it too
    const csns = rows.filter((r) => picked.has(r.csn)).map((r) => r.csn);
    setBusy(true);
    setProblem(null);
    try {
      const next = await api.takeOver(chosen.id, csns, apiAt);
      setDone({ from: chosen.name, names: rows.filter((r) => next.taken?.includes(r.csn)).map((r) => r.patient.name) });
      setData(next);
      bump();
    } catch (e) {
      setProblem(e instanceof ApiError ? e.message : 'Something went wrong.');
    } finally {
      setBusy(false);
    }
  };
  const n = rows.filter((r) => picked.has(r.csn)).length;
  const tpl = TEMPLATE_NAME[data.template] ?? data.template;

  return (
    <Layout title={title} crumbs={`Shift change · ${persona.name}`} help="takeover">
      <div className="max-w-5xl space-y-5">
        <p className="text-sm text-muted-foreground">
          Coming on shift? Pick the colleague you are relieving, check their patients, then take them in one go. Signatures stay with whoever signed; a handoff that changed after signing still needs signing again.
        </p>
        {locked && (
          <p className="flex items-start gap-2 rounded-lg border bg-muted/50 px-4 py-2.5 text-sm">
            <Lock className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
            {READ_ONLY}
          </p>
        )}

        <Card className="gap-3 p-4 sm:p-5">
          <div className="flex items-center gap-1">
            <h2 className="text-sm font-semibold">1. Who are you relieving?</h2>
            <HelpButton topic="takeover.pick" label="About choosing a colleague" className="size-6" />
          </div>
          {data.colleagues.length === 0 ? (
            <p className="text-sm text-muted-foreground">No one else has your role in this app.</p>
          ) : (
            <div role="radiogroup" aria-label="Colleague you are relieving" className="flex flex-wrap gap-2">
              {data.colleagues.map((c) => (
                <button
                  key={c.id}
                  type="button"
                  role="radio"
                  aria-checked={chosen?.id === c.id}
                  onClick={() => setColleague(c.id)}
                  className={cn(
                    'flex min-w-48 items-center justify-between gap-3 rounded-lg border px-3 py-2 text-left text-sm outline-none transition-colors focus-visible:ring-[3px] focus-visible:ring-ring/50',
                    chosen?.id === c.id ? 'border-primary bg-primary/5' : 'hover:bg-accent',
                  )}
                >
                  <span className="font-medium">{c.name}</span>
                  <span className="text-xs text-muted-foreground tabular-nums">
                    {c.patients} patient{c.patients === 1 ? '' : 's'}
                  </span>
                </button>
              ))}
            </div>
          )}
        </Card>

        {done && (
          <div role="status" className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-sev-stable/30 bg-sev-stable/10 px-4 py-2.5 text-sm text-sev-stable">
            <Check className="size-4" aria-hidden />
            <span className="font-medium">
              You took over {done.names.length} patient{done.names.length === 1 ? '' : 's'} from {done.from}: {done.names.join(', ')}.
            </span>
            <a {...linkProps('/')} className="font-medium underline underline-offset-4">They are in My patients on the board</a>
          </div>
        )}

        {chosen && (
          <Card className="overflow-hidden">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b p-4 sm:px-5">
              <div className="flex items-center gap-1">
                <h2 className="text-sm font-semibold">2. {shortName(chosen.name)}'s patients</h2>
                <HelpButton topic="takeover.list" label="About this list" className="size-6" />
              </div>
              <p className="text-xs text-muted-foreground">Handoff shown: {tpl}, your role's template</p>
            </div>
            {rows.length === 0 ? (
              <EmptyState title={`${shortName(chosen.name)} has no patients to hand over`}>
                Only boarding patients they own now are listed. Unassigned patients are on the board, under My patients.
              </EmptyState>
            ) : (
              <>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead className="bg-muted/40 text-left text-xs font-medium text-muted-foreground">
                      <tr>
                        <th scope="col" className="w-10 py-2.5 pl-4 pr-1 sm:pl-5">
                          <input
                            type="checkbox"
                            aria-label="Take every patient"
                            className="size-4 accent-primary"
                            checked={n === rows.length}
                            onChange={(e) => setPicked(new Set(e.target.checked ? rows.map((r) => r.csn) : []))}
                          />
                        </th>
                        <th scope="col" className="px-3 py-2.5 font-medium">Patient</th>
                        <th scope="col" className="px-3 py-2.5 font-medium">Handoff</th>
                        <th scope="col" className="px-3 py-2.5 font-medium">Still to confirm</th>
                        <th scope="col" className="px-3 py-2.5 pr-4 font-medium sm:pr-5">Changed since signing</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y">
                      {rows.map((r) => (
                        <tr key={r.csn} className="align-top">
                          <td className="py-3 pl-4 pr-1 sm:pl-5">
                            <input
                              type="checkbox"
                              aria-label={`Take ${r.patient.name}`}
                              className="mt-0.5 size-4 accent-primary"
                              checked={picked.has(r.csn)}
                              onChange={(e) => {
                                const next = new Set(picked);
                                if (e.target.checked) next.add(r.csn);
                                else next.delete(r.csn);
                                setPicked(next);
                              }}
                            />
                          </td>
                          <td className="px-3 py-3"><PatientCell r={r} /></td>
                          <td className="px-3 py-3"><Handoff r={r} /></td>
                          <td className="px-3 py-3"><Confirm items={r.to_confirm} /></td>
                          <td className="px-3 py-3 pr-4 sm:pr-5"><Changed items={r.changed} /></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="flex flex-col gap-2 border-t p-4 sm:flex-row sm:items-center sm:justify-between sm:px-5">
                  <p className="text-xs text-muted-foreground">
                    {problem ? <span role="alert" className="text-destructive">{problem}</span> : `${n} of ${rows.length} chosen. Each becomes yours from now on.`}
                  </p>
                  <div className="flex items-center gap-1">
                    <Button onClick={confirm} disabled={locked || busy || n === 0}>
                      <Handshake aria-hidden />
                      Take {n} patient{n === 1 ? '' : 's'}
                    </Button>
                    <HelpButton topic="takeover.confirm" label="What taking over does" className="size-6" />
                  </div>
                </div>
              </>
            )}
          </Card>
        )}
      </div>
    </Layout>
  );
}
