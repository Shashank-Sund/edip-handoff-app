import { Activity, BedDouble, Bot, FileCheck2 } from 'lucide-react';
import { Bar, BarChart, CartesianGrid, XAxis, YAxis } from 'recharts';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { ChartContainer, ChartLegend, ChartTooltip, ChartTooltipContent, type ChartConfig } from '@/components/ui/chart';
import { Layout } from '@/components/layout/Layout';
import { Stat } from '@/components/common/Stat';
import { ErrorState, PageSkeleton } from '@/components/common/states';
import { TimeChart } from '@/components/charts/TimeChart';
import { HelpButton } from '@/components/guide/HelpButton';
import { useApp } from '@/hooks/useApp';
import { useLoad } from '@/hooks/useLoad';
import { api } from '@/lib/api';
import { dateTime, duration, pct, span } from '@/lib/format';
import { ROLE_OWNER, shortName } from '@/lib/owners';
import type { Analytics as AnalyticsData } from '@/lib/types';

const unitConfig: ChartConfig = {
  median_min: { label: 'Median', color: 'var(--series-1)' },
  p90_min: { label: '90th percentile', color: 'var(--series-1)' },
};
const histConfig: ChartConfig = { n: { label: 'Handoffs', color: 'var(--series-3)' } };

const ROLE_PLURAL = { ed_nurse: 'ED nurses', ed_physician: 'ED physicians' } as const;

/** Owners: unassigned boarding patients by hour, actions outside an assignment, handovers around the shift change,
 *  and admit decision to signature by owner. */
function OwnerPanels({ data }: { data: AnalyticsData }) {
  const o = data.owners;
  if (!o) return null;
  const maxHand = Math.max(1, ...o.handovers.map((h) => h.n));
  const handed = o.handovers.reduce((n, h) => n + h.n, 0);
  return (
    <>
      <Panel help="analytics.unassigned" title="Unassigned boarding patients" description="Patients boarding with no nurse owner, and with no physician owner, hourly. A stale owner counts as no owner.">
        <TimeChart
          ariaLabel="Unassigned boarding patients by hour"
          height={240}
          lines={[
            { key: 'boarding', label: 'Boarding', color: 'var(--series-2)', data: data.series.map((p) => ({ t: p.t, v: p.boarding })), points: false, dashed: true, format: (v) => String(Math.round(v)) },
            { key: 'rn', label: 'No nurse owner', color: 'var(--series-3)', data: data.series.map((p) => ({ t: p.t, v: p.unassigned_rn ?? null })), points: false, format: (v) => String(Math.round(v)) },
            { key: 'md', label: 'No physician owner', color: 'var(--series-4)', data: data.series.map((p) => ({ t: p.t, v: p.unassigned_md ?? null })), points: false, format: (v) => String(Math.round(v)) },
          ]}
        />
      </Panel>
      <div className="grid gap-5 lg:grid-cols-3">
        <Panel help="analytics.outside" title="Actions outside an assignment" description="Edits, attestations and signatures on a patient a colleague in the same role owned.">
          <dl className="grid grid-cols-2 gap-3 pt-2">
            {o.outside.map((x) => (
              <div key={x.role} className="rounded-lg border px-3 py-3">
                <dt className="text-xs text-muted-foreground">{ROLE_PLURAL[x.role]}</dt>
                <dd className="mt-1 text-2xl font-semibold tabular-nums">{x.n}</dd>
                <dd className="text-xs text-muted-foreground tabular-nums">{x.of ? `${pct(x.n / x.of)} of ${x.of} actions` : 'No actions yet'}</dd>
              </div>
            ))}
          </dl>
        </Panel>
        <Panel help="analytics.handovers" title="Handovers around the shift change" description="Patients taken over, by how far from the nearest shift change.">
          {handed ? (
            <ul className="space-y-3 pt-2">
              {o.handovers.map((h) => (
                <li key={h.bucket} className="grid grid-cols-[9.5rem_1fr_2rem] items-center gap-3 text-sm">
                  <span className="text-muted-foreground">{h.bucket}</span>
                  <span className="h-2.5 rounded-full bg-muted" aria-hidden>
                    <span className="block h-2.5 rounded-full bg-[var(--series-3)]" style={{ width: `${(h.n / maxHand) * 100}%` }} />
                  </span>
                  <span className="text-right font-medium tabular-nums">{h.n}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="py-10 text-center text-sm text-muted-foreground">No handovers yet.</p>
          )}
        </Panel>
        <Panel help="analytics.to-sign" title="Admit decision to signature, by owner" description="Median minutes to the first signature of the role's handoff, by who owned the patient then.">
          {o.to_sign.length ? (
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-muted-foreground">
                <tr>
                  <th scope="col" className="pb-2 font-medium">Owner</th>
                  <th scope="col" className="pb-2 text-right font-medium">Signed</th>
                  <th scope="col" className="pb-2 text-right font-medium">Median</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {o.to_sign.map((r) => (
                  <tr key={`${r.role}|${r.owner}`}>
                    <td className="py-1.5">
                      <div className={r.owner ? 'font-medium' : 'italic text-muted-foreground'}>{r.owner ? shortName(r.owner) : 'Unassigned'}</div>
                      <div className="text-xs text-muted-foreground">{ROLE_OWNER[r.role]}</div>
                    </td>
                    <td className="py-1.5 text-right tabular-nums">{r.n}</td>
                    <td className="py-1.5 text-right tabular-nums">{duration(r.median_min)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="py-10 text-center text-sm text-muted-foreground">No handoffs signed yet.</p>
          )}
        </Panel>
      </div>
    </>
  );
}

function Panel({ title, description, children, className, help }: { title: string; description?: string; children: React.ReactNode; className?: string; help?: string }) {
  return (
    <Card className={className}>
      <CardHeader className="pb-2 sm:pb-2">
        <CardTitle className="flex items-center justify-between gap-2 text-sm">
          {title}
          {help && <HelpButton topic={help} label="How to read this chart" className="-my-1" />}
        </CardTitle>
        {description && <CardDescription className="text-xs">{description}</CardDescription>}
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}

export default function Analytics() {
  const { at, apiAt, version } = useApp();
  const { data, error, retry } = useLoad(() => api.analytics(apiAt), [at, version]);
  const title = 'Analytics';
  if (error && !data) return <Layout title={title}><ErrorState message={error} onRetry={retry} /></Layout>;
  if (!data) return <Layout title={title}><PageSkeleton /></Layout>;
  const sev = data.severity_at_transfer;
  const units = data.units.filter((u) => u.n > 0);
  const total = Object.values(sev).reduce((a, b) => a + b, 0);
  const peak = data.series.reduce((m, p) => (p.boarding > m.boarding ? p : m), data.series[0] ?? { boarding: 0, t: at, ed: 0 });
  return (
    <Layout title={title} crumbs={`Both hospitals, all units: the reference week through ${dateTime(at)}`} help="analytics">
      <div className="space-y-5 sm:space-y-6">
        <section aria-label="Summary" className="grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-4">
          <Stat
            label="Admitted from the ED"
            value={data.admitted}
            icon={<BedDouble />}
            hint={data.admitted ? `${data.signed_before_transfer} signed before transfer, ${data.transferred_without_sign_off} transferred without sign off` : 'Transferred to an inpatient or observation bed'}
          />
          <Stat label="Peak boarding" value={peak.boarding} icon={<Activity />} hint={peak.boarding ? `at ${dateTime(peak.t)}` : 'No boarding yet'} />
          <Stat label="Median completeness" value={pct(data.completeness_at_transfer.median)} icon={<FileCheck2 />} hint="Model draft at transfer, before clinician edits" />
          <Stat label="Model calls" value={data.model_calls} icon={<Bot />} hint={`${data.drafts} drafts across 3 templates; ${data.addenda} addenda after transfer`} />
        </section>

        <Panel help="analytics.census" title="ED census and boarding" description="Patients in the ED and patients boarding (admit decision made, still in the ED), hourly.">
          <TimeChart
            ariaLabel="ED census and boarding by hour"
            height={300}
            lines={[
              { key: 'ed', label: 'In the ED', color: 'var(--series-1)', area: true, data: data.series.map((p) => ({ t: p.t, v: p.ed })), points: false, format: (v) => String(Math.round(v)) },
              { key: 'boarding', label: 'Boarding', color: 'var(--series-2)', data: data.series.map((p) => ({ t: p.t, v: p.boarding })), points: false, format: (v) => String(Math.round(v)) },
            ]}
          />
          <p className="mt-3 text-xs text-muted-foreground">
            {data.series.length
              ? `Over ${span(data.series.length)} the ED held up to ${Math.max(...data.series.map((p) => p.ed))} patients at once, and up to ${peak.boarding} were boarding.`
              : 'The week has not started yet.'}
          </p>
        </Panel>

        <OwnerPanels data={data} />

        <div className="grid gap-5 lg:grid-cols-2">
          <Panel help="analytics.units" title="Boarding time by destination" description="Admit decision to leaving the ED, patients transferred so far.">
            {units.length ? (
              <>
                <ChartContainer config={unitConfig} className="h-64">
                  <BarChart data={units} margin={{ left: 4, right: 8, top: 8 }} barGap={2}>
                    <CartesianGrid vertical={false} />
                    <XAxis dataKey="unit" tickLine={false} axisLine={false} interval={0} tick={{ fontSize: 11 }} />
                    <YAxis tickLine={false} axisLine={false} width={44} tickFormatter={(v: number) => `${Math.round(v / 60)} h`} />
                    <ChartTooltip cursor content={<ChartTooltipContent formatter={(v) => duration(v)} labelFormatter={(l, p) => `${l} (${(p[0]?.payload as { n: number })?.n ?? 0} patients)`} />} />
                    <Bar dataKey="median_min" fill="var(--color-median_min)" radius={[4, 4, 0, 0]} maxBarSize={22} />
                    <Bar dataKey="p90_min" fill="var(--color-p90_min)" fillOpacity={0.35} radius={[4, 4, 0, 0]} maxBarSize={22} />
                  </BarChart>
                </ChartContainer>
                <ChartLegend keys={['median_min', 'p90_min']} config={unitConfig} className="mt-2" />
              </>
            ) : (
              <p className="py-10 text-center text-sm text-muted-foreground">No transfers yet.</p>
            )}
          </Panel>

          <Panel help="analytics.blocked" title="What blocked sign off at transfer" description="Required items missing from the model draft, so the clinician had to attest or write them.">
            {data.blocking_at_transfer.length ? (
              <ul className="space-y-3 pt-2">
                {data.blocking_at_transfer.map((b) => {
                  const max = data.blocking_at_transfer[0].n || 1;
                  return (
                    <li key={b.label} className="grid grid-cols-[9rem_1fr_2.5rem] items-center gap-3 text-sm">
                      <span className="truncate text-muted-foreground" title={b.label}>
                        {b.label}
                      </span>
                      <span className="h-2.5 rounded-full bg-muted" aria-hidden>
                        <span className="block h-2.5 rounded-full bg-[var(--series-4)]" style={{ width: `${(b.n / max) * 100}%` }} />
                      </span>
                      <span className="text-right font-medium tabular-nums">{b.n}</span>
                    </li>
                  );
                })}
                <li className="pt-1 text-xs text-muted-foreground">
                  Out of {data.admitted} transfers. Most are documentation gaps the clinician closes with one tap (for example "No known allergies confirmed"); stale vitals need a recheck.
                </li>
              </ul>
            ) : (
              <p className="py-10 text-center text-sm text-muted-foreground">No transfers yet.</p>
            )}
          </Panel>

          <Panel help="analytics.quality" title="Completeness at transfer" description="Share of the template's rules the model draft met on its own.">
            <ChartContainer config={histConfig} className="h-56">
              <BarChart data={data.completeness_at_transfer.histogram} margin={{ left: 4, right: 8, top: 8 }}>
                <CartesianGrid vertical={false} />
                <XAxis dataKey="bin" tickLine={false} axisLine={false} interval={0} tick={{ fontSize: 11 }} />
                <YAxis tickLine={false} axisLine={false} width={32} allowDecimals={false} />
                <ChartTooltip cursor content={<ChartTooltipContent hideIndicator />} />
                <Bar dataKey="n" fill="var(--color-n)" radius={[4, 4, 0, 0]} maxBarSize={36} />
              </BarChart>
            </ChartContainer>
          </Panel>

          <Panel help="analytics.quality" title="Illness severity at transfer" description="Final draft severity: rule floor raised by the model when it judged higher.">
            {total ? (
              <div className="space-y-5 pt-3">
                <div className="flex h-4 overflow-hidden rounded-full bg-muted" role="img" aria-label={(['stable', 'watcher', 'unstable'] as const).map((k) => `${k} ${sev[k] ?? 0}`).join(', ')}>
                  {(['stable', 'watcher', 'unstable'] as const).map((k) => (
                    <div key={k} className="h-full border-r-2 border-card last:border-r-0" style={{ width: `${((sev[k] ?? 0) / total) * 100}%`, background: `var(--sev-${k})` }} />
                  ))}
                </div>
                <dl className="grid grid-cols-3 gap-3">
                  {(['stable', 'watcher', 'unstable'] as const).map((k) => (
                    <div key={k} className="rounded-lg border px-3 py-3">
                      <dt className="flex items-center gap-1.5 text-xs text-muted-foreground">
                        <span className="size-2 rounded-full" style={{ background: `var(--sev-${k})` }} aria-hidden />
                        {k[0].toUpperCase() + k.slice(1)}
                      </dt>
                      <dd className="mt-1 text-2xl font-semibold tabular-nums">{sev[k] ?? 0}</dd>
                      <dd className="text-xs text-muted-foreground tabular-nums">{pct((sev[k] ?? 0) / total)} of transfers</dd>
                    </div>
                  ))}
                </dl>
                <p className="text-xs text-muted-foreground">Severity is never lower than the rule floor (critical results, ICU level, shock range vitals, ESI 1 or 2); the model can only raise it.</p>
              </div>
            ) : (
              <p className="py-10 text-center text-sm text-muted-foreground">No transfers yet.</p>
            )}
          </Panel>
        </div>
      </div>
    </Layout>
  );
}
