import { useEffect } from 'react';
import { ArrowDown, ArrowRight } from 'lucide-react';
import { Card } from '@/components/ui/card';
import { Layout } from '@/components/layout/Layout';
import { GUIDE } from '@/components/guide/content';
import { useApp } from '@/hooks/useApp';

const FLOW = [
  { title: 'Hospital systems', body: 'ADT, results, orders and notes (HL7 v2), FHIR events, and a nightly copy of the EHR database.' },
  { title: 'Data pipeline', body: 'Reads every feed, removes duplicates, and keeps each patient\'s current status.' },
  { title: 'Drafts', body: 'At each admit decision or important change, the model writes the narrative; rules check it.' },
  { title: 'App database', body: 'Holds the drafts for the app, and every edit, attestation, signature and acceptance.' },
  { title: 'This app', body: 'Board, handoff review, sign off, read back and analytics.' },
];

function Behind() {
  const { config } = useApp();
  return (
    <section id="behind" className="scroll-mt-20 space-y-4">
      <div>
        <h2 className="text-lg font-semibold tracking-tight">Behind the scenes</h2>
        <p className="text-sm text-muted-foreground">For the curious, and for informatics teams.</p>
      </div>
      <Card className="p-5">
        <ol className="grid gap-3 md:grid-cols-5">
          {FLOW.map((f, i) => (
            <li key={f.title} className="relative rounded-lg border bg-muted/30 p-4">
              <div className="text-xs font-medium text-primary">Step {i + 1}</div>
              <div className="mt-0.5 font-semibold">{f.title}</div>
              <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{f.body}</p>
              {i < FLOW.length - 1 && (
                <>
                  <ArrowRight className="absolute -right-3 top-1/2 hidden size-4 -translate-y-1/2 text-muted-foreground md:block" aria-hidden />
                  <ArrowDown className="absolute -bottom-3 left-1/2 size-4 -translate-x-1/2 text-muted-foreground md:hidden" aria-hidden />
                </>
              )}
            </li>
          ))}
        </ol>
      </Card>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card className="space-y-2 p-5 text-sm leading-relaxed">
          <h3 className="font-semibold">What the model may and may not write</h3>
          <p>Allergies, code status, isolation, vital signs, labs, medications, pending results and destination are copied from the chart, so the model cannot get them wrong. The model writes only the narrative, and every statement must name the chart records it used. Before anyone sees a draft, a checker removes references to records that do not exist and highlights numbers it cannot find in the cited records.</p>
        </Card>
        <Card className="space-y-2 p-5 text-sm leading-relaxed">
          <h3 className="font-semibold">About this demo</h3>
          <p>About 700 made up ED visits over one week at two invented hospitals, Shai General Hospital (SGH) and Lakeside Community Hospital (LKS). Drafts: {config.drafts_from}{config.models.length ? ` (${config.models.join(', ')})` : ''}.</p>
          <p className="text-muted-foreground">
            Charts by{' '}
            <a href="https://www.tradingview.com/" className="font-medium text-primary hover:underline" target="_blank" rel="noreferrer">
              TradingView
            </a>{' '}
            Lightweight Charts and Recharts; components adapted from shadcn/ui on Radix UI; icons from Lucide. Templates draw on Joint Commission Sentinel Event Alert 58, IHI SBAR, I-PASS and its ED adaptation, SBAR-DR and PREP-4Cs.
          </p>
        </Card>
      </div>
    </section>
  );
}

export default function Guide() {
  useEffect(() => {
    const id = window.location.hash.slice(1);
    if (id) window.setTimeout(() => document.getElementById(id)?.scrollIntoView({ block: 'start' }), 50);
  }, []);
  return (
    <Layout title="How to use" crumbs="A guide for nurses and physicians">
      <div className="grid gap-8 lg:grid-cols-[220px_minmax(0,1fr)]">
        <nav aria-label="Guide contents" className="hidden lg:block">
          <ul className="sticky top-20 space-y-1 text-sm">
            {GUIDE.map((s) => (
              <li key={s.id}>
                <a href={`#${s.id}`} className="block rounded-md px-2.5 py-1.5 text-muted-foreground hover:bg-accent hover:text-foreground">
                  {s.title}
                </a>
              </li>
            ))}
            <li>
              <a href="#behind" className="block rounded-md px-2.5 py-1.5 text-muted-foreground hover:bg-accent hover:text-foreground">
                Behind the scenes
              </a>
            </li>
          </ul>
        </nav>
        <div className="min-w-0 max-w-4xl space-y-10">
          {GUIDE.map((s) => (
            <section key={s.id} id={s.id} className="scroll-mt-20 space-y-4">
              <div>
                <h2 className="text-lg font-semibold tracking-tight">{s.title}</h2>
                <p className="text-sm text-muted-foreground">{s.summary}</p>
              </div>
              <div className="space-y-4">
                {s.topics.map((t) => (
                  <Card key={t.id} id={`${s.id}-${t.id}`} className="scroll-mt-20 space-y-3 p-5 text-sm leading-relaxed">
                    <h3 className="font-semibold">{t.title}</h3>
                    {t.body}
                  </Card>
                ))}
              </div>
            </section>
          ))}
          <Behind />
        </div>
      </div>
    </Layout>
  );
}
