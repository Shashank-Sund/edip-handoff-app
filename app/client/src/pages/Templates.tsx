import { Cog, Database, Sparkles } from 'lucide-react';
import { Card } from '@/components/ui/card';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Layout } from '@/components/layout/Layout';
import { Pill } from '@/components/common/badges';
import { ErrorState, PageSkeleton } from '@/components/common/states';
import { useLoad } from '@/hooks/useLoad';
import { api } from '@/lib/api';
import type { TemplateDef } from '@/lib/types';

type Def = TemplateDef & { elements: Record<string, { label: string; kind: string; help?: string }> };
const KIND = {
  deterministic: { label: 'From the chart', Icon: Database },
  narrative: { label: 'Model drafted', Icon: Sparkles },
  process: { label: 'In the app', Icon: Cog },
} as const;
const LEVEL: Record<string, { label: string; tone: 'danger' | 'warn' | 'neutral' | 'info' }> = {
  hard: { label: 'Blocks sign off', tone: 'danger' },
  stale: { label: 'Must be recent', tone: 'warn' },
  soft: { label: 'Warning', tone: 'neutral' },
};

function TemplateView({ t }: { t: Def }) {
  const rules = new Map(t.rules.map((r) => [r.element, r]));
  return (
    <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_320px]">
      <div className="space-y-4">
        {t.sections.map((s) => (
          <Card key={s.id} className="overflow-hidden">
            <div className="flex items-center gap-2.5 border-b bg-muted/30 px-4 py-2.5 sm:px-5">
              <span className="grid size-6 place-items-center rounded-md bg-primary/10 text-[11px] font-bold text-primary">{s.id}</span>
              <h3 className="text-sm font-semibold">{s.label}</h3>
            </div>
            <ul className="divide-y">
              {s.elements.map((id) => {
                const e = t.elements[id];
                const k = KIND[e.kind as keyof typeof KIND] ?? KIND.deterministic;
                const r = rules.get(id);
                return (
                  <li key={id} className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center sm:justify-between sm:px-5">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-medium">{e.label}</span>
                        <span className="font-mono text-[11px] text-muted-foreground">{id}</span>
                      </div>
                      {e.help && <p className="text-xs text-muted-foreground">{e.help}</p>}
                    </div>
                    <div className="flex shrink-0 flex-wrap items-center gap-1.5">
                      <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
                        <k.Icon className="size-3.5" aria-hidden />
                        {k.label}
                      </span>
                      {r && (
                        <Pill tone={r.at ? 'info' : LEVEL[r.level]?.tone ?? 'neutral'}>
                          {r.at ? `Checked at ${r.at}` : LEVEL[r.level]?.label ?? r.level}
                          {r.max_age_min ? ` (${Object.entries(r.max_age_min).map(([s2, m]) => `${s2} ${m} min`).join(', ')})` : ''}
                        </Pill>
                      )}
                    </div>
                  </li>
                );
              })}
            </ul>
          </Card>
        ))}
      </div>
      <aside className="space-y-4 lg:sticky lg:top-20 lg:self-start">
        <Card className="space-y-3 p-5 text-sm">
          <h3 className="font-semibold">{t.name}</h3>
          {t.description && <p className="text-muted-foreground">{t.description}</p>}
          <dl className="grid grid-cols-3 gap-2 text-center">
            {(['deterministic', 'narrative', 'process'] as const).map((kind) => (
              <div key={kind} className="rounded-lg bg-muted/50 px-2 py-2">
                <dd className="text-lg font-semibold tabular-nums">{Object.values(t.sections).flatMap((s) => s.elements).filter((e) => t.elements[e]?.kind === kind).length}</dd>
                <dt className="text-[11px] text-muted-foreground">{KIND[kind].label}</dt>
              </div>
            ))}
          </dl>
        </Card>
        <Card className="space-y-2 p-5 text-sm">
          <h3 className="font-semibold">Adapt it for your hospital</h3>
          <p className="text-muted-foreground">
            Templates are YAML in <code className="rounded bg-muted px-1 py-0.5 text-xs">ai/templates</code>. Copy the custom template, rename sections to local labels, move elements and set each rule. A site whose feeds cannot supply an element downgrades its rule from hard to soft, with a note.
          </p>
        </Card>
      </aside>
    </div>
  );
}

export default function Templates() {
  const { data, error } = useLoad(() => api.templates() as Promise<Def[]>, []);
  if (error && !data) return <Layout title="Templates"><ErrorState message={error} /></Layout>;
  if (!data) return <Layout title="Templates"><PageSkeleton /></Layout>;
  return (
    <Layout title="Templates" crumbs="31 shared elements, three templates" help="templates">
      <Tabs defaultValue={data[0].id}>
        <TabsList aria-label="Template">
          {data.map((t) => (
            <TabsTrigger key={t.id} value={t.id}>
              {t.name}
            </TabsTrigger>
          ))}
        </TabsList>
        {data.map((t) => (
          <TabsContent key={t.id} value={t.id} className="mt-4">
            <TemplateView t={t} />
          </TabsContent>
        ))}
      </Tabs>
    </Layout>
  );
}
